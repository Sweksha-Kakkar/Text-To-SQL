"""Build the RAG index: table docs + glossary + verified examples -> embeddings in Postgres.

Run from the project folder:  python -m rag.build_index
Re-run whenever you change the schema, glossary.json or examples.json.
"""
import json
import sys
from pathlib import Path

import psycopg
import sqlglot
from psycopg.types.json import Jsonb
from sqlglot import exp

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402
from core.executor import run_query  # noqa: E402
from core.llm import Gemini  # noqa: E402
from core.schema import load_schema  # noqa: E402
from core.sql_guard import check_sql  # noqa: E402

KNOWLEDGE = Path(__file__).with_name("knowledge")

DDL = """
CREATE TABLE IF NOT EXISTS rag.documents (
    doc_id     SERIAL PRIMARY KEY,
    kind       TEXT NOT NULL CHECK (kind IN ('table', 'glossary', 'example')),
    key        TEXT NOT NULL,
    content    TEXT NOT NULL,          -- what the model sees
    embed_text TEXT NOT NULL,          -- what was embedded
    meta       JSONB NOT NULL DEFAULT '{}',
    embedding  REAL[] NOT NULL,
    model      TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (kind, key)
)
"""


def main() -> None:
    docs = []  # (kind, key, content, embed_text, meta)

    with psycopg.connect(**config.pg_params("app")) as conn:
        for t in load_schema(conn).values():
            text = t.to_text()
            docs.append(("table", t.name, text, text, {"fk_tables": sorted({r for _, r, _ in t.foreign_keys})}))
    print(f"tables:   {sum(d[0] == 'table' for d in docs)}")

    for g in json.loads((KNOWLEDGE / "glossary.json").read_text(encoding="utf-8")):
        content = f"{g['term']}: {g['definition']}\n  SQL pattern: {g['sql']}"
        docs.append(("glossary", g["term"], content, f"{g['term']}: {g['definition']}", {}))
    print(f"glossary: {sum(d[0] == 'glossary' for d in docs)}")

    # Every example must pass the guard and actually run, or the build fails:
    # a wrong example would teach the model a wrong pattern.
    examples = json.loads((KNOWLEDGE / "examples.json").read_text(encoding="utf-8"))
    for ex in examples:
        try:
            result = run_query(check_sql(ex["sql"]), max_rows=5)
        except Exception as e:
            sys.exit(f"Example failed: {ex['question']}\n  {e}")
        if result.df.empty:
            print(f"  warning: example returns no rows: {ex['question']}")
        tables = sorted({t.name for t in sqlglot.parse_one(ex["sql"], read="postgres").find_all(exp.Table)}
                        & set(config.STORE_TABLES))
        docs.append(("example", ex["question"], f"Q: {ex['question']}\nSQL:\n{ex['sql'].strip()}",
                     ex["question"], {"tables": tables}))
    print(f"examples: {len(examples)} (all verified against the database)")

    llm = Gemini()
    vectors = llm.embed([d[3] for d in docs], "RETRIEVAL_DOCUMENT")
    print(f"embedded {len(vectors)} docs with {config.EMBED_MODEL} ({len(vectors[0])} dims)")

    with psycopg.connect(**config.pg_params("app")) as conn:
        conn.execute(DDL)
        conn.execute("TRUNCATE rag.documents RESTART IDENTITY")
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO rag.documents (kind, key, content, embed_text, meta, embedding, model)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [(k, key, c, e, Jsonb(m), v, config.EMBED_MODEL)
                 for (k, key, c, e, m), v in zip(docs, vectors)])
    print("saved to rag.documents")


if __name__ == "__main__":
    main()
