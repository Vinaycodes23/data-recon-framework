"""File connectors: csv, tsv, xlsx, parquet, sas7bdat, fwf, json/jsonl (local or ``s3://``)."""

from __future__ import annotations

from typing import Any

import pandas as pd

from recon.connectors.base import register


def _path(spec: dict[str, Any]) -> str:
    if "path" not in spec:
        raise KeyError("file connector requires 'path'")
    return str(spec["path"])


def _opts(spec: dict[str, Any]) -> dict[str, Any]:
    return dict(spec.get("read_options") or {})


@register("csv")
def load_csv(spec: dict[str, Any]) -> pd.DataFrame:
    """Read a CSV (``read_options`` are passed to ``pandas.read_csv``)."""
    return pd.read_csv(_path(spec), **_opts(spec))


@register("tsv")
def load_tsv(spec: dict[str, Any]) -> pd.DataFrame:
    """Read a tab-separated file."""
    opts = _opts(spec)
    opts.setdefault("sep", "\t")
    return pd.read_csv(_path(spec), **opts)


@register("xlsx", "excel")
def load_xlsx(spec: dict[str, Any]) -> pd.DataFrame:
    """Read an Excel sheet (``sheet_name`` in read_options)."""
    return pd.read_excel(_path(spec), **_opts(spec))


@register("parquet")
def load_parquet(spec: dict[str, Any]) -> pd.DataFrame:
    """Read a Parquet file."""
    return pd.read_parquet(_path(spec), **_opts(spec))


@register("sas7bdat", "sas")
def load_sas(spec: dict[str, Any]) -> pd.DataFrame:
    """Read a SAS7BDAT file."""
    opts = _opts(spec)
    opts.setdefault("format", "sas7bdat")
    return pd.read_sas(_path(spec), **opts)


@register("fwf")
def load_fwf(spec: dict[str, Any]) -> pd.DataFrame:
    """Read a fixed-width file as strings. Needs ``widths`` or ``colspecs`` and ``names``."""
    opts = _opts(spec)
    for k in ("widths", "colspecs", "names"):
        if k in spec:
            opts[k] = spec[k]
    if "widths" not in opts and "colspecs" not in opts:
        raise KeyError("fwf connector requires 'widths' or 'colspecs'")
    opts["dtype"] = str
    opts.setdefault("keep_default_na", False)
    if "colspecs" in opts:
        opts["colspecs"] = [tuple(c) for c in opts["colspecs"]]
    return pd.read_fwf(_path(spec), **opts)


@register("json")
def load_json(spec: dict[str, Any]) -> pd.DataFrame:
    """Read a JSON array file."""
    return pd.read_json(_path(spec), **_opts(spec))


@register("jsonl", "ndjson")
def load_jsonl(spec: dict[str, Any]) -> pd.DataFrame:
    """Read newline-delimited JSON."""
    opts = _opts(spec)
    opts["lines"] = True
    return pd.read_json(_path(spec), **opts)
