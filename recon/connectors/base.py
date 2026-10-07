"""Connector registry.

A connector is a function ``dict -> DataFrame`` registered under one or more type names::

    @register("csv", "tsv")
    def load_csv(spec: dict) -> pd.DataFrame: ...

Adding a new data source is one decorated function.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import pandas as pd

Loader = Callable[[dict[str, Any]], pd.DataFrame]
_REGISTRY: dict[str, Loader] = {}


class ConnectorError(RuntimeError):
    """Raised when a connector is unknown or fails to load."""


def register(*names: str) -> Callable[[Loader], Loader]:
    """Decorator registering a loader under the given type names."""

    def deco(fn: Loader) -> Loader:
        for n in names:
            _REGISTRY[n.lower()] = fn
        return fn

    return deco


def available_types() -> list[str]:
    """Sorted list of registered connector type names."""
    return sorted(_REGISTRY)


def load(spec: dict[str, Any]) -> pd.DataFrame:
    """Load a DataFrame from a connector spec (``{"type": ..., ...}``)."""
    ctype = str(spec.get("type", "")).lower()
    if ctype not in _REGISTRY:
        raise ConnectorError(f"Unknown connector type '{ctype}'. Available: {', '.join(available_types())}")
    try:
        return _REGISTRY[ctype](spec)
    except ConnectorError:
        raise
    except Exception as exc:
        raise ConnectorError(f"{describe(spec)} failed: {type(exc).__name__}: {exc}") from exc


_URL_PW = re.compile(r"(://[^:/@\s]+:)([^@/\s]+)(@)")
_SECRET_KEYS = ("password", "token", "secret", "security_token", "key")


def mask_url(url: str) -> str:
    """Mask the password in a SQLAlchemy/URL style string."""
    return _URL_PW.sub(r"\1****\3", url)


def describe(spec: dict[str, Any]) -> str:
    """One-line human description of a spec with secrets masked."""
    ctype = spec.get("type", "?")
    if "url" in spec:
        where = mask_url(str(spec["url"]))
        what = spec.get("query") or spec.get("table") or ""
        what = " ".join(str(what).split())
        return f"{ctype}: {where}" + (f" [{what[:80]}]" if what else "")
    if "path" in spec:
        return f"{ctype}: {spec['path']}"
    extras = {
        k: ("****" if any(s in k.lower() for s in _SECRET_KEYS) else v)
        for k, v in spec.items()
        if k != "type" and not isinstance(v, (dict, list))
    }
    return f"{ctype}: " + ", ".join(f"{k}={v}" for k, v in extras.items())
