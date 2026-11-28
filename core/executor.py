"""Run checked SQL as the read-only role and return a DataFrame."""
from dataclasses import dataclass

import pandas as pd
import psycopg
from psycopg import sql as pgsql

import config


@dataclass
class QueryResult:
    df: pd.DataFrame
    truncated: bool  # True if more than MAX_ROWS rows existed


def run_query(sql: str, max_rows: int = config.MAX_ROWS, schema: str = "public") -> QueryResult:
    """Execute as tabletalk_reader inside a read-only transaction with a timeout.

    Raises psycopg.Error on SQL errors; the pipeline feeds that message back
    to the model so it can fix its own query.
    """
    with psycopg.connect(**config.pg_params("reader")) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(f"SET LOCAL statement_timeout = {int(config.STATEMENT_TIMEOUT_MS)}")
            cur.execute(pgsql.SQL("SET LOCAL search_path TO {}").format(pgsql.Identifier(schema)))
            cur.execute(sql)
            if cur.description is None:
                return QueryResult(pd.DataFrame(), False)
            columns = [d.name for d in cur.description]
            rows = cur.fetchmany(max_rows + 1)
        conn.rollback()
    truncated = len(rows) > max_rows
    df = pd.DataFrame(rows[:max_rows], columns=columns)
    # NUMERIC comes back as Decimal; convert to float so charts and stats work.
    for i in range(df.shape[1]):  # by position: generated SQL may repeat column names
        col = df.iloc[:, i]
        if col.map(lambda v: v.__class__.__name__ == "Decimal").any():
            df.isetitem(i, pd.to_numeric(col, errors="coerce"))
    return QueryResult(df, truncated)
