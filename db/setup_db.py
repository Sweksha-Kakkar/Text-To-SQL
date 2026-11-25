"""Create the tabletalk database, its roles, the 10 tables, and load the demo data.

Run from the project folder:  python -m db.setup_db
Safe to re-run: it drops and recreates the tables (data is regenerated identically).
"""
import os
import sys
from pathlib import Path

import psycopg
from psycopg import sql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402
from db.seed import generate  # noqa: E402

SCHEMA_FILE = Path(__file__).with_name("schema.sql")


def ensure_role(cur, name: str, password: str) -> None:
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,))
    verb = "ALTER" if cur.fetchone() else "CREATE"
    cur.execute(sql.SQL(verb + " ROLE {} WITH LOGIN PASSWORD {}").format(
        sql.Identifier(name), sql.Literal(password)))


def main() -> None:
    if not os.getenv("PG_SUPERUSER_PASSWORD"):
        sys.exit("PG_SUPERUSER_PASSWORD is missing from .env")

    # 1. database + roles (needs autocommit: CREATE DATABASE can't run in a transaction)
    with psycopg.connect(**config.pg_params("super", dbname="postgres"), autocommit=True) as conn:
        cur = conn.cursor()
        ensure_role(cur, config.APP_USER, config.env_secret("PG_APP_PASSWORD"))
        ensure_role(cur, config.READER_USER, config.env_secret("PG_READER_PASSWORD"))
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (config.PG_DB,))
        if not cur.fetchone():
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(config.PG_DB)))
            print(f"created database {config.PG_DB}")

    # 2. tables, data, permissions
    with psycopg.connect(**config.pg_params("super")) as conn:
        cur = conn.cursor()
        cur.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
        print("created 10 tables")

        for table, (cols, rows) in generate().items():
            copy_sql = sql.SQL("COPY {} ({}) FROM STDIN").format(
                sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, cols)))
            with cur.copy(copy_sql) as copy:
                for row in rows:
                    copy.write_row(row)
            print(f"  loaded {table:<12} {len(rows):>7,} rows")

        db, app, reader = (sql.Identifier(x) for x in (config.PG_DB, config.APP_USER, config.READER_USER))
        cur.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(db))
        cur.execute(sql.SQL("REVOKE CREATE ON SCHEMA public FROM PUBLIC"))
        for role in (app, reader):
            cur.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(db, role))
            cur.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
            cur.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(role))
        # The reader can only ever read, and slow queries are cut off.
        cur.execute(sql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(reader))
        cur.execute(sql.SQL("ALTER ROLE {} SET statement_timeout = {}").format(
            reader, sql.Literal(f"{config.STATEMENT_TIMEOUT_MS}ms")))
        # RAG index lives in its own schema, owned by the app role, invisible to the reader.
        cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS rag AUTHORIZATION {}").format(app))
        cur.execute(sql.SQL("REVOKE ALL ON SCHEMA rag FROM PUBLIC"))
        conn.commit()

    with psycopg.connect(**config.pg_params("super"), autocommit=True) as conn:
        conn.execute("ANALYZE")
    print("granted permissions to tabletalk_app / tabletalk_reader and analyzed tables")
    print("done. Next: python -m rag.build_index")


if __name__ == "__main__":
    main()
