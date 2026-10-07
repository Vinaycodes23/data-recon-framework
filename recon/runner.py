"""Run a suite (tests in parallel), write JSON + HTML reports and persist history.

History lives in a SQLAlchemy database (``RECON_HISTORY_URL``, default
``sqlite:///reports/recon_history.db``) with tables ``recon_runs`` and ``recon_test_results``.
Reports go to ``RECON_REPORTS_DIR`` (default ``reports``).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import (
    Column, Engine, Float, Integer, MetaData, String, Table, Text, create_engine, insert, select,
)

from recon.config import Suite, load_suite
from recon.engine import ERROR, FAIL, PASS, TestResult, run_test
from recon.report import render_html

metadata = MetaData()
runs_table = Table(
    "recon_runs", metadata,
    Column("run_id", String(32), primary_key=True),
    Column("suite", String(200), nullable=False),
    Column("description", Text),
    Column("started_at", String(40), nullable=False),
    Column("status", String(10), nullable=False),
    Column("tests_total", Integer), Column("tests_passed", Integer),
    Column("tests_failed", Integer), Column("tests_error", Integer),
    Column("duration_sec", Float),
    Column("json_path", Text), Column("html_path", Text),
)
results_table = Table(
    "recon_test_results", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String(32), nullable=False, index=True),
    Column("suite", String(200)), Column("test_name", String(200), nullable=False),
    Column("started_at", String(40)), Column("status", String(10), nullable=False),
    Column("source_rows", Integer), Column("target_rows", Integer),
    Column("matched_keys", Integer), Column("identical_rows", Integer),
    Column("mismatched_rows", Integer), Column("missing_in_target", Integer),
    Column("missing_in_source", Integer),
    Column("duplicate_keys_source", Integer), Column("duplicate_keys_target", Integer),
    Column("match_rate", Float), Column("duration_sec", Float), Column("error", Text),
)

_engines: dict[str, Engine] = {}
_lock = threading.Lock()


def reports_dir() -> Path:
    """Directory for reports and the default history DB."""
    return Path(os.environ.get("RECON_REPORTS_DIR", "reports"))


def history_url() -> str:
    """SQLAlchemy URL of the history DB."""
    return os.environ.get("RECON_HISTORY_URL") or f"sqlite:///{reports_dir() / 'recon_history.db'}"


def history_engine() -> Engine:
    """Cached engine for the history DB; creates the tables (and sqlite directory) on first use."""
    url = history_url()
    with _lock:
        if url not in _engines:
            if url.startswith("sqlite:///") and ":memory:" not in url:
                Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
            eng = create_engine(url, pool_pre_ping=True)
            metadata.create_all(eng)
            _engines[url] = eng
        return _engines[url]


@dataclass
class SuiteRun:
    """Outcome of a whole suite."""

    suite: str
    description: str = ""
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    started_at: str = ""
    finished_at: str = ""
    duration_sec: float = 0.0
    results: list[TestResult] = field(default_factory=list)
    json_path: str | None = None
    html_path: str | None = None

    @property
    def status(self) -> str:
        """ERROR if any test errored, else FAIL if any failed, else PASS."""
        states = {r.status for r in self.results}
        return ERROR if ERROR in states else FAIL if FAIL in states else PASS

    def counts(self) -> dict[str, int]:
        """Totals per status."""
        c = {"tests": len(self.results), "passed": 0, "failed": 0, "errors": 0}
        for r in self.results:
            c["passed" if r.status == PASS else "failed" if r.status == FAIL else "errors"] += 1
        return c

    def to_dict(self, include_samples: bool = True) -> dict[str, Any]:
        """JSON-safe representation."""
        return {
            "run_id": self.run_id, "suite": self.suite, "description": self.description,
            "started_at": self.started_at, "finished_at": self.finished_at,
            "duration_sec": round(self.duration_sec, 4), "status": self.status,
            "counts": self.counts(), "json_path": self.json_path, "html_path": self.html_path,
            "tests": [r.to_dict(include_samples) for r in self.results],
        }

    def summary(self) -> str:
        """Multi-line text summary."""
        c = self.counts()
        head = f"Suite '{self.suite}' run {self.run_id}: {self.status} ({c['passed']} pass, {c['failed']} fail, {c['errors']} error)"
        return "\n".join([head, *[r.summary() for r in self.results]])


def run_suite(
    suite_or_path: Suite | str | Path, only: list[str] | None = None, workers: int = 4, save: bool = True
) -> SuiteRun:
    """Run enabled tests of a suite in a thread pool; optionally write reports and history."""
    suite = suite_or_path if isinstance(suite_or_path, Suite) else load_suite(suite_or_path)
    tests = suite.enabled_tests(only)
    now = dt.datetime.now(dt.timezone.utc)
    t0 = dt.datetime.now(dt.timezone.utc)
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(tests) or 1))) as pool:
        results = list(pool.map(run_test, tests))
    end = dt.datetime.now(dt.timezone.utc)
    run = SuiteRun(
        suite=suite.suite, description=suite.description, started_at=now.isoformat(),
        finished_at=end.isoformat(), duration_sec=(end - t0).total_seconds(), results=results,
    )
    if save:
        save_run(run)
    return run


def save_run(run: SuiteRun) -> None:
    """Write JSON + HTML reports and persist run/test metrics to the history DB."""
    out = reports_dir()
    out.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.fromisoformat(run.started_at).strftime("%Y%m%dT%H%M%S")
    base = out / f"{run.suite}_{stamp}_{run.run_id}"
    run.json_path, run.html_path = str(base.with_suffix(".json")), str(base.with_suffix(".html"))
    data = run.to_dict()
    Path(run.json_path).write_text(json.dumps(data, indent=2), encoding="utf-8")
    Path(run.html_path).write_text(render_html(data), encoding="utf-8")
    c = run.counts()
    with history_engine().begin() as conn:
        conn.execute(insert(runs_table).values(
            run_id=run.run_id, suite=run.suite, description=run.description, started_at=run.started_at,
            status=run.status, tests_total=c["tests"], tests_passed=c["passed"], tests_failed=c["failed"],
            tests_error=c["errors"], duration_sec=run.duration_sec, json_path=run.json_path, html_path=run.html_path,
        ))
        rows = [
            dict(
                run_id=run.run_id, suite=run.suite, test_name=r.name, started_at=r.started_at or run.started_at,
                status=r.status, source_rows=r.source_rows, target_rows=r.target_rows, matched_keys=r.matched_keys,
                identical_rows=r.identical_rows, mismatched_rows=r.mismatched_rows,
                missing_in_target=r.missing_in_target, missing_in_source=r.missing_in_source,
                duplicate_keys_source=r.duplicate_keys_source, duplicate_keys_target=r.duplicate_keys_target,
                match_rate=r.match_rate, duration_sec=r.duration_sec, error=r.error,
            )
            for r in run.results
        ]
        if rows:
            conn.execute(insert(results_table), rows)


def history(limit: int = 100, suite: str | None = None) -> pd.DataFrame:
    """Past runs, newest first."""
    q = select(runs_table).order_by(runs_table.c.started_at.desc()).limit(limit)
    if suite:
        q = q.where(runs_table.c.suite == suite)
    with history_engine().connect() as conn:
        return pd.read_sql_query(q, conn)


def test_history(test_name: str | None = None, suite: str | None = None, limit: int = 1000) -> pd.DataFrame:
    """Per-test metrics over time (oldest first), optionally filtered."""
    q = select(results_table).order_by(results_table.c.started_at.desc()).limit(limit)
    if test_name:
        q = q.where(results_table.c.test_name == test_name)
    if suite:
        q = q.where(results_table.c.suite == suite)
    with history_engine().connect() as conn:
        df = pd.read_sql_query(q, conn)
    return df.iloc[::-1].reset_index(drop=True)





def load_run(run_id: str) -> dict[str, Any]:
    """Full saved result of a run. Raises ``KeyError`` for an unknown id, ``FileNotFoundError`` if the report is gone."""
    with history_engine().connect() as conn:
        row = conn.execute(select(runs_table).where(runs_table.c.run_id == run_id)).mappings().first()
    if row is None:
        raise KeyError(run_id)
    return json.loads(Path(row["json_path"]).read_text(encoding="utf-8"))


def run_html_path(run_id: str) -> Path:
    """Path of the saved HTML report for a run (``KeyError`` if unknown)."""
    with history_engine().connect() as conn:
        row = conn.execute(select(runs_table.c.html_path).where(runs_table.c.run_id == run_id)).first()
    if row is None:
        raise KeyError(run_id)
    return Path(row[0])
