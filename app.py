"""TableTalk: ask a PostgreSQL database questions in plain English.

Run from the project folder:  streamlit run app.py
"""
import json
from pathlib import Path

import altair as alt
import pandas as pd
import psycopg
import sqlglot
import streamlit as st

import config
from core.pipeline import MODES, AskResult, Pipeline

st.set_page_config(page_title="TableTalk · Text-to-SQL", page_icon="🗄️", layout="wide")

EXAMPLE_QUESTIONS = [
    "Which 5 products made the most profit in FY2026?",
    "Show monthly revenue for 2025 with month-over-month growth",
    "Which sales rep has the best average review rating on orders they handled?",
    "Which products are low on stock in London Gateway?",
    "How many customers are active vs churned in each country?",
    "Who reports to the Head of Operations, at any level?",
]


@st.cache_resource(show_spinner="Loading RAG index…")
def get_pipeline() -> Pipeline:
    return Pipeline()


# ---------------------------------------------------------------------------- sidebar
with st.sidebar:
    st.title("🗄️ TableTalk")
    st.caption("Plain English → PostgreSQL, powered by Gemini + RAG")
    page = st.radio("Page", ["Ask", "Database", "Evaluation"], label_visibility="collapsed")
    st.divider()
    model = st.selectbox("Model", config.SQL_MODELS, help="From the Google AI Studio catalogue. "
                         f"Falls back to {config.FALLBACK_MODEL} if the model is busy or rate-limited.")
    thinking = st.select_slider("Thinking level", config.THINKING_LEVELS, value=config.DEFAULT_THINKING,
                                help="How long Gemini 3 models reason before answering. Higher = slower.")
    mode = st.radio("Context", list(MODES), format_func=lambda m: MODES[m],
                    help="What the model sees. Compare them on the Evaluation page.")
    if st.button("Clear chat", width="stretch"):
        st.session_state.history = []
        st.rerun()

st.session_state.setdefault("history", [])  # list[AskResult]


# ---------------------------------------------------------------------------- helpers
def auto_chart(df: pd.DataFrame):
    """Pick a sensible chart for small results with one label column and numbers."""
    if df is None or not 2 <= len(df) <= 60 or df.shape[1] < 2:
        return None
    numeric = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and not c.endswith("_id")]
    labels = [c for c in df.columns if c not in numeric]
    if not numeric or not labels or len(set(df.columns)) != len(df.columns):
        return None
    x, y = labels[0], numeric[0]
    data = df[[x, y]].copy()
    is_time = pd.api.types.is_datetime64_any_dtype(data[x]) or data[x].map(
        lambda v: hasattr(v, "year")).all()
    if is_time:
        data[x] = pd.to_datetime(data[x])
        return alt.Chart(data).mark_line(point=True).encode(
            x=alt.X(x, title=x), y=alt.Y(y, title=y), tooltip=[x, y]).properties(height=320)
    return alt.Chart(data).mark_bar().encode(
        x=alt.X(y, title=y), y=alt.Y(f"{x}:N", sort=None, title=None),
        tooltip=[x, y]).properties(height=min(28 * len(data) + 40, 600))


def pretty_sql(sql: str) -> str:
    """Multi-line formatting so the SQL reads top to bottom instead of one long line."""
    try:
        return sqlglot.transpile(sql, read="postgres", write="postgres", pretty=True)[0]
    except sqlglot.errors.SqlglotError:
        return sql


def show_sql(sql: str, label: str = "Generated SQL") -> None:
    st.markdown(f"**{label}**")
    # wrap_lines + full height: the whole query is visible, no scrollbars
    st.code(pretty_sql(sql), language="sql", wrap_lines=True)


def show_table(df: pd.DataFrame, truncated: bool, seconds: float | None, key: str) -> None:
    n = len(df)
    st.caption(f"{n:,} row{'s' if n != 1 else ''}" + (" (truncated)" if truncated else "")
               + (f" · {seconds:.1f}s" if seconds is not None else ""))
    if n == 1 and df.shape[1] <= 3:
        for c, name in zip(st.columns(df.shape[1]), df.columns):
            value = df.iloc[0][name]
            c.metric(name, f"{value:,.2f}" if isinstance(value, float) else str(value))
    else:
        chart = auto_chart(df)
        if chart is not None:
            st.altair_chart(chart, width="stretch")
        st.dataframe(df, width="stretch", hide_index=True)
    st.download_button("Download CSV", df.to_csv(index=False), "result.csv", "text/csv", key=key)


def render_result(r: AskResult) -> None:
    if r.explanation:
        st.markdown(r.explanation)
    if r.assumptions:
        st.caption("Assumptions: " + "; ".join(r.assumptions))
    if not r.ok:
        st.error(r.error)
        if r.sql:
            show_sql(r.sql)
        return

    show_sql(r.sql)
    if r.df.empty:
        note = r.empty_note or "No rows matched this query."
        if r.empty_is_correct:
            st.info(f"🔍 **No matching data.** {note}")
        else:
            st.warning(f"⚠️ **The query returned no rows.** {note}")
        if r.alternative:
            a = r.alternative
            st.markdown(f"#### 💡 {a.label}")
            show_sql(a.sql, "Suggested SQL")
            show_table(a.df, a.truncated, None, key=f"dl-alt-{id(r)}")
    else:
        show_table(r.df, r.truncated, r.total_seconds, key=f"dl-{id(r)}")

    with st.expander("🔎 Retrieved context (RAG)"):
        if r.retrieved is None:
            st.info("Baseline mode: the model saw the full schema and nothing else.")
        else:
            rv = r.retrieved
            st.markdown("**Tables** (cosine similarity · 0.00 = added by rule)")
            st.write(", ".join(f"`{d.key}` {s:.2f}" for d, s in rv.tables)
                     + (f"  ·  **+ BFS join path:** {', '.join(f'`{t}`' for t in rv.join_tables)}"
                        if rv.join_tables else ""))
            st.markdown("**Business rules**")
            for d, s in rv.glossary:
                st.markdown(f"- `{s:.2f}` {d.content.splitlines()[0]}")
            st.markdown("**Similar examples**")
            for d, s in rv.examples:
                st.markdown(f"- `{s:.2f}` {d.key}")
        st.caption(f"Schema sent to the model: {', '.join(r.context_tables)}")
    with st.expander(f"🛠️ Trace · {len(r.attempts)} attempt{'s' if len(r.attempts) != 1 else ''}"):
        for i, a in enumerate(r.attempts, 1):
            status = "✅ ran" if a.error is None else f"❌ {a.error}"
            st.markdown(f"**Attempt {i}** · `{a.model}` · {a.seconds:.1f}s · {status}")
            if a.error:
                st.code(pretty_sql(a.sql), language="sql", wrap_lines=True)
        if len(r.attempts) > 1 and r.ok:
            st.success("Self-corrected: the first query failed, the model read the error and fixed it.")


# ---------------------------------------------------------------------------- pages
def page_ask() -> None:
    st.header("Ask your database")
    try:
        pipeline = get_pipeline()
    except Exception as e:  # noqa: BLE001 - show setup problems in the UI
        st.error(f"Could not start: {e}")
        st.info("Is PostgreSQL running? Run `scripts\\start_db.ps1`, then "
                "`python -m db.setup_db` and `python -m rag.build_index`.")
        st.stop()

    pending = None
    if not st.session_state.history:
        st.write("Try one of these, or type your own question below. Follow-ups like "
                 "*'now only for India'* work too.")
        cols = st.columns(3)
        for i, q in enumerate(EXAMPLE_QUESTIONS):
            if cols[i % 3].button(q, width="stretch", key=f"ex{i}"):
                pending = q

    for r in st.session_state.history:
        st.chat_message("user").write(r.question)
        with st.chat_message("assistant"):
            render_result(r)

    question = st.chat_input("Ask about customers, orders, products, stock, staff…") or pending
    if question:
        st.chat_message("user").write(question)
        history = [(r.question, r.sql) for r in st.session_state.history if r.ok]
        with st.chat_message("assistant"):
            with st.spinner("Retrieving context and writing SQL…"):
                result = pipeline.ask(question, model=model, mode=mode, history=history, thinking=thinking)
            render_result(result)
        st.session_state.history.append(result)


def page_database() -> None:
    st.header("The database")
    st.write("A fictional online store with 10 tables, generated by `db/seed.py` (fixed random seed). "
             "The model only ever gets **read-only** access.")
    try:
        retriever = get_pipeline().retriever
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not load schema: {e}")
        return
    tables = {d.key: d for d in retriever.docs if d.kind == "table"}

    edges = []
    for name, d in tables.items():
        for line in d.content.splitlines()[1:]:
            if "FK -> " in line:
                col = line.split()[0]
                ref = line.split("FK -> ")[1].split()[0].split(".")[0]
                edges.append(f'"{name}" -> "{ref}" [label="{col}", fontsize=9, color="#888888"];')
    dot = ("digraph { rankdir=LR; bgcolor=transparent; "
           'node [shape=box, style="rounded,filled", fillcolor="#eef2ff", color="#6366f1", fontname=Helvetica]; '
           'edge [fontname=Helvetica, fontcolor="#666666"]; ' + " ".join(edges) + " }")
    st.subheader("Relationships (foreign keys)")
    st.graphviz_chart(dot, width="stretch")
    st.caption("The retriever runs a breadth-first search on this graph to find the tables "
               "needed to join the ones it retrieved (e.g. reviews → customers → orders → employees).")

    st.subheader("Tables")
    for name, d in tables.items():
        header = d.content.splitlines()[0].replace("TABLE ", "")
        with st.expander(header):
            st.code(d.content, language="text")

    st.subheader("Business glossary used by RAG")
    for d in retriever.docs:
        if d.kind == "glossary":
            term, rest = d.content.split(": ", 1)
            st.markdown(f"**{term}**: {rest.splitlines()[0]}")


def page_evaluation() -> None:
    st.header("Evaluation")
    st.write("25 held-out questions (none appear in the RAG examples), each with a hand-written "
             "gold query. A question counts as correct when the generated SQL returns the **same data** "
             "as the gold query (execution accuracy).")
    path = Path(__file__).parent / "eval" / "results" / "latest.json"
    if not path.exists():
        st.info("No results yet. Run `python -m eval.run_eval` from the project folder.")
        return
    res = json.loads(path.read_text(encoding="utf-8"))
    df = pd.DataFrame(res["rows"])
    df["setup"] = df["model"] + " · " + df["mode"].map(lambda m: m.replace("_", " "))
    st.caption(f"Run at {res['run_at']} · {res['n_questions']} questions")

    summary = (df.groupby("setup")
                 .agg(accuracy=("correct", "mean"), avg_seconds=("seconds", "mean"),
                      self_corrected=("self_corrected", "sum"))
                 .reset_index())
    summary["accuracy"] = (summary["accuracy"] * 100).round(1)
    cols = st.columns(len(summary))
    for c, row in zip(cols, summary.itertuples()):
        c.metric(row.setup, f"{row.accuracy:.0f}%", help=f"avg {row.avg_seconds:.1f}s per question")

    by_diff = (df.groupby(["setup", "difficulty"])["correct"].mean().mul(100).round(0).reset_index())
    chart = alt.Chart(by_diff).mark_bar().encode(
        x=alt.X("difficulty:N", sort=["easy", "medium", "hard"], title=None),
        xOffset="setup:N",
        y=alt.Y("correct:Q", title="accuracy %", scale=alt.Scale(domain=[0, 100])),
        color=alt.Color("setup:N", title=None, legend=alt.Legend(orient="bottom")),
        tooltip=["setup", "difficulty", "correct"],
    ).properties(height=320)
    st.altair_chart(chart, width="stretch")

    st.subheader("Per question")
    pivot = df.pivot_table(index=["id", "difficulty", "question"], columns="setup",
                           values="correct", aggfunc="first").reset_index()
    for c in pivot.columns[3:]:
        pivot[c] = pivot[c].map({True: "✅", False: "❌"})
    st.dataframe(pivot, width="stretch", hide_index=True)

    failed = df[~df["correct"]]
    if not failed.empty:
        st.subheader("Failures")
        for r in failed.itertuples():
            with st.expander(f"#{r.id} · {r.setup} · {r.question}"):
                if r.error:
                    st.error(r.error)
                st.code(r.sql or "(no SQL)", language="sql")


try:
    {"Ask": page_ask, "Database": page_database, "Evaluation": page_evaluation}[page]()
except psycopg.OperationalError as e:
    st.error(f"Database connection failed: {e}")
