"""Streamlit-free helpers for the UI (kept separate so they are unit-testable)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from recon import connectors
from recon.config import resolve_env

SQL_TYPES = ["sqlite", "postgres", "mssql", "snowflake", "redshift", "mysql"]
FILE_TYPES = ["csv", "tsv", "xlsx", "parquet", "sas7bdat", "fwf", "json", "jsonl"]
SAAS_TYPES = ["salesforce", "dynamodb"]
ALL_TYPES = SQL_TYPES + FILE_TYPES + SAAS_TYPES


def type_group(ctype: str) -> str:
    """'sql', 'file' or 'saas' for a connector type."""
    return "sql" if ctype in SQL_TYPES else "file" if ctype in FILE_TYPES else "saas"


def list_suite_files(directory: str | Path) -> list[str]:
    """YAML file names in a directory."""
    return sorted(p.name for p in Path(directory).glob("*") if p.suffix in (".yaml", ".yml"))


def load_preview(spec: dict[str, Any], n: int = 50) -> pd.DataFrame:
    """First ``n`` rows of a source (env placeholders resolved in memory only)."""
    return connectors.load(resolve_env(spec)).head(n)


def parse_list(text: str, cast: type = str) -> list[Any]:
    """'8, 8, 12' -> [8, 8, 12]."""
    return [cast(x.strip()) for x in text.split(",") if x.strip()]


def unmatched_columns(src_cols: list[str], tgt_cols: list[str]) -> tuple[list[str], list[str]]:
    """Columns that have no case-insensitive name match on the other side."""
    sl, tl = {c.lower() for c in src_cols}, {c.lower() for c in tgt_cols}
    return [c for c in src_cols if c.lower() not in tl], [c for c in tgt_cols if c.lower() not in sl]


def numeric_columns(df: pd.DataFrame) -> list[str]:
    """Names of numeric columns."""
    return [str(c) for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c])]
