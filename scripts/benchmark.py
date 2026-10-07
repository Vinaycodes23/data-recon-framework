"""Measure reconciliation time for N-row tables (synthetic data, ~1% of rows differ).

Usage: python scripts/benchmark.py [rows ...]      (default: 100000 1000000)
Writes a markdown table to benchmark_results.md and prints it. Times are wall-clock for
``run_test`` on in-memory frames (connector I/O excluded), median of 3 runs.
"""

from __future__ import annotations

import platform
import resource
import statistics
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from recon.config import TestConfig  # noqa: E402
from recon.engine import run_test  # noqa: E402


def make_frames(n: int, seed: int = 7) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    """Source with 8 columns; target = source with 0.5% amount drifts, 0.5% status case changes, 0.1% rows dropped."""
    rng = np.random.default_rng(seed)
    src = pd.DataFrame({
        "id": np.arange(n),
        "amount": np.round(rng.uniform(1, 1000, n), 2),
        "qty": rng.integers(1, 50, n),
        "status": rng.choice(["Shipped", "Pending", "Cancelled"], n),
        "name": rng.choice([f"customer {i}" for i in range(5000)], n),
        "region": rng.choice(["N", "S", "E", "W"], n),
        "created": pd.Timestamp("2024-01-01") + pd.to_timedelta(rng.integers(0, 365, n), unit="D"),
        "score": rng.normal(size=n),
    })
    tgt = src.copy()
    drift = rng.choice(n, n // 200, replace=False)
    tgt.loc[drift, "amount"] += 5.0
    rest = np.setdiff1d(np.arange(n), drift)
    case = rng.choice(rest, n // 200, replace=False)
    tgt.loc[case, "status"] = tgt.loc[case, "status"].str.lower()
    dropped = rng.choice(np.setdiff1d(rest, case), n // 1000, replace=False)
    tgt = tgt.drop(index=dropped)
    return src, tgt, {"expected_mismatched": len(drift) + len(case), "expected_missing": len(dropped)}


def bench(n: int, repeats: int = 3) -> dict:
    """Time ``run_test`` for n rows and verify it found exactly the planted differences."""
    src, tgt, exp = make_frames(n)
    cfg = TestConfig(name="bench", source={"type": "x"}, target={"type": "x"}, keys=["id"], tolerance={"amount": 0.01})
    times, res = [], None
    for _ in range(repeats):
        t0 = time.perf_counter()
        res = run_test(cfg, src, tgt)
        times.append(time.perf_counter() - t0)
    assert res is not None and res.status == "FAIL", res.error if res else "no result"
    assert res.mismatched_rows == exp["expected_mismatched"] and res.missing_in_target == exp["expected_missing"], "engine disagreed with planted diffs"
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024 if sys.platform == "darwin" else 1024)
    return {"rows": n, "cols": src.shape[1], "median_s": statistics.median(times), "min_s": min(times),
            "rows_per_s": n / statistics.median(times), "mismatched": res.mismatched_rows, "missing": res.missing_in_target,
            "peak_rss_mb": rss}


def main() -> None:
    """Run the benchmark and write benchmark_results.md."""
    sizes = [int(a) for a in sys.argv[1:]] or [100_000, 1_000_000]
    rows = [bench(n) for n in sizes]
    lines = [
        f"Machine: {platform.platform()} / {platform.processor() or platform.machine()} / Python {platform.python_version()} / pandas {pd.__version__}",
        "",
        "| Rows | Columns | Median time (s) | Best (s) | Rows/sec | Mismatched rows found | Missing found | Peak RSS (MB, cumulative) |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(f"| {r['rows']:,} | {r['cols']} | {r['median_s']:.2f} | {r['min_s']:.2f} | {r['rows_per_s']:,.0f} | "
                     f"{r['mismatched']:,} | {r['missing']:,} | {r['peak_rss_mb']:.0f} |")
    out = "\n".join(lines)
    Path("benchmark_results.md").write_text(out + "\n")
    print(out)


if __name__ == "__main__":
    main()
