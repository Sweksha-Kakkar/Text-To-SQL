"""Static checks on model-generated SQL before it reaches the database.

This is layer 1 of 3. Even if something slips through, the query runs as
tabletalk_reader (SELECT-only, read-only transactions) with a statement timeout.
"""
import sqlglot
from sqlglot import exp

import config

# Node types that change data or the database. Looked up by name so the list
# works across sqlglot versions.
_FORBIDDEN_NAMES = ["Insert", "Update", "Delete", "Drop", "Create", "Alter", "Command", "Merge",
                    "TruncateTable", "Grant", "Revoke", "Copy", "Set", "Transaction", "Commit",
                    "Rollback", "Into", "Lock", "Use", "Pragma"]
FORBIDDEN_NODES = tuple(getattr(exp, n) for n in _FORBIDDEN_NAMES if hasattr(exp, n))

# Functions that could read files, sleep, or touch server settings.
FORBIDDEN_FUNCS = {"dblink", "lo_import", "lo_export", "set_config", "current_setting",
                   "query_to_xml", "copy", "txid_current"}
QUERY_TYPES = tuple(getattr(exp, n) for n in ("Query", "Select", "Union", "Intersect", "Except")
                    if hasattr(exp, n))


class UnsafeSQL(ValueError):
    pass


def check_sql(sql: str, allowed_tables=config.STORE_TABLES, schema: str = "public") -> str:
    """Return the cleaned SQL if it is a single safe SELECT, else raise UnsafeSQL."""
    sql = (sql or "").strip().rstrip(";").strip()
    if not sql:
        raise UnsafeSQL("Empty query.")
    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except sqlglot.errors.ParseError as e:
        raise UnsafeSQL(f"SQL could not be parsed: {str(e).splitlines()[0]}") from e
    if len(statements) != 1:
        raise UnsafeSQL("Exactly one statement is allowed.")
    tree = statements[0]
    if not isinstance(tree, QUERY_TYPES):
        raise UnsafeSQL(f"Only SELECT queries are allowed (got {tree.key.upper()}).")

    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise UnsafeSQL(f"Forbidden operation: {node.key.upper()}.")
        if isinstance(node, exp.Func):
            name = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()
            if name.startswith("pg_") or name in FORBIDDEN_FUNCS:
                raise UnsafeSQL(f"Function not allowed: {name}().")

    allowed = {t.lower() for t in allowed_tables}
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if not name:  # e.g. FROM generate_series(...)
            continue
        if table.catalog or (table.db and table.db.lower() != schema):
            raise UnsafeSQL(f"Schema not allowed: {table.db}.")
        if name not in allowed and name not in cte_names:
            raise UnsafeSQL(f"Unknown or forbidden table: {name}.")
    return sql
