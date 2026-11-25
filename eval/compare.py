"""Execution accuracy: does the generated query return the same data as the gold query?

Comparing SQL text is useless (many correct queries look different), so we
compare RESULTS, leniently:
  * same number of rows
  * every gold column has a matching predicted column (any order, any name);
    a text column may also match two predicted columns joined with a space
    ("first_name", "last_name" == "rep")
  * numbers are compared rounded to 2 decimals; ints and floats are equal
  * extra predicted columns are fine (the model may add helpful context)
With match="numeric", only numeric gold columns must match (used where labels
can legitimately be formatted many ways, e.g. months).
"""
from datetime import date, datetime
from decimal import Decimal
from numbers import Number

import pandas as pd


def _norm(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "NULL"
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (Number, Decimal)):
        return f"{round(float(v), 2):.2f}"
    if isinstance(v, (datetime, pd.Timestamp)):
        return v.date().isoformat() if v.time() == datetime.min.time() else v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return str(v).strip()


def _is_numeric(col: pd.Series) -> bool:
    vals = [v for v in col if v is not None and not (isinstance(v, float) and pd.isna(v))]
    return bool(vals) and all(isinstance(v, (Number, Decimal)) and not isinstance(v, bool) for v in vals)


def _column(col: pd.Series) -> tuple[str, list]:
    """('num', sorted floats) or ('str', sorted normalised strings)."""
    if _is_numeric(col):
        return "num", sorted(-1e308 if v is None or pd.isna(v) else float(v) for v in col)
    return "str", sorted(_norm(v) for v in col)


def _same(a: tuple[str, list], b: tuple[str, list]) -> bool:
    if a[0] != b[0] or len(a[1]) != len(b[1]):
        return False
    if a[0] == "str":
        return a[1] == b[1]
    # Tolerance: rounding at a different step (round-then-sum vs sum-then-round)
    # legitimately shifts totals by a cent or two.
    return all(abs(x - y) <= max(0.05, 1e-6 * abs(y)) for x, y in zip(a[1], b[1]))


def results_match(gold: pd.DataFrame, pred: pd.DataFrame, match: str = "all") -> bool:
    if len(gold) != len(pred):
        return False
    if len(gold) == 0:
        return True
    pred_cols = [_column(pred.iloc[:, j]) for j in range(pred.shape[1])]
    # Two adjacent text columns joined with a space ("first", "last" -> "first last")
    text_idx = [j for j in range(pred.shape[1]) if not _is_numeric(pred.iloc[:, j])]
    for a, b in zip(text_idx, text_idx[1:]):
        pred_cols.append(("str", sorted(f"{_norm(x)} {_norm(y)}"
                                        for x, y in zip(pred.iloc[:, a], pred.iloc[:, b]))))

    used: set[int] = set()
    for i in range(gold.shape[1]):
        g = gold.iloc[:, i]
        if match == "numeric" and not _is_numeric(g):
            continue
        target = _column(g)
        hit = next((j for j, col in enumerate(pred_cols) if j not in used and _same(col, target)), None)
        if hit is None:
            return False
        used.add(hit)
    return True
