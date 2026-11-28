"""Thin wrapper around the Gemini API (google-genai SDK).

Free-tier keys hit 429 (quota) and 503 (model busy) often. Every call has a time
limit; a busy model is retried once, an exhausted or silent one is skipped, and the
second model takes over.
"""
import json
import re
import time

from google import genai
from google.genai import errors, types
from pydantic import BaseModel, Field

import config

RETRYABLE = {429, 500, 503, 504}  # embeddings: worth waiting for, there is no second model
BRIEF = {500, 503}                # text: retry once, then move to the other model


class SQLAnswer(BaseModel):
    """The structured answer we ask the model to return."""
    sql: str = Field(description="One PostgreSQL SELECT query, or empty string if unanswerable.")
    explanation: str = Field(description="1-3 sentences in plain English describing what the query does.")
    assumptions: list[str] = Field(default_factory=list,
                                   description="Interpretations made where the question was ambiguous.")


class ClarifyingSQLAnswer(SQLAnswer):
    """Same answer, but the model may ask the user a question instead of guessing."""
    clarifying_question: str = Field(
        default="", description="Only if the question has two clearly different meanings: ask which one. "
                                "Otherwise an empty string.")
    options: list[str] = Field(default_factory=list,
                               description="2-4 short choices answering the clarifying question.")


class ResultExplanation(BaseModel):
    """The result of a query, retold for someone who does not know SQL."""
    summary: str = Field(description="2-4 short sentences in easy English saying what the result shows.")


class EmptyResultAnswer(BaseModel):
    """What the model says when a query ran fine but returned no rows."""
    empty_is_correct: bool = Field(
        description="True if 'nothing matches' is the genuine answer (e.g. no orders were cancelled "
                    "yesterday). False if the query is probably too strict or wrong.")
    message: str = Field(description="1-3 friendly sentences for the user explaining why there are no rows, "
                                     "citing the data facts (e.g. dates covered, values that exist).")
    suggested_sql: str = Field(
        default="", description="If empty_is_correct is false: a corrected or relaxed SELECT that answers "
                                "the closest sensible question. Otherwise an empty string.")
    suggestion_label: str = Field(
        default="", description="Short title for the suggested query, e.g. 'Same question for 2026 instead'.")


class LLMError(RuntimeError):
    pass


class Gemini:
    def __init__(self, api_key: str = config.GEMINI_API_KEY):
        if not api_key:
            raise LLMError("GEMINI_API_KEY is missing from .env")
        self.client = genai.Client(api_key=api_key,
                                   http_options=types.HttpOptions(
            timeout=config.LLM_TIMEOUT_MS,
            retry_options=types.HttpRetryOptions(attempts=1)))  # retries are handled here
        self._cooling: dict[str, float] = {}  # model -> time until which to skip it

    # ---------------------------------------------------------------- text-to-SQL
    def generate_sql(self, model: str, system: str, prompt: str,
                     thinking: str | None = config.DEFAULT_THINKING,
                     allow_clarify: bool = False) -> tuple[SQLAnswer, str]:
        """Return (answer, model_actually_used)."""
        shape = ClarifyingSQLAnswer if allow_clarify else SQLAnswer
        return self.structured(model, system, prompt, shape, thinking)

    def explain_result(self, system: str, prompt: str) -> str:
        """Always the small fast model: this is rewording, not writing SQL."""
        answer, _ = self.structured(config.FALLBACK_MODEL, system, prompt, ResultExplanation, "minimal")
        return answer.summary.strip()

    def explain_empty(self, model: str, system: str, prompt: str,
                      thinking: str | None = config.DEFAULT_THINKING) -> tuple["EmptyResultAnswer", str]:
        """Diagnose a query that returned no rows (see core/pipeline.py)."""
        return self.structured(model, system, prompt, EmptyResultAnswer, thinking)

    def structured(self, model: str, system: str, prompt: str, schema: type[BaseModel],
                   thinking: str | None) -> tuple[BaseModel, str]:
        """Ask for JSON matching `schema`; retry, then fall back to a second model."""
        models = [model] + ([config.FALLBACK_MODEL] if model != config.FALLBACK_MODEL else [])
        # Skip models that just ran out of quota, but always keep at least one to try.
        models = [m for m in models if self._cooling.get(m, 0) <= time.monotonic()] or models[-1:]
        last_error = None
        for m in models:
            try:
                return self._call_with_retry(m, system, prompt, schema, thinking), m
            except errors.APIError as e:
                last_error = e  # try the next model
                if e.code == 429:
                    self._cooling[m] = time.monotonic() + config.QUOTA_COOLDOWN_S
            except Exception as e:  # noqa: BLE001
                if not _is_network_error(e):
                    raise
                last_error = e  # no reply in time, or no internet
        if _is_network_error(last_error):
            raise LLMError("All models failed. Gemini did not reply: check your internet connection "
                           "and try again.")
        if getattr(last_error, "code", None) == 429:
            raise LLMError("All models failed. Gemini's free limit is reached. Wait a minute and try again; "
                           "if it keeps failing, today's limit is used up.")
        raise LLMError(f"All models failed. Last error: {_short(last_error)}")

    def _call_with_retry(self, model: str, system: str, prompt: str, schema: type[BaseModel],
                         thinking: str | None, tries: int = 2) -> BaseModel:
        for attempt in range(tries):
            try:
                return self._call(model, system, prompt, schema, thinking)
            except errors.APIError as e:
                if e.code not in BRIEF or attempt == tries - 1:
                    raise
                time.sleep(2)
        raise AssertionError("unreachable")

    def _call(self, model: str, system: str, prompt: str, schema: type[BaseModel],
              thinking: str | None) -> BaseModel:
        if model.startswith("gemma"):
            # Gemma on the Gemini API has no system instructions or JSON mode.
            keys = ", ".join(f'"{k}"' for k in schema.model_fields)
            text = f"{system}\n\n{prompt}\n\nRespond with ONLY a JSON object with keys {keys}."
            resp = self.client.models.generate_content(model=model, contents=text)
            return _parse_json(resp.text or "", schema)
        resp = self.client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                response_schema=schema,
                # Gemini 3+ "thinks" before answering. 'low' was ~3x faster than the
                # default with the same SQL quality in our tests.
                thinking_config=(types.ThinkingConfig(thinking_level=thinking)
                                 if thinking and model.startswith("gemini-3") else None),
            ),
        )
        if isinstance(resp.parsed, schema):
            return resp.parsed
        return _parse_json(resp.text or "", schema)

    # ---------------------------------------------------------------- embeddings
    def embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        """task_type: RETRIEVAL_DOCUMENT for stored docs, RETRIEVAL_QUERY for questions."""
        out: list[list[float]] = []
        for i in range(0, len(texts), 50):
            batch = texts[i:i + 50]
            for attempt in range(4):
                try:
                    # One Content per text: gemini-embedding-2 is multimodal and
                    # merges a plain list of strings into ONE embedding.
                    resp = self.client.models.embed_content(
                        model=config.EMBED_MODEL,
                        contents=[types.Content(parts=[types.Part(text=t)]) for t in batch],
                        config=types.EmbedContentConfig(task_type=task_type,
                                                        output_dimensionality=config.EMBED_DIM),
                    )
                    out.extend(e.values for e in resp.embeddings)
                    break
                except errors.APIError as e:
                    if e.code not in RETRYABLE or attempt == 3:
                        raise LLMError(f"Embedding failed: {_short(e)}") from e
                    time.sleep(3 * 2 ** attempt)
                except Exception as e:  # noqa: BLE001
                    if not _is_network_error(e):
                        raise
                    if attempt:  # one more try: an old idle connection may simply have died
                        raise LLMError("Could not reach Gemini: check your internet connection.") from e
        if len(out) != len(texts):
            raise LLMError(f"Expected {len(texts)} embeddings, got {len(out)}")
        return out


def _is_network_error(e: Exception) -> bool:
    """No internet, DNS failure, timeout: raised by the HTTP library, not by the Gemini API."""
    return e is not None and (type(e).__module__.startswith(("httpx", "httpcore")) or isinstance(e, OSError))


def _parse_json(text: str, schema: type[BaseModel]) -> BaseModel:
    """Pull a JSON object out of free text (handles ```json fences)."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match and issubclass(schema, SQLAnswer):
        # Model ignored the format: treat a bare SQL reply as the query.
        sql = re.sub(r"^```(?:sql)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
        return SQLAnswer(sql=sql, explanation="(model returned SQL without an explanation)")
    if not match:
        raise LLMError("Model did not return JSON.")
    try:
        return schema.model_validate(json.loads(match.group(0)))
    except (json.JSONDecodeError, ValueError) as e:
        raise LLMError(f"Could not parse model output as JSON: {e}") from e


def _short(e: Exception | None) -> str:
    if e is None:
        return "unknown"
    if isinstance(e, errors.APIError):
        return f"{e.code} {e.status}: {(e.message or '')[:200]}"
    return str(e)[:200]
