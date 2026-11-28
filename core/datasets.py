"""Datasets: the built-in demo store plus anything the user uploads.

Each upload gets its own PostgreSQL schema (ds_<name>). The app role creates and
owns it; the reader role only gets SELECT, exactly like on the demo store.
"""
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

import config
from core.schema import load_schema

NULL_WORDS = {"", "n/a", "#n/a", "null", "nan", "none"}
SENSITIVE = re.compile(r"e_?mail|phone|mobile|address|aadhaar|aadhar|passport|password|"
                       r"account.?(no|num)|birth|\b(pan|dob)\b", re.I)
RESERVED = set("""all analyse analyze and any array as asc asymmetric authorization between binary both case
cast check collate column constraint create cross current_date current_role current_time current_timestamp
current_user default deferrable desc distinct do else end except false fetch for foreign freeze from full
grant group having ilike in initially inner intersect into is isnull join lateral leading left like limit
localtime localtimestamp natural not notnull null offset on only or order outer over overlaps placing primary
references returning right select session_user similar some symmetric table tablesample then to trailing
true union unique user using variadic verbose when where window with""".split())


@dataclass
class Dataset:
    name: str                 # short id, e.g. 'store' or 'ipl'
    title: str
    schema: str               # PostgreSQL schema holding the tables
    tables: list[str]
    schema_text: dict[str, str] = field(default_factory=dict)  # uploads only
    data_facts: str = ""
    rules: str = ""           # plain-English notes written by the user, one per line

    @property
    def builtin(self) -> bool:
        return self.name == "store"


STORE = Dataset("store", "Demo store (ShopVerse)", "public", list(config.STORE_TABLES))


# ------------------------------------------------------------------ registry
def _ensure_registry(conn: psycopg.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS rag.datasets (
        name        TEXT PRIMARY KEY,
        title       TEXT NOT NULL,
        tables      JSONB NOT NULL,
        schema_text JSONB NOT NULL,
        data_facts  TEXT NOT NULL,
        rules       TEXT NOT NULL DEFAULT '',
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now())""")


def list_datasets() -> list[Dataset]:
    with psycopg.connect(**config.pg_params("app")) as conn:
        _ensure_registry(conn)
        rows = conn.execute("SELECT name, title, tables, schema_text, data_facts, rules "
                            "FROM rag.datasets ORDER BY created_at").fetchall()
    return [STORE] + [Dataset(n, t, f"ds_{n}", tables, text, facts, rules)
                      for n, t, tables, text, facts, rules in rows]


def get_dataset(name: str) -> Dataset:
    for d in list_datasets():
        if d.name == name:
            return d
    raise KeyError(f"No dataset called {name!r}")


def save_rules(name: str, rules: str) -> None:
    with psycopg.connect(**config.pg_params("app")) as conn:
        conn.execute("UPDATE rag.datasets SET rules = %s WHERE name = %s", (rules.strip(), name))


def delete_dataset(name: str) -> None:
    ds = get_dataset(name)
    if ds.builtin:
        raise ValueError("The demo store cannot be deleted.")
    with psycopg.connect(**config.pg_params("app")) as conn:
        conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(ds.schema)))
        conn.execute("DELETE FROM rag.datasets WHERE name = %s", (name,))
        if conn.execute("SELECT to_regclass('rag.feedback')").fetchone()[0]:
            conn.execute("DELETE FROM rag.feedback WHERE dataset = %s", (name,))


# ------------------------------------------------------------------- reading
def safe_name(raw: str, fallback: str, taken: set[str]) -> str:
    """'InvoiceNo' -> 'invoice_no'. Always unique, short and usable without quotes."""
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(raw).strip()).lower()
    name = re.sub(r"[^a-z0-9]+", "_", name).strip("_")[:40] or fallback
    if name[0].isdigit():
        name = f"c_{name}"
    if name in RESERVED:
        name = f"{name}_col"
    base, i = name, 2
    while name in taken:
        name, i = f"{base}_{i}", i + 1
    taken.add(name)
    return name


def _read_csv(data: bytes) -> pd.DataFrame:
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    first_line = text.split("\n", 1)[0]
    delimiter = max(",;\t|", key=first_line.count)
    return pd.read_csv(io.StringIO(text), sep=delimiter, dtype=str, keep_default_na=False)


def read_files(files: list[tuple[str, bytes]]) -> dict[str, pd.DataFrame]:
    """[(filename, bytes)] -> {table_name: DataFrame of strings}. One table per CSV or sheet."""
    frames: dict[str, pd.DataFrame] = {}
    taken: set[str] = set()
    for filename, data in files:
        stem, suffix = Path(filename).stem, Path(filename).suffix.lower()
        if suffix in (".csv", ".tsv", ".txt"):
            parts = [(stem, _read_csv(data))]
        elif suffix == ".xlsx":
            sheets = pd.read_excel(io.BytesIO(data), sheet_name=None, dtype=str, keep_default_na=False)
            parts = [(stem if len(sheets) == 1 else sheet, df) for sheet, df in sheets.items()]
        else:
            raise ValueError(f"{filename}: only CSV, TSV and XLSX files are supported.")
        for label, df in parts:
            if df.empty or df.shape[1] == 0:
                continue
            if len(df) > config.MAX_UPLOAD_ROWS:
                raise ValueError(f"{label}: more than {config.MAX_UPLOAD_ROWS:,} rows.")
            frames[safe_name(label, "data", taken)] = df
    if not frames:
        raise ValueError("No data rows found in the file.")
    return frames


def sensitive_columns(columns) -> list[str]:
    """Column headers that look like personal data, so the user can leave them out."""
    return [c for c in columns if SENSITIVE.search(str(c).replace("_", " "))]


# ------------------------------------------------------------- type guessing
def infer_column(col: pd.Series) -> tuple[str, list, str | None]:
    """Return (postgres type, values ready for COPY, note for the user).

    Every value is checked, not a sample, so loading can never fail half way.
    """
    text = col.fillna("").astype(str).str.strip()
    null = text.str.lower().isin(NULL_WORDS)
    present = text[~null]

    def out(values: pd.Series) -> list:
        full = pd.Series([None] * len(text), index=text.index, dtype=object)
        full[~null] = values.astype(object)
        return [None if empty else v for v, empty in zip(full.tolist(), null.tolist())]

    if present.empty:
        return "varchar", out(present), None
    if present.str.lower().isin({"true", "false", "yes", "no"}).all():
        flags = present.str.lower().map({"true": True, "yes": True, "false": False, "no": False})
        return "boolean", out(flags), None

    # Leading zeros ("00123") mean it is a code, not a number.
    if not present.str.match(r"-?0\d").any():
        if present.str.fullmatch(r"-?\d{1,18}").all():
            return "bigint", out(present), None
        number = present
        # 1,234,567 and the Indian 12,34,567
        if present.str.fullmatch(r"-?\d{1,3}(,\d{2,3})*,\d{3}(\.\d+)?|-?\d+(\.\d+)?").all():
            number = present.str.replace(",", "", regex=False)
        if number.str.fullmatch(r"-?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d{1,2})?").all():
            return "numeric", out(number), None

    note = None
    parsed = None
    if present.str.fullmatch(r"\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?").all():
        parsed = pd.to_datetime(present, format="ISO8601", errors="coerce")
    else:
        m = present.str.extract(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})(?: (\d{1,2}:\d{2}(?::\d{2})?))?$")
        if m[2].notna().all():
            first, second = m[0].astype(int), m[1].astype(int)
            day_first = not (second > 12).any()
            if day_first and not (first > 12).any():
                note = "dates could be day/month or month/day; they were read as day/month"
            day, month = (m[0], m[1]) if day_first else (m[1], m[0])
            iso = m[2] + "-" + month.str.zfill(2) + "-" + day.str.zfill(2)
            clock = m[3].fillna("00:00")
            clock = clock.where(clock.str.count(":") == 2, clock + ":00").str.zfill(8)
            parsed = pd.to_datetime(iso + " " + clock, format="%Y-%m-%d %H:%M:%S", errors="coerce")
    if parsed is not None and parsed.notna().all():
        if (parsed == parsed.dt.normalize()).all():
            return "date", out(parsed.dt.strftime("%Y-%m-%d")), note
        return "timestamp", out(parsed.dt.strftime("%Y-%m-%d %H:%M:%S")), note
    return "varchar", out(present), None


# ------------------------------------------------------------------ creating
def create_dataset(title: str, frames: dict[str, pd.DataFrame], rules: str = "",
                   drop_columns: dict[str, list[str]] | None = None) -> tuple[Dataset, list[str]]:
    """Load the frames into a new schema. All or nothing: one transaction."""
    title = title.strip()
    if not title:
        raise ValueError("Give the dataset a name.")
    name = safe_name(title, "dataset", {d.name for d in list_datasets()})
    schema = f"ds_{name}"
    notes: list[str] = []
    tables: list[str] = []

    with psycopg.connect(**config.pg_params("app")) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        for table, df in frames.items():
            df = df.drop(columns=(drop_columns or {}).get(table, []))
            if df.shape[1] == 0:
                continue
            taken: set[str] = set()
            columns = []  # (name, header, type, values)
            for i, header in enumerate(df.columns):
                col = safe_name(header, f"column_{i + 1}", taken)
                dtype, values, note = infer_column(df.iloc[:, i])
                columns.append((col, str(header), dtype, values))
                if note:
                    notes.append(f"{table}.{col}: {note}")
            target = sql.Identifier(schema, table)
            conn.execute(sql.SQL("CREATE TABLE {} ({})").format(target, sql.SQL(", ").join(
                sql.SQL("{} " + dtype).format(sql.Identifier(col)) for col, _, dtype, _ in columns)))
            with conn.cursor() as cur, cur.copy(sql.SQL("COPY {} FROM STDIN").format(target)) as copy:
                for row in zip(*(values for *_, values in columns)):
                    copy.write_row(row)
            # Keep the original header: it often explains the column better than the id.
            for col, header, _, _ in columns:
                if header.strip().lower() != col:
                    conn.execute(sql.SQL("COMMENT ON COLUMN {}.{} IS {}").format(
                        target, sql.Identifier(col), sql.Literal(f"Original header: {header}")))
            tables.append(table)

        if not tables:
            raise ValueError("Every column was left out, so there is nothing to load.")
        reader = sql.Identifier(config.READER_USER)
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(schema), reader))
        conn.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(
            sql.Identifier(schema), reader))

        meta = load_schema(conn, tables, schema)
        _link_tables(conn, schema, meta)
        text = {t: meta[t].to_text() for t in tables}
        facts = data_facts(conn, schema, tables)
        conn.execute("INSERT INTO rag.datasets (name, title, tables, schema_text, data_facts, rules) "
                     "VALUES (%s, %s, %s, %s, %s, %s)",
                     (name, title, Jsonb(tables), Jsonb(text), facts, rules.strip()))
    return Dataset(name, title, schema, tables, text, facts, rules.strip()), notes


def _link_tables(conn, schema: str, meta: dict) -> None:
    """Uploads have no foreign keys, so guess them: a shared '*_id' column that is
    unique in exactly one table is treated as pointing there."""
    names = {c.name for t in meta.values() for c in t.columns if c.name.endswith("_id")}
    for column in sorted(names):
        holders = [t for t in meta.values() if any(c.name == column for c in t.columns)]
        if len(holders) < 2:
            continue
        unique = []
        for t in holders:
            distinct, total = conn.execute(sql.SQL("SELECT count(DISTINCT {c}), count(*) FROM {t}").format(
                c=sql.Identifier(column), t=sql.Identifier(schema, t.name))).fetchone()
            if distinct == total:
                unique.append(t.name)
        if len(unique) != 1:
            continue
        for t in holders:
            if t.name != unique[0]:
                next(c for c in t.columns if c.name == column).fk = (unique[0], column)


def data_facts(conn, schema: str, tables: list[str]) -> str:
    """Date ranges: the evidence the model needs to explain empty results."""
    cols = conn.execute(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = ANY(%s) "
        "AND data_type IN ('date', 'timestamp without time zone') "
        "ORDER BY table_name, ordinal_position", (schema, list(tables))).fetchall()
    lines = []
    for table, col in cols:
        lo, hi = conn.execute(sql.SQL("SELECT min({c})::date, max({c})::date FROM {t}").format(
            c=sql.Identifier(col), t=sql.Identifier(schema, table))).fetchone()
        lines.append(f"- {table}.{col} ranges from {lo} to {hi}")
    return "\n".join(lines)
