"""Thumbs up / thumbs down. A liked answer becomes an example for similar questions."""
import numpy as np
import psycopg

import config

MIN_SIMILARITY = 0.6


def _ensure(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS rag.feedback (
        dataset    TEXT NOT NULL,
        question   TEXT NOT NULL,
        sql        TEXT NOT NULL,
        helpful    BOOLEAN NOT NULL,
        embedding  REAL[],
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (dataset, question))""")


def save(dataset: str, question: str, sql: str, helpful: bool, llm) -> None:
    """The latest rating for a question wins, so a thumbs down removes an old example."""
    vector = llm.embed([question], "RETRIEVAL_DOCUMENT")[0] if helpful else None
    with psycopg.connect(**config.pg_params("app")) as conn:
        _ensure(conn)
        conn.execute(
            "INSERT INTO rag.feedback (dataset, question, sql, helpful, embedding) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (dataset, question) DO UPDATE "
            "SET sql = EXCLUDED.sql, helpful = EXCLUDED.helpful, embedding = EXCLUDED.embedding, "
            "created_at = now()", (dataset, question, sql, helpful, vector))


def liked(dataset: str) -> list[tuple[str, str, list[float]]]:
    with psycopg.connect(**config.pg_params("app")) as conn:
        _ensure(conn)
        return conn.execute("SELECT question, sql, embedding FROM rag.feedback "
                            "WHERE dataset = %s AND helpful", (dataset,)).fetchall()


def most_similar(rows, query_vec, k: int = 2) -> list[tuple[str, str, float]]:
    """rows from liked() -> [(question, sql, cosine similarity)], best first."""
    if not rows:
        return []
    matrix = np.array([r[2] for r in rows], dtype=np.float32)
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
    query = np.array(query_vec, dtype=np.float32)
    scores = matrix @ (query / np.linalg.norm(query))
    return [(rows[i][0], rows[i][1], float(scores[i]))
            for i in np.argsort(-scores)[:k] if scores[i] >= MIN_SIMILARITY]
