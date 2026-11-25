"""Question -> retrieve context -> generate SQL -> guard -> execute -> self-correct."""
import time
from dataclasses import dataclass, field
from datetime import date

import pandas as pd
import psycopg
from psycopg import sql as pgsql

import config
from core.executor import run_query
from core.llm import Gemini, LLMError
from core.sql_guard import UnsafeSQL, check_sql
from rag.retriever import Retrieved, Retriever

# How much context the model gets. The evaluation compares these.
MODES = {
    "rag": "RAG: full schema + retrieved business rules & examples",
    "rag_tables": "RAG only: retrieved tables (+BFS join path) + rules & examples",
    "schema_only": "Baseline: full schema, no business rules or examples",
}

SYSTEM_PROMPT = """You are a senior data analyst who writes PostgreSQL 17 for an online retail store.
Convert the user's question into ONE read-only SELECT query.

Rules:
- Use only the tables and columns listed in the schema. Never invent columns.
- Follow the business definitions exactly when they apply (e.g. revenue excludes cancelled and returned orders).
- Use the exact category/status/value spellings shown in the schema "Values" lists.
- Use explicit JOINs with short table aliases. Qualify every column with its alias.
- For relative dates ("last month", "this year") use CURRENT_DATE. Today is {today}.
- Round money and percentages with ROUND(..., 2). Give columns readable snake_case aliases.
- For "top N" questions use ORDER BY ... LIMIT N. Otherwise return at most 1000 rows.
- Only SELECT/WITH. Never write INSERT, UPDATE, DELETE, DDL, or call pg_* functions.
- If the question cannot be answered from this database, return an empty sql and say why in explanation."""

EMPTY_SYSTEM_PROMPT = """You are a senior data analyst reviewing a PostgreSQL query that ran without
errors but returned ZERO rows. Decide which case applies:

1. Genuinely empty: the data really has nothing matching (e.g. "customers from Japan" when no
   customer lives in Japan). Say so plainly and mention what DOES exist, if useful.
2. Too strict or wrong: a date outside the range the data covers, a value spelled differently
   from the real values, contradictory filters, a wrong join, etc. Explain the cause and write
   a corrected or relaxed SELECT that answers the closest sensible version of the question.

Use the data facts and the schema "Values" lists as evidence. Today is {today}.
Suggested SQL rules: PostgreSQL 17, one SELECT/WITH query, only the listed tables and columns,
exact value spellings, never pg_* functions."""


@dataclass
class Alternative:
    """A follow-up query proposed by the model when the original returned no rows."""
    label: str
    sql: str
    df: pd.DataFrame
    truncated: bool


@dataclass
class Attempt:
    sql: str
    error: str | None
    model: str
    seconds: float


@dataclass
class AskResult:
    question: str
    sql: str = ""
    explanation: str = ""
    assumptions: list[str] = field(default_factory=list)
    df: pd.DataFrame | None = None
    truncated: bool = False
    error: str | None = None
    attempts: list[Attempt] = field(default_factory=list)
    retrieved: Retrieved | None = None
    context_tables: list[str] = field(default_factory=list)
    mode: str = "rag"
    total_seconds: float = 0.0
    # Filled only when the query returned zero rows:
    empty_note: str | None = None
    empty_is_correct: bool | None = None
    alternative: Alternative | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.df is not None


class Pipeline:
    def __init__(self):
        self.llm = Gemini()
        self.retriever = Retriever(self.llm)
        self.data_facts = self._load_data_facts()

    @staticmethod
    def _load_data_facts() -> str:
        """Row counts and date ranges: the evidence the model needs to explain empty results."""
        lines = []
        with psycopg.connect(**config.pg_params("app")) as conn:
            cols = conn.execute(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = ANY(%s) "
                "AND data_type IN ('date', 'timestamp without time zone') "
                "ORDER BY table_name, ordinal_position", (config.STORE_TABLES,)).fetchall()
            for table, col in cols:
                lo, hi = conn.execute(pgsql.SQL("SELECT min({c})::date, max({c})::date FROM {t}").format(
                    c=pgsql.Identifier(col), t=pgsql.Identifier(table))).fetchone()
                lines.append(f"- {table}.{col} ranges from {lo} to {hi}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ prompt
    def build_prompt(self, question: str, mode: str, history: list[tuple[str, str]],
                     retrieved: Retrieved | None) -> tuple[str, list[str]]:
        sections = []
        if mode == "rag_tables" and retrieved:
            tables = [d.key for d, _ in retrieved.tables] + retrieved.join_tables
            schema = "\n\n".join(self.retriever.table_doc(t).content for t in tables)
        else:
            tables = list(config.STORE_TABLES)
            schema = self.retriever.all_tables_text()
        sections.append("### Database schema\n" + schema)

        if mode in ("rag", "rag_tables") and retrieved:
            sections.append("### Business definitions (follow these)\n"
                            + "\n".join(f"- {d.content}" for d, _ in retrieved.glossary))
            sections.append("### Similar solved examples\n"
                            + "\n\n".join(d.content for d, _ in retrieved.examples))
        if history:
            turns = history[-config.HISTORY_TURNS:]
            sections.append("### Earlier in this conversation (the question may refer back to these)\n"
                            + "\n\n".join(f"Q: {q}\nSQL:\n{s}" for q, s in turns))
        sections.append(f"### Question\n{question}")
        return "\n\n".join(sections), tables

    # --------------------------------------------------------------------- ask
    def ask(self, question: str, model: str = config.DEFAULT_MODEL, mode: str = "rag",
            history: list[tuple[str, str]] | None = None,
            thinking: str | None = config.DEFAULT_THINKING,
            handle_empty: bool = True) -> AskResult:
        start = time.perf_counter()
        res = AskResult(question=question, mode=mode)
        try:
            # Embed the question with recent history so "and by country?" still
            # retrieves the right tables.
            if mode != "schema_only":
                query = " ".join([q for q, _ in (history or [])[-1:]] + [question])
                res.retrieved = self.retriever.retrieve(query)
            prompt, res.context_tables = self.build_prompt(question, mode, history or [], res.retrieved)
            system = SYSTEM_PROMPT.format(today=date.today().isoformat())

            for attempt_no in range(config.MAX_FIX_ATTEMPTS + 1):
                t0 = time.perf_counter()
                answer, used_model = self.llm.generate_sql(model, system, prompt, thinking)
                res.sql, res.explanation, res.assumptions = answer.sql.strip(), answer.explanation, answer.assumptions
                if not res.sql:
                    res.error = "The model says this question can't be answered from the database."
                    res.attempts.append(Attempt("", res.error, used_model, time.perf_counter() - t0))
                    break
                try:
                    clean = check_sql(res.sql)
                    result = run_query(clean)
                    res.sql, res.df, res.truncated, res.error = clean, result.df, result.truncated, None
                    res.attempts.append(Attempt(clean, None, used_model, time.perf_counter() - t0))
                    break
                except (UnsafeSQL, psycopg.Error) as e:
                    msg = _error_text(e)
                    res.error = msg
                    res.attempts.append(Attempt(res.sql, msg, used_model, time.perf_counter() - t0))
                    # Self-correction: show the model its own query and the error.
                    prompt += (f"\n\n### Your previous attempt failed\nSQL:\n{res.sql}\nError: {msg}\n"
                               "Return a corrected query.")
            if handle_empty and res.ok and res.df.empty:
                self._explain_empty(res, model, thinking)
        except LLMError as e:
            res.error = str(e)
        res.total_seconds = time.perf_counter() - start
        return res

    # -------------------------------------------------------- empty results
    def _explain_empty(self, res: AskResult, model: str, thinking: str | None) -> None:
        """The query ran but matched nothing. Ask the model why, and run its fix if it has one."""
        prompt = (f"### Question\n{res.question}\n\n### SQL that returned 0 rows\n{res.sql}\n\n"
                  f"### Data facts\n{self.data_facts}\n\n### Database schema\n"
                  f"{self.retriever.all_tables_text()}")
        try:
            answer, _ = self.llm.explain_empty(
                model, EMPTY_SYSTEM_PROMPT.format(today=date.today().isoformat()), prompt, thinking)
        except LLMError:
            res.empty_note = "No rows matched this query."
            return
        res.empty_note, res.empty_is_correct = answer.message, answer.empty_is_correct
        if answer.empty_is_correct or not answer.suggested_sql.strip():
            return
        try:  # the suggestion goes through the same guard and read-only role
            clean = check_sql(answer.suggested_sql)
            result = run_query(clean)
        except (UnsafeSQL, psycopg.Error):
            return
        if not result.df.empty:
            res.alternative = Alternative(answer.suggestion_label or "Closest answer", clean,
                                          result.df, result.truncated)


def _error_text(e: Exception) -> str:
    if isinstance(e, psycopg.Error):
        diag = getattr(e, "diag", None)
        msg = (diag.message_primary if diag and diag.message_primary else str(e)).strip()
        if diag and diag.message_hint:
            msg += f" (hint: {diag.message_hint})"
        return msg
    return str(e)
