"""Comparison engine for one source-vs-target test.

Pipeline (``run_test`` -> ``compare_frames``):

1. **Align columns** - apply ``column_map`` (source name -> target name), match the rest
   case-insensitively, drop ``ignore_columns``.
2. **Schema check** - columns only in source / only in target, dtype and "kind"
   (numeric / string / datetime / boolean) per column.
3. **Normalise** every column pair to one common type: datetime if either side is a
   datetime and the other parses; numeric if every non-null value on both sides parses
   as a number (handles ``"1,234.50"`` and ``"00123.40"`` from fixed-width files);
   otherwise string with trim / case / empty-as-null options.
4. **Normalise keys** so ``1``, ``1.0`` and ``" 1 "`` join as the same key.
5. **Duplicate keys** on each side are counted and sampled, then the first row is kept.
6. **Outer join** on the keys: missing in target, missing in source, matched.
7. **Hash pre-filter** - each normalised matched row is hashed on both sides
   (``pd.util.hash_pandas_object``); equal hashes mean identical rows and are skipped.
8. **Column-level diff** only for rows whose hashes differ, with per-column numeric
   tolerance and null handling. Output is long-format: keys, column, source_value,
   target_value, difference.
9. **Aggregate checks** (sum, count, ...) over the full tables with tolerance.
10. **Verdict** - PASS / FAIL against thresholds with readable reasons. Any exception
    becomes status ERROR; ``run_test`` never raises.
"""

from __future__ import annotations

import datetime as dt
import math
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd

from recon import connectors
from recon.config import Aggregate, ConfigError, Options, TestConfig

PASS, FAIL, ERROR = "PASS", "FAIL", "ERROR"
SAMPLE_KEYS = ("mismatches", "missing_in_target", "missing_in_source", "duplicates_source", "duplicates_target")


# --------------------------------------------------------------------------- JSON helpers
def jsonable(v: Any) -> Any:
    """Convert numpy / pandas / datetime values into JSON-safe Python (NaN and NaT become None)."""
    if v is None or v is pd.NA or v is pd.NaT:
        return None
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [jsonable(x) for x in v]
    if isinstance(v, np.ndarray):
        return [jsonable(x) for x in v.tolist()]
    if isinstance(v, (pd.Timestamp, dt.datetime, dt.date)):
        return v.isoformat()
    if isinstance(v, (np.bool_, bool)):
        return bool(v)
    if isinstance(v, (np.integer, int)):
        return int(v)
    if isinstance(v, (np.floating, float, Decimal)):
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return str(v) if not isinstance(v, str) else v


def frame_to_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    """DataFrame -> list of JSON-safe dicts."""
    return [{str(c): jsonable(v) for c, v in row.items()} for row in df.to_dict("records")]


# --------------------------------------------------------------------------- result
@dataclass
class TestResult:
    """Outcome of one test. ``samples`` holds DataFrames for the UI/report."""

    __test__ = False  # not a pytest class

    name: str
    status: str = PASS
    source_desc: str = ""
    target_desc: str = ""
    source_rows: int = 0
    target_rows: int = 0
    matched_keys: int = 0
    identical_rows: int = 0
    mismatched_rows: int = 0
    missing_in_target: int = 0
    missing_in_source: int = 0
    duplicate_keys_source: int = 0
    duplicate_keys_target: int = 0
    null_keys_source: int = 0
    null_keys_target: int = 0
    mismatches_by_column: dict[str, int] = field(default_factory=dict)
    aggregates: list[dict[str, Any]] = field(default_factory=list)
    schema: dict[str, Any] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    error: str | None = None
    duration_sec: float = 0.0
    started_at: str = ""
    keys: list[str] = field(default_factory=list)
    samples: dict[str, pd.DataFrame] = field(default_factory=dict)

    @property
    def source_keys(self) -> int:
        """Unique, non-null source keys (the denominator for all percentages)."""
        return self.matched_keys + self.missing_in_target

    @property
    def match_rate(self) -> float:
        """Identical rows as % of unique source keys (100.0 when both sides are empty)."""
        if self.source_keys == 0:
            return 100.0 if self.missing_in_source == 0 else 0.0
        return round(100.0 * self.identical_rows / self.source_keys, 4)

    @property
    def missing_pct(self) -> float:
        """Source keys absent from target, as % of source keys."""
        return round(100.0 * self.missing_in_target / self.source_keys, 4) if self.source_keys else 0.0

    @property
    def extra_pct(self) -> float:
        """Target-only keys, as % of source keys."""
        if self.source_keys:
            return round(100.0 * self.missing_in_source / self.source_keys, 4)
        return 100.0 if self.missing_in_source else 0.0

    @property
    def mismatch_pct(self) -> float:
        """Matched rows with at least one differing column, as % of source keys."""
        return round(100.0 * self.mismatched_rows / self.source_keys, 4) if self.source_keys else 0.0

    def summary(self) -> str:
        """One-line human summary."""
        if self.status == ERROR:
            return f"[ERROR] {self.name}: {self.error}"
        return (
            f"[{self.status}] {self.name}: match {self.match_rate:.2f}% | "
            f"src={self.source_rows} tgt={self.target_rows} | missing={self.missing_in_target} "
            f"extra={self.missing_in_source} | diff_rows={self.mismatched_rows} | "
            f"dups={self.duplicate_keys_source}/{self.duplicate_keys_target}"
        )

    def to_dict(self, include_samples: bool = True) -> dict[str, Any]:
        """JSON-safe dict (timestamps to ISO, NaN to null)."""
        d: dict[str, Any] = {
            "name": self.name,
            "status": self.status,
            "source_desc": self.source_desc,
            "target_desc": self.target_desc,
            "keys": list(self.keys),
            "source_rows": self.source_rows,
            "target_rows": self.target_rows,
            "matched_keys": self.matched_keys,
            "identical_rows": self.identical_rows,
            "mismatched_rows": self.mismatched_rows,
            "missing_in_target": self.missing_in_target,
            "missing_in_source": self.missing_in_source,
            "duplicate_keys_source": self.duplicate_keys_source,
            "duplicate_keys_target": self.duplicate_keys_target,
            "null_keys_source": self.null_keys_source,
            "null_keys_target": self.null_keys_target,
            "match_rate": self.match_rate,
            "missing_pct": self.missing_pct,
            "extra_pct": self.extra_pct,
            "mismatch_pct": self.mismatch_pct,
            "mismatches_by_column": jsonable(self.mismatches_by_column),
            "aggregates": jsonable(self.aggregates),
            "schema": jsonable(self.schema),
            "failures": list(self.failures),
            "error": self.error,
            "duration_sec": round(self.duration_sec, 4),
            "started_at": self.started_at,
        }
        if include_samples:
            d["samples"] = {k: frame_to_records(v) for k, v in self.samples.items()}
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TestResult:
        """Rebuild a result from ``to_dict`` output (e.g. a saved JSON report)."""
        names = set(cls.__dataclass_fields__)
        kwargs = {k: v for k, v in d.items() if k in names and k != "samples"}
        res = cls(**kwargs)
        res.samples = {k: pd.DataFrame(v) for k, v in (d.get("samples") or {}).items()}
        return res


# --------------------------------------------------------------------------- type helpers
def kind_of(dtype: Any) -> str:
    """Coarse kind of a pandas dtype: numeric / string / datetime / boolean."""
    if pd.api.types.is_bool_dtype(dtype):
        return "boolean"
    if pd.api.types.is_datetime64_any_dtype(dtype):
        return "datetime"
    if pd.api.types.is_numeric_dtype(dtype):
        return "numeric"
    return "string"


def _as_string(s: pd.Series) -> pd.Series:
    """Series -> pandas 'string' dtype (NA for nulls), tolerant of exotic objects."""
    try:
        return s.astype("string")
    except (TypeError, ValueError):
        return s.map(lambda v: pd.NA if _isnull(v) else str(v)).astype("string")


def _isnull(v: Any) -> bool:
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def _clean_strings(s: pd.Series, trim: bool, lower: bool, empty_null: bool) -> pd.Series:
    out = _as_string(s)
    if trim:
        out = out.str.strip()
    if lower:
        out = out.str.lower()
    if empty_null:
        out = out.mask(out.str.strip() == "", pd.NA)
    return out


def _to_numeric(s: pd.Series, empty_null: bool) -> pd.Series | None:
    """Float64 series if every non-null value parses as a number, else None."""
    if pd.api.types.is_bool_dtype(s.dtype):
        return None
    if pd.api.types.is_numeric_dtype(s.dtype):
        return s.astype("float64")
    if pd.api.types.is_datetime64_any_dtype(s.dtype):
        return None
    st = _clean_strings(s, trim=True, lower=False, empty_null=empty_null)
    num = pd.to_numeric(st.str.replace(",", "", regex=False), errors="coerce")
    if int(num.notna().sum()) != int(st.notna().sum()):
        return None
    return num.astype("float64")


def _to_datetime(s: pd.Series, empty_null: bool) -> pd.Series | None:
    """tz-naive datetime64[ns] series if every non-null value parses, else None."""
    if pd.api.types.is_datetime64_any_dtype(s.dtype):
        parsed = s
        raw_nonnull = int(s.notna().sum())
    else:
        st = _clean_strings(s, trim=True, lower=False, empty_null=empty_null)
        raw_nonnull = int(st.notna().sum())
        try:
            parsed = pd.to_datetime(st.astype(object), errors="coerce", utc=True)
        except (ValueError, TypeError):
            return None
    if int(parsed.notna().sum()) != raw_nonnull:
        return None
    if getattr(parsed.dt, "tz", None) is not None:
        parsed = parsed.dt.tz_convert("UTC").dt.tz_localize(None)
    return parsed.astype("datetime64[ns]")


def normalise_pair(s: pd.Series, t: pd.Series, opts: Options) -> tuple[pd.Series, pd.Series, str]:
    """Normalise two raw series to a common type. Returns ``(s_norm, t_norm, kind)``."""
    s_dt = pd.api.types.is_datetime64_any_dtype(s.dtype)
    t_dt = pd.api.types.is_datetime64_any_dtype(t.dtype)
    if s_dt or t_dt:
        sd, td = _to_datetime(s, opts.empty_string_as_null), _to_datetime(t, opts.empty_string_as_null)
        if sd is not None and td is not None:
            return sd, td, "datetime"
    if pd.api.types.is_bool_dtype(s.dtype) and pd.api.types.is_bool_dtype(t.dtype):
        return s.astype("boolean"), t.astype("boolean"), "boolean"
    sn, tn = _to_numeric(s, opts.empty_string_as_null), _to_numeric(t, opts.empty_string_as_null)
    if sn is not None and tn is not None:
        return sn + 0.0, tn + 0.0, "numeric"  # + 0.0 folds -0.0 into 0.0 for hashing
    args = (opts.trim_strings, opts.case_insensitive, opts.empty_string_as_null)
    return _clean_strings(s, *args), _clean_strings(t, *args), "string"


def normalise_keys(s: pd.Series, t: pd.Series, case_insensitive: bool) -> tuple[pd.Series, pd.Series]:
    """Normalise one key column pair to join-safe strings (1, 1.0 and ' 1 ' all become '1')."""
    sn, tn = _to_numeric(s, True), _to_numeric(t, True)
    if sn is not None and tn is not None:
        both = pd.concat([sn.dropna(), tn.dropna()])
        integral = bool(((both % 1 == 0) & (both.abs() < 1e15)).all())

        def fmt(n: pd.Series) -> pd.Series:
            if integral:
                return n.round().astype("Int64").astype("string")
            return n.map(lambda v: pd.NA if pd.isna(v) else repr(float(v))).astype("string")

        return fmt(sn), fmt(tn)
    return (
        _clean_strings(s, True, case_insensitive, True),
        _clean_strings(t, True, case_insensitive, True),
    )


# --------------------------------------------------------------------------- alignment
def _find(columns: Any, name: str) -> str | None:
    low = str(name).lower()
    for c in columns:
        if str(c).lower() == low:
            return str(c)
    return None


def _align(src: pd.DataFrame, tgt: pd.DataFrame, cfg: TestConfig) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, str]]:
    """Rename source columns via column_map and target columns to the matching source name."""
    src, tgt = src.copy(), tgt.copy()
    src.columns, tgt.columns = [str(c) for c in src.columns], [str(c) for c in tgt.columns]
    cmap = {str(k).lower(): str(v) for k, v in cfg.column_map.items()}
    renames = {c: cmap[c.lower()] for c in src.columns if c.lower() in cmap}
    src = src.rename(columns=renames)
    t_ren = {}
    for c in tgt.columns:
        match = _find(src.columns, c)
        if match and match != c:
            t_ren[c] = match
    tgt = tgt.rename(columns=t_ren)
    for side, df in (("source", src), ("target", tgt)):
        dupes = sorted({c for c in df.columns if list(df.columns).count(c) > 1})
        if dupes:
            raise ValueError(f"Duplicate column names on {side} after alignment: {dupes}")
    return src, tgt, renames


def _resolve_name(name: str, src: pd.DataFrame, cfg: TestConfig, renames: dict[str, str]) -> str | None:
    """Resolve a user-supplied column name to its aligned (source-side) name."""
    hit = _find(src.columns, name)
    if hit:
        return hit
    for orig, mapped in renames.items():
        if orig.lower() == str(name).lower():
            return mapped
    return None


# --------------------------------------------------------------------------- aggregates
def _agg_value(s: pd.Series, func: str, empty_null: bool) -> Any:
    num = _to_numeric(s, empty_null)
    if func == "null_count":
        return int(_clean_strings(s, True, False, empty_null).isna().sum())
    if func == "count":
        return int(_clean_strings(s, True, False, empty_null).notna().sum())
    if func == "count_distinct":
        base = num if num is not None else _clean_strings(s, True, False, empty_null)
        return int(base.nunique(dropna=True))
    if func in ("min", "max"):
        base = num if num is not None else _clean_strings(s, True, False, empty_null)
        base = base.dropna()
        if base.empty:
            return None
        return getattr(base, func)()
    if num is None:
        raise ValueError(f"'{func}' needs a numeric column")
    return float(num.sum()) if func == "sum" else (float(num.mean()) if num.notna().any() else None)


def _check_aggregate(agg: Aggregate, src: pd.DataFrame, tgt: pd.DataFrame, opts: Options) -> dict[str, Any]:
    out: dict[str, Any] = {
        "column": agg.column, "func": agg.func, "tolerance": agg.tolerance,
        "source": None, "target": None, "difference": None, "passed": False, "error": None,
    }
    sc, tc = _find(src.columns, agg.column), _find(tgt.columns, agg.column)
    if sc is None or tc is None:
        out["error"] = f"column '{agg.column}' missing on " + ("source" if sc is None else "target")
        return out
    try:
        sv = _agg_value(src[sc], agg.func, opts.empty_string_as_null)
        tv = _agg_value(tgt[tc], agg.func, opts.empty_string_as_null)
    except ValueError as exc:
        out["error"] = str(exc)
        return out
    out["source"], out["target"] = jsonable(sv), jsonable(tv)
    if sv is None or tv is None:
        out["passed"] = sv is None and tv is None
    elif isinstance(sv, (int, float, np.number)) and isinstance(tv, (int, float, np.number)):
        diff = float(tv) - float(sv)
        out["difference"] = diff
        out["passed"] = abs(diff) <= agg.tolerance * (1 + 1e-9) + 4 * np.finfo(float).eps * max(abs(float(sv)), abs(float(tv)))
    else:
        out["passed"] = sv == tv
    return out


# --------------------------------------------------------------------------- core
def _values_differ(sv: pd.Series, tv: pd.Series, kind: str, tol: float, null_equals_null: bool) -> np.ndarray:
    """Boolean array: True where the (already normalised) values differ."""
    s_null, t_null = sv.isna().to_numpy(), tv.isna().to_numpy()
    both_null, either_null = s_null & t_null, s_null | t_null
    eq = np.zeros(len(sv), dtype=bool)
    ok = ~either_null
    if kind == "numeric":
        a, b = sv.to_numpy(dtype="float64", na_value=np.nan), tv.to_numpy(dtype="float64", na_value=np.nan)
        with np.errstate(invalid="ignore"):
            diff = np.abs(a - b)
            limit = tol * (1 + 1e-9) + 4 * np.finfo(float).eps * np.maximum(np.abs(a), np.abs(b))
            close = (diff <= limit) | (a == b)  # a == b also covers equal infinities
        eq[ok] = close[ok]
    else:
        a, b = sv.to_numpy(dtype=object, na_value=None), tv.to_numpy(dtype=object, na_value=None)
        eq[ok] = np.array([x == y for x, y in zip(a[ok], b[ok], strict=True)], dtype=bool) if ok.any() else []
    eq[both_null] = null_equals_null
    return ~eq


def _empty_samples() -> dict[str, pd.DataFrame]:
    return {k: pd.DataFrame() for k in SAMPLE_KEYS}


def compare_frames(cfg: TestConfig, source: pd.DataFrame, target: pd.DataFrame) -> TestResult:
    """Run the full pipeline on two already-loaded frames. May raise; ``run_test`` wraps it."""
    opts, cap = cfg.options, max(int(cfg.options.max_mismatch_samples), 0)
    res = TestResult(name=cfg.name, source_rows=len(source), target_rows=len(target), samples=_empty_samples())

    # 1. align -------------------------------------------------------------------------
    src, tgt, renames = _align(source, target, cfg)
    ignored = {c.lower() for c in cfg.ignore_columns}
    ignored |= {m.lower() for o, m in renames.items() if o.lower() in ignored}

    key_names: list[str] = []
    for k in cfg.keys:
        name = _resolve_name(k, src, cfg, renames)
        if name is None or _find(tgt.columns, name) is None:
            side = "source" if name is None else "target"
            raise ConfigError(f"Key column '{k}' not found on {side}")
        key_names.append(name)
    res.keys = key_names

    # 2. schema ------------------------------------------------------------------------
    s_cols = [c for c in src.columns if c.lower() not in ignored]
    t_cols = [c for c in tgt.columns if c.lower() not in ignored]
    only_s = [c for c in s_cols if _find(t_cols, c) is None]
    only_t = [c for c in t_cols if _find(s_cols, c) is None]
    compare_cols = [c for c in s_cols if c in set(t_cols) and c not in key_names]
    schema_cols = []
    for c in s_cols:
        if c in set(t_cols):
            sk, tk = kind_of(src[c].dtype), kind_of(tgt[c].dtype)
            schema_cols.append({
                "column": c, "source_dtype": str(src[c].dtype), "target_dtype": str(tgt[c].dtype),
                "source_kind": sk, "target_kind": tk, "kind_match": sk == tk,
            })
    res.schema = {
        "only_in_source": only_s,
        "only_in_target": only_t,
        "ignored": sorted(c for c in set(src.columns) | set(tgt.columns) if c.lower() in ignored),
        "compared_columns": compare_cols,
        "columns": schema_cols,
    }

    # 4. key normalisation -------------------------------------------------------------
    kcols = [f"_k{i}" for i in range(len(key_names))]
    s_keys, t_keys = pd.DataFrame(index=range(len(src))), pd.DataFrame(index=range(len(tgt)))
    for kc, name in zip(kcols, key_names, strict=True):
        s_keys[kc], t_keys[kc] = normalise_keys(
            src[name].reset_index(drop=True), tgt[name].reset_index(drop=True), opts.case_insensitive
        )
    s_keys["_pos"], t_keys["_pos"] = np.arange(len(src)), np.arange(len(tgt))

    s_null, t_null = s_keys[kcols].isna().any(axis=1), t_keys[kcols].isna().any(axis=1)
    res.null_keys_source, res.null_keys_target = int(s_null.sum()), int(t_null.sum())
    s_keys, t_keys = s_keys[~s_null], t_keys[~t_null]

    # 5. duplicates --------------------------------------------------------------------
    raw_s, raw_t = src.reset_index(drop=True), tgt.reset_index(drop=True)
    for side, keys_df, raw in (("source", s_keys, raw_s), ("target", t_keys, raw_t)):
        dup_all = keys_df.duplicated(kcols, keep=False)
        n_dup = int(keys_df[dup_all].drop_duplicates(kcols).shape[0])
        setattr(res, f"duplicate_keys_{side}", n_dup)
        if n_dup:
            pos = keys_df.loc[dup_all].sort_values(kcols)["_pos"].to_numpy()[:cap]
            res.samples[f"duplicates_{side}"] = raw.iloc[pos].reset_index(drop=True)
    s_u, t_u = s_keys.drop_duplicates(kcols, keep="first"), t_keys.drop_duplicates(kcols, keep="first")

    # 6. outer join --------------------------------------------------------------------
    merged = s_u.rename(columns={"_pos": "_si"}).merge(
        t_u.rename(columns={"_pos": "_ti"}), on=kcols, how="outer", indicator=True
    )
    left, right, both = (merged[merged["_merge"] == m] for m in ("left_only", "right_only", "both"))
    res.missing_in_target, res.missing_in_source, res.matched_keys = len(left), len(right), len(both)
    if len(left):
        res.samples["missing_in_target"] = raw_s.iloc[left["_si"].astype(int).to_numpy()[:cap]].reset_index(drop=True)
    if len(right):
        res.samples["missing_in_source"] = raw_t.iloc[right["_ti"].astype(int).to_numpy()[:cap]].reset_index(drop=True)
    si, ti = both["_si"].astype(int).to_numpy(), both["_ti"].astype(int).to_numpy()

    # 3 + 7. normalise matched columns and hash pre-filter ------------------------------
    norm_s: dict[str, pd.Series] = {}
    norm_t: dict[str, pd.Series] = {}
    kinds: dict[str, str] = {}
    for c in compare_cols:
        ns, nt, kinds[c] = normalise_pair(raw_s[c], raw_t[c], opts)
        norm_s[c] = ns.iloc[si].reset_index(drop=True)
        norm_t[c] = nt.iloc[ti].reset_index(drop=True)
    n_matched = len(si)
    if compare_cols and n_matched:
        fs, ft = pd.DataFrame(norm_s), pd.DataFrame(norm_t)
        differs = pd.util.hash_pandas_object(fs, index=False).to_numpy() != pd.util.hash_pandas_object(ft, index=False).to_numpy()
        if not opts.null_equals_null:
            differs |= (fs.isna().any(axis=1) | ft.isna().any(axis=1)).to_numpy()
        idx = np.nonzero(differs)[0]
    else:
        idx = np.array([], dtype=int)

    # 8. column-level diff on rows whose hash differs -------------------------------------
    by_col: dict[str, int] = {}
    row_mismatch = np.zeros(len(idx), dtype=bool)
    sample_parts: list[pd.DataFrame] = []
    budget = cap
    tol_map = {k.lower(): v for k, v in cfg.tolerance.items()}
    for c in compare_cols if len(idx) else []:
        tol = tol_map.get(c.lower(), 0.0)
        sv, tv = norm_s[c].iloc[idx].reset_index(drop=True), norm_t[c].iloc[idx].reset_index(drop=True)
        bad = _values_differ(sv, tv, kinds[c], tol, opts.null_equals_null)
        n_bad = int(bad.sum())
        if not n_bad:
            continue
        by_col[c] = n_bad
        row_mismatch |= bad
        take = np.nonzero(bad)[0][:budget]
        if len(take):
            budget -= len(take)
            g = idx[take]
            part = raw_s[key_names].iloc[si[g]].reset_index(drop=True).astype(object)
            part["column"] = c
            part["source_value"] = raw_s[c].iloc[si[g]].astype(object).reset_index(drop=True)
            part["target_value"] = raw_t[c].iloc[ti[g]].astype(object).reset_index(drop=True)
            diff = (tv.iloc[take].to_numpy(dtype="float64", na_value=np.nan) - sv.iloc[take].to_numpy(dtype="float64", na_value=np.nan)
                    ) if kinds[c] == "numeric" else np.full(len(take), np.nan)
            part["difference"] = diff
            sample_parts.append(part)
    if sample_parts:
        res.samples["mismatches"] = pd.concat(sample_parts, ignore_index=True)
    res.mismatches_by_column = dict(sorted(by_col.items(), key=lambda kv: -kv[1]))
    res.mismatched_rows = int(row_mismatch.sum())
    res.identical_rows = res.matched_keys - res.mismatched_rows

    # 9. aggregates -------------------------------------------------------------------------
    res.aggregates = [_check_aggregate(a, src, tgt, opts) for a in cfg.aggregates]

    # 10. verdict -----------------------------------------------------------------------------
    th, f = cfg.thresholds, res.failures
    if res.missing_pct > th.max_missing_pct:
        f.append(f"{res.missing_in_target} source rows missing in target ({res.missing_pct:.2f}% > {th.max_missing_pct}%)")
    if res.extra_pct > th.max_missing_pct:
        f.append(f"{res.missing_in_source} extra rows in target not in source ({res.extra_pct:.2f}% > {th.max_missing_pct}%)")
    if res.mismatch_pct > th.max_mismatch_pct:
        f.append(f"{res.mismatched_rows} rows have value mismatches ({res.mismatch_pct:.2f}% > {th.max_mismatch_pct}%)")
    if not th.allow_duplicate_keys and (res.duplicate_keys_source or res.duplicate_keys_target):
        f.append(f"duplicate keys: {res.duplicate_keys_source} in source, {res.duplicate_keys_target} in target")
    if res.null_keys_source or res.null_keys_target:
        f.append(f"null keys: {res.null_keys_source} in source, {res.null_keys_target} in target")
    for a in res.aggregates:
        if not a["passed"]:
            detail = a["error"] or f"source={a['source']} target={a['target']} (tolerance {a['tolerance']})"
            f.append(f"aggregate {a['func']}({a['column']}) failed: {detail}")
    if th.allow_schema_drift is False and (only_s or only_t):
        f.append(f"schema drift: only in source {only_s}, only in target {only_t}")
    res.status = FAIL if f else PASS
    return res


def run_test(cfg: TestConfig, source_df: pd.DataFrame | None = None, target_df: pd.DataFrame | None = None) -> TestResult:
    """Load both sides (unless frames are supplied) and compare. Never raises."""
    started = dt.datetime.now(dt.UTC)
    t0 = time.perf_counter()
    res = TestResult(name=cfg.name, started_at=started.isoformat(), samples=_empty_samples())
    try:
        try:
            r = cfg.resolved()
        except ConfigError:
            r = cfg
        res.source_desc, res.target_desc = connectors.describe(r.source), connectors.describe(r.target)
        src = source_df if source_df is not None else connectors.load(r.source)
        tgt = target_df if target_df is not None else connectors.load(r.target)
        out = compare_frames(r, src, tgt)
        out.source_desc, out.target_desc, out.started_at = res.source_desc, res.target_desc, res.started_at
        res = out
    except Exception as exc:  # noqa: BLE001 - run_test must never raise
        res.status, res.error = ERROR, f"{type(exc).__name__}: {exc}"
        res.failures = [res.error]
    res.duration_sec = time.perf_counter() - t0
    return res
