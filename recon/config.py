"""Suite configuration: dataclasses, YAML load/save and ``${ENV_VAR}`` resolution.

Secrets never live in YAML. Values may contain ``${VAR}`` or ``${VAR:-default}``
placeholders which are resolved at run time. ``load_suite(path, resolve=False)``
keeps placeholders intact so an editor (the Streamlit app) can never write a
resolved secret back to disk.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

AGG_FUNCS = ("sum", "count", "count_distinct", "min", "max", "mean", "null_count")

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class ConfigError(ValueError):
    """Raised for an invalid suite definition."""


def resolve_env(value: Any, env: dict[str, str] | None = None) -> Any:
    """Recursively replace ``${VAR}`` / ``${VAR:-default}`` in strings, lists and dicts.

    A missing variable without a default raises ``ConfigError``.
    """
    environ = os.environ if env is None else env
    if isinstance(value, str):

        def repl(m: re.Match[str]) -> str:
            name, default = m.group(1), m.group(2)
            if name in environ:
                return environ[name]
            if default is not None:
                return default
            raise ConfigError(f"Environment variable '{name}' is not set and has no default")

        return _ENV_RE.sub(repl, value)
    if isinstance(value, list):
        return [resolve_env(v, env) for v in value]
    if isinstance(value, dict):
        return {k: resolve_env(v, env) for k, v in value.items()}
    return value


@dataclass
class Aggregate:
    """An aggregate to compare between source and target."""

    column: str
    func: str = "sum"
    tolerance: float = 0.0

    def __post_init__(self) -> None:
        if self.func not in AGG_FUNCS:
            raise ConfigError(f"Invalid aggregate func '{self.func}'. Allowed: {', '.join(AGG_FUNCS)}")
        self.tolerance = float(self.tolerance or 0.0)


@dataclass
class Options:
    """Normalisation and comparison options."""

    trim_strings: bool = True
    case_insensitive: bool = False
    null_equals_null: bool = True
    empty_string_as_null: bool = True
    max_mismatch_samples: int = 500


@dataclass
class Thresholds:
    """Pass/fail thresholds. Percentages are of source rows."""

    max_missing_pct: float = 0.0
    max_mismatch_pct: float = 0.0
    allow_duplicate_keys: bool = False
    allow_schema_drift: bool = True


@dataclass
class TestConfig:
    """One source-vs-target comparison."""

    __test__ = False  # not a pytest class

    name: str
    source: dict[str, Any]
    target: dict[str, Any]
    keys: list[str]
    column_map: dict[str, str] = field(default_factory=dict)
    ignore_columns: list[str] = field(default_factory=list)
    tolerance: dict[str, float] = field(default_factory=dict)
    options: Options = field(default_factory=Options)
    aggregates: list[Aggregate] = field(default_factory=list)
    thresholds: Thresholds = field(default_factory=Thresholds)
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.name:
            raise ConfigError("Test requires a name")
        if isinstance(self.keys, str):
            self.keys = [self.keys]
        if not self.keys:
            raise ConfigError(f"Test '{self.name}' requires at least one key column")
        if isinstance(self.options, dict):
            self.options = Options(**self.options)
        if isinstance(self.thresholds, dict):
            self.thresholds = Thresholds(**self.thresholds)
        self.aggregates = [Aggregate(**a) if isinstance(a, dict) else a for a in self.aggregates]
        self.tolerance = {k: float(v) for k, v in (self.tolerance or {}).items()}

    def resolved(self, env: dict[str, str] | None = None) -> TestConfig:
        """Return a copy with ``${VAR}`` placeholders resolved in source/target."""
        data = dump_test(self)
        data["source"] = resolve_env(data["source"], env)
        data["target"] = resolve_env(data["target"], env)
        return parse_test(data)


@dataclass
class Suite:
    """A named collection of tests."""

    suite: str
    tests: list[TestConfig] = field(default_factory=list)
    description: str = ""

    def __post_init__(self) -> None:
        names = [t.name for t in self.tests]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ConfigError(f"Duplicate test names: {sorted(dupes)}")

    def enabled_tests(self, only: list[str] | None = None) -> list[TestConfig]:
        """Enabled tests, optionally restricted to the names in ``only``."""
        tests = [t for t in self.tests if t.enabled]
        if only:
            unknown = set(only) - {t.name for t in self.tests}
            if unknown:
                raise ConfigError(f"Unknown test(s): {sorted(unknown)}")
            tests = [t for t in self.tests if t.name in only]
        return tests


def parse_test(d: dict[str, Any]) -> TestConfig:
    """Build a ``TestConfig`` from a plain dict (as found in YAML)."""
    d = dict(d)
    for req in ("name", "source", "target", "keys"):
        if req not in d:
            raise ConfigError(f"Test is missing required field '{req}'")
    return TestConfig(**d)


def dump_test(t: TestConfig) -> dict[str, Any]:
    """Serialise a test, omitting defaults for readable YAML."""
    d = asdict(t)
    default = TestConfig(name="x", source={}, target={}, keys=["k"])
    out: dict[str, Any] = {k: d[k] for k in ("name", "source", "target", "keys")}
    for k in ("column_map", "ignore_columns", "tolerance", "aggregates"):
        if d[k]:
            out[k] = d[k]
    if d["options"] != asdict(default.options):
        out["options"] = d["options"]
    if d["thresholds"] != asdict(default.thresholds):
        out["thresholds"] = d["thresholds"]
    if not t.enabled:
        out["enabled"] = False
    return out




def suite_from_dict(d: dict[str, Any]) -> Suite:
    """Build a ``Suite`` from a plain dict."""
    if "suite" not in d:
        raise ConfigError("Suite file must define 'suite' (its name)")
    return Suite(
        suite=d["suite"],
        description=d.get("description", ""),
        tests=[parse_test(t) for t in d.get("tests") or []],
    )


def suite_to_dict(s: Suite) -> dict[str, Any]:
    """Serialise a suite to a plain dict."""
    return {"suite": s.suite, "description": s.description, "tests": [dump_test(t) for t in s.tests]}


def load_suite(path: str | Path, resolve: bool = False, env: dict[str, str] | None = None) -> Suite:
    """Load a suite from YAML.

    ``resolve=False`` (default) leaves ``${VAR}`` placeholders untouched, which is what
    editors need. The runner resolves per test at execution time.
    """
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    suite = suite_from_dict(raw)
    if resolve:
        suite.tests = [t.resolved(env) for t in suite.tests]
    return suite


def save_suite(suite: Suite, path: str | Path) -> None:
    """Write a suite to YAML."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(suite_yaml(suite))


def suite_yaml(suite: Suite) -> str:
    """Render a suite as YAML text."""
    return yaml.safe_dump(suite_to_dict(suite), sort_keys=False, default_flow_style=False)
