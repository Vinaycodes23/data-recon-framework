"""Aggregate checks pushed down to the database (SQL) or to DuckDB (parquet / csv files).

The pandas engine loads both tables into memory before it can compute an aggregate. For
sum / count / min / max / mean / null_count / count_distinct that is wasteful: the database or a
columnar engine can answer without moving rows. ``pushdown_aggregates`` runs one
``SELECT agg1, agg2, ... FROM (<source>)`` per side and compares the results with the same
tolerance rule as the in-memory engine.

Semantics differ slightly from the pandas path: values are aggregated as the engine stores
them (NULL is the only null, strings are not parsed as numbers), so use numeric columns.
Row-level comparison (missing / mismatched rows) still needs the in-memory engine.
DuckDB is an optional dependency, imported lazily.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import text

from recon.config import Aggregate, TestConfig, resolve_env
from recon.connectors.base import ConnectorError
from recon.connectors.sql import build_query, get_engine
from recon.engine import compare_aggregate_values

SQL_TYPES = {"sqlite", "postgres", "postgresql", "mssql", "snowflake", "redshift", "mysql", "sql"}
DUCKDB_TYPES = {"parquet", "csv"}
_FUNC_SQL = {
    "sum": "SUM({c})", "count": "COUNT({c})", "count_distinct": "COUNT(DISTINCT {c})",
    "min": "MIN({c})", "max": "MAX({c})", "mean": "AVG({c})",
    "null_count": "SUM(CASE WHEN {c} IS NULL THEN 1 ELSE 0 END)",
}
_PLAIN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def quote_ident(name: str) -> str:
    """Leave plain identifiers to the database's own case rules; quote anything else."""
    return name if _PLAIN.match(name) else '"' + name.replace('"', '""') + '"'


def aggregate_sql(from_sql: str, aggs: list[tuple[Aggregate, str]]) -> str:
    """``SELECT <agg> AS a0, ... FROM (<from_sql>) t`` for (aggregate, physical column) pairs."""
    cols = ", ".join(f"{_FUNC_SQL[a.func].format(c=quote_ident(col))} AS a{i}" for i, (a, col) in enumerate(aggs))
    return f"SELECT {cols} FROM ({from_sql}) t"


def supports(spec: dict[str, Any]) -> bool:
    """True if this connector spec can answer aggregates without loading rows."""
    ctype = str(spec.get("type", "")).lower()
    return ctype in SQL_TYPES or (ctype in DUCKDB_TYPES and "path" in spec)


def _scalar(v: Any) -> Any:
    if v is None:
        return None
    return float(v) if hasattr(v, "__float__") and not isinstance(v, (int, float)) else v


def _run_side(spec: dict[str, Any], aggs: list[tuple[Aggregate, str]]) -> list[Any]:
    ctype = str(spec.get("type", "")).lower()
    if ctype in SQL_TYPES:
        sql = aggregate_sql(build_query(spec), aggs)
        with get_engine(str(spec["url"])).connect() as conn:
            row = conn.execute(text(sql)).one()
        return [_scalar(v) for v in row]
    if ctype in DUCKDB_TYPES and "path" in spec:
        import duckdb  # lazy: optional dependency

        reader = "read_parquet" if ctype == "parquet" else "read_csv_auto"
        path = str(spec["path"]).replace("'", "''")
        sql = aggregate_sql(f"SELECT * FROM {reader}('{path}')", aggs)
        con = duckdb.connect()
        try:
            return [_scalar(v) for v in con.execute(sql).fetchone()]
        finally:
            con.close()
    raise ConnectorError(f"pushdown not supported for connector type '{ctype}'")


def pushdown_aggregates(cfg: TestConfig) -> list[dict[str, Any]]:
    """Evaluate ``cfg.aggregates`` on both sides without loading rows; same result shape as the engine."""
    if not cfg.aggregates:
        return []
    src, tgt = resolve_env(cfg.source), resolve_env(cfg.target)
    cmap = {k.lower(): v for k, v in cfg.column_map.items()}
    s_aggs = [(a, a.column) for a in cfg.aggregates]
    t_aggs = [(a, cmap.get(a.column.lower(), a.column)) for a in cfg.aggregates]
    sv, tv = _run_side(src, s_aggs), _run_side(tgt, t_aggs)
    return [compare_aggregate_values(a, s, t) for a, s, t in zip(cfg.aggregates, sv, tv, strict=True)]
