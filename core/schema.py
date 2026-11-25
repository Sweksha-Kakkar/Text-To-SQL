"""Read table/column metadata from PostgreSQL's catalog and render it as text.

The text for each table becomes one RAG document, and the foreign keys become
a graph the retriever walks (BFS) to find the tables needed to join things.
"""
from dataclasses import dataclass, field

import psycopg
from psycopg import sql

import config

VALUE_HINT_MAX = 25  # list distinct values for text columns with at most this many


@dataclass
class Column:
    name: str
    data_type: str
    nullable: bool
    comment: str | None = None
    pk: bool = False
    fk: tuple[str, str] | None = None  # (ref_table, ref_column)
    values: list[str] | None = None    # distinct values for low-cardinality text columns


@dataclass
class Table:
    name: str
    comment: str | None
    row_count: int
    columns: list[Column] = field(default_factory=list)

    def to_text(self) -> str:
        lines = [f"TABLE {self.name} ({self.row_count:,} rows)"
                 + (f" -- {self.comment}" if self.comment else "")]
        for c in self.columns:
            parts = [f"  {c.name} {c.data_type}"]
            if c.pk:
                parts.append("PRIMARY KEY")
            if c.fk:
                parts.append(f"FK -> {c.fk[0]}.{c.fk[1]}")
            if not c.nullable and not c.pk:
                parts.append("NOT NULL")
            if c.comment:
                parts.append(f"-- {c.comment}")
            if c.values:
                parts.append("Values: " + ", ".join(f"'{v}'" for v in c.values))
            lines.append(" ".join(parts))
        return "\n".join(lines)

    @property
    def foreign_keys(self) -> list[tuple[str, str, str]]:
        """[(column, ref_table, ref_column)]"""
        return [(c.name, *c.fk) for c in self.columns if c.fk]


COLUMNS_SQL = """
SELECT c.table_name, c.column_name, c.data_type, c.is_nullable = 'YES',
       col_description(format('public.%%I', c.table_name)::regclass, c.ordinal_position)
FROM information_schema.columns c
WHERE c.table_schema = 'public' AND c.table_name = ANY(%s)
ORDER BY c.table_name, c.ordinal_position
"""
# pg_catalog instead of information_schema: the latter hides FK targets from non-owners.
KEYS_SQL = """
SELECT con.conrelid::regclass::text, con.contype, a.attname,
       con.confrelid::regclass::text, af.attname
FROM pg_constraint con
JOIN pg_namespace n ON n.oid = con.connamespace AND n.nspname = 'public'
CROSS JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum
LEFT JOIN pg_attribute af ON con.contype = 'f' AND af.attrelid = con.confrelid
                         AND af.attnum = con.confkey[k.ord]
WHERE con.contype IN ('p', 'f')
"""


def load_schema(conn: psycopg.Connection, tables=config.STORE_TABLES) -> dict[str, Table]:
    cur = conn.cursor()
    result: dict[str, Table] = {}
    for t in tables:
        cur.execute("SELECT obj_description(format('public.%%I', %s::text)::regclass, 'pg_class')", (t,))
        comment = cur.fetchone()[0]
        cur.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(t)))
        result[t] = Table(t, comment, cur.fetchone()[0])

    cur.execute(COLUMNS_SQL, (list(tables),))
    for table, col, dtype, nullable, comment in cur.fetchall():
        dtype = {"character varying": "varchar", "timestamp without time zone": "timestamp",
                 "double precision": "float"}.get(dtype, dtype)
        result[table].columns.append(Column(col, dtype, nullable, comment))

    cur.execute(KEYS_SQL)
    for table, kind, col, ref_table, ref_col in cur.fetchall():
        if table not in result:
            continue
        column = next(c for c in result[table].columns if c.name == col)
        if kind == "p":
            column.pk = True
        else:
            column.fk = (ref_table, ref_col)

    # Low-cardinality text columns: list their actual values so the model
    # writes status = 'cancelled' and not status = 'Canceled'.
    for t in result.values():
        for c in t.columns:
            if c.data_type != "varchar" or c.pk:
                continue
            cur.execute(sql.SQL("SELECT DISTINCT {c} FROM {t} WHERE {c} IS NOT NULL ORDER BY 1 LIMIT {n}")
                        .format(c=sql.Identifier(c.name), t=sql.Identifier(t.name),
                                n=sql.Literal(VALUE_HINT_MAX + 1)))
            values = [r[0] for r in cur.fetchall()]
            if len(values) <= VALUE_HINT_MAX:
                c.values = values
    return result
