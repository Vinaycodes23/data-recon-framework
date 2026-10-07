"""Benchmark aggregate checks: load-into-pandas vs. pushdown (DuckDB on parquet, SQL on SQLite).

Usage: python scripts/benchmark_pushdown.py [rows ...]     (default: 1000000 10000000)
Both paths must return the same aggregate values (asserted). Writes benchmark_pushdown_results.md.
Times are wall clock including file/database reads, median of 3 runs. SQLite is only run for the
first size (building a 10M-row SQLite file is slow and not the point).
"""

from __future__ import annotations

import platform
import shutil
import sqlite3
import statistics
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from recon import connectors  # noqa: E402
from recon.config import Aggregate, Options, TestConfig  # noqa: E402
from recon.engine import _check_aggregate  # noqa: E402
from recon.pushdown import pushdown_aggregates  # noqa: E402

AGGS = [Aggregate("amount", "sum", 1.0), Aggregate("id", "count_distinct"), Aggregate("qty", "max"),
        Aggregate("amount", "null_count")]


def make(n: int, seed: int = 3) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    src = pd.DataFrame({"id": np.arange(n), "amount": np.round(rng.uniform(1, 1000, n), 2),
                        "qty": rng.integers(1, 50, n), "region": rng.choice(["N", "S", "E", "W"], n)})
    tgt = src.copy()
    tgt.loc[0, "amount"] += 0.25
    return src, tgt


def timed(fn, repeats: int = 3):
    times, out = [], None
    for _ in range(repeats):
        t0 = time.perf_counter()
        out = fn()
        times.append(time.perf_counter() - t0)
    return statistics.median(times), out


def pandas_path(cfg: TestConfig):
    s, t = connectors.load(cfg.source), connectors.load(cfg.target)
    return [_check_aggregate(a, s, t, Options()) for a in cfg.aggregates]


def check_same(a: list[dict], b: list[dict]) -> None:
    for x, y in zip(a, b, strict=True):
        assert x["passed"] == y["passed"] and abs(float(x["source"]) - float(y["source"])) < 1e-6 * max(1.0, abs(float(x["source"]))), (x, y)


def main() -> None:
    sizes = [int(a) for a in sys.argv[1:]] or [1_000_000, 10_000_000]
    work = Path(tempfile.mkdtemp(prefix="recon_pushdown_"))
    rows = []
    try:
        for i, n in enumerate(sizes):
            src, tgt = make(n)
            src.to_parquet(work / f"s{n}.parquet")
            tgt.to_parquet(work / f"t{n}.parquet")
            cfgs = {"parquet": TestConfig(name="b", source={"type": "parquet", "path": str(work / f"s{n}.parquet")},
                                          target={"type": "parquet", "path": str(work / f"t{n}.parquet")}, keys=["id"], aggregates=AGGS)}
            if i == 0:
                s_url, t_url = f"sqlite:///{work / 's.db'}", f"sqlite:///{work / 't.db'}"
                for url, df in ((s_url, src), (t_url, tgt)):
                    with sqlite3.connect(url.removeprefix("sqlite:///")) as con:
                        df.to_sql("t", con, index=False)
                cfgs["sqlite"] = TestConfig(name="b", source={"type": "sqlite", "url": s_url, "table": "t"},
                                            target={"type": "sqlite", "url": t_url, "table": "t"}, keys=["id"], aggregates=AGGS)
            del src, tgt
            for kind, cfg in cfgs.items():
                tp, rp = timed(lambda c=cfg: pandas_path(c))
                td, rd = timed(lambda c=cfg: pushdown_aggregates(c))
                check_same(rp, rd)
                rows.append((kind, n, tp, td))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    lines = [f"Machine: {platform.platform()} / Python {platform.python_version()} / pandas {pd.__version__}", "",
             "| Source | Rows | Load into pandas + aggregate (s) | Pushdown (s) | Speedup |", "|---|---:|---:|---:|---:|"]
    for kind, n, tp, td in rows:
        engine = "DuckDB on parquet" if kind == "parquet" else "SQL on SQLite"
        lines.append(f"| {engine} | {n:,} | {tp:.2f} | {td:.3f} | {tp / td:.0f}x |")
    out = "\n".join(lines)
    Path("benchmark_pushdown_results.md").write_text(out + "\n")
    print(out)


if __name__ == "__main__":
    main()
