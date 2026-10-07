"""One SQLAlchemy loader for sqlite / postgres / mssql / snowflake / redshift."""

from __future__ import annotations

import threading
from typing import Any

import pandas as pd
from sqlalchemy import Engine, create_engine, text

from recon.connectors.base import register

_ENGINES: dict[str, Engine] = {}
_LOCK = threading.Lock()


def get_engine(url: str) -> Engine:
    """Return a cached engine for ``url`` (one per URL, ``pool_pre_ping`` enabled)."""
    with _LOCK:
        if url not in _ENGINES:
            _ENGINES[url] = create_engine(url, pool_pre_ping=True)
        return _ENGINES[url]


def build_query(spec: dict[str, Any]) -> str:
    """Return the SQL to run: ``query`` verbatim, or ``SELECT * FROM [schema.]table [WHERE ...]``."""
    if spec.get("query"):
        return str(spec["query"])
    if not spec.get("table"):
        raise KeyError("sql connector requires 'query' or 'table'")
    name = f"{spec['schema']}.{spec['table']}" if spec.get("schema") else str(spec["table"])
    sql = f"SELECT * FROM {name}"
    if spec.get("where"):
        sql += f" WHERE {spec['where']}"
    return sql


@register("sqlite", "postgres", "postgresql", "mssql", "snowflake", "redshift", "mysql", "sql")
def load_sql(spec: dict[str, Any]) -> pd.DataFrame:
    """Run a query (or table read) through SQLAlchemy and return a DataFrame."""
    if "url" not in spec:
        raise KeyError("sql connector requires 'url'")
    engine = get_engine(str(spec["url"]))
    with engine.connect() as conn:
        df = pd.read_sql_query(text(build_query(spec)), conn)
    if spec.get("lowercase_columns"):
        df.columns = [str(c).lower() for c in df.columns]
    return df
