"""Command line interface: ``python -m recon run | validate | history``."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from recon import connectors
from recon.config import ConfigError, load_suite
from recon.engine import ERROR, FAIL
from recon.runner import history, run_suite


def _cmd_run(args: argparse.Namespace) -> int:
    run = run_suite(args.suite, only=args.test, workers=args.workers, save=not args.no_save)
    print(run.summary())
    for r in run.results:
        for f in r.failures:
            print(f"  - {r.name}: {f}")
    if run.html_path:
        print(f"Report: {run.html_path}")
    if run.status == ERROR:
        return 1
    return 1 if run.status == FAIL and args.fail_on_mismatch else 0


def _cmd_validate(args: argparse.Namespace) -> int:
    try:
        suite = load_suite(args.suite)
    except (ConfigError, OSError, TypeError, ValueError) as exc:
        print(f"INVALID: {exc}")
        return 1
    problems: list[str] = []
    types = connectors.available_types()
    for t in suite.tests:
        for side in ("source", "target"):
            ctype = str(getattr(t, side).get("type", "")).lower()
            if ctype not in types:
                problems.append(f"{t.name}.{side}: unknown connector type '{ctype}'")
    for t in suite.tests:
        for var in set(re.findall(r"\$\{([A-Za-z_]\w*)\}", str((t.source, t.target)))):
            print(f"note: {t.name} requires environment variable {var}")
    for p in problems:
        print(f"INVALID: {p}")
    if not problems:
        print(f"OK: suite '{suite.suite}' with {len(suite.tests)} test(s)")
    return 1 if problems else 0


def _cmd_history(args: argparse.Namespace) -> int:
    df = history(limit=args.limit)
    cols = ["run_id", "suite", "started_at", "status", "tests_passed", "tests_failed", "tests_error"]
    print(df[cols].to_string(index=False) if len(df) else "No runs recorded yet.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Create the argument parser."""
    p = argparse.ArgumentParser(prog="recon", description="Data reconciliation framework")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run a suite")
    r.add_argument("suite", type=Path)
    r.add_argument("--test", action="append", help="only run this test (repeatable)")
    r.add_argument("--workers", type=int, default=4)
    r.add_argument("--no-save", action="store_true", help="do not write reports or history")
    r.add_argument("--fail-on-mismatch", action="store_true", help="exit 1 when any test is FAIL")
    r.set_defaults(func=_cmd_run)
    v = sub.add_parser("validate", help="validate a suite file")
    v.add_argument("suite", type=Path)
    v.set_defaults(func=_cmd_validate)
    h = sub.add_parser("history", help="list past runs")
    h.add_argument("--limit", type=int, default=20)
    h.set_defaults(func=_cmd_history)
    return p


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns the process exit code."""
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (ConfigError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
