"""Thin REST API over the reconciliation runner.

Run with ``python apps/api.py`` (or ``flask --app apps.api run``).
Suites are read from ``RECON_SUITES_DIR`` (default: ``suites/`` in the repo root).
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

from flask import Flask, abort, jsonify, request, send_file

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from recon import runner  # noqa: E402
from recon.config import ConfigError, load_suite  # noqa: E402

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def suites_dir() -> Path:
    """Directory holding suite YAML files."""
    return Path(os.environ.get("RECON_SUITES_DIR", ROOT / "suites")).resolve()


def safe_suite_path(name: str) -> Path | None:
    """Map a suite name to a file inside ``suites_dir``; ``None`` if unsafe or missing (blocks path traversal)."""
    if not isinstance(name, str) or not _SAFE_NAME.match(name) or ".." in name:
        return None
    base = suites_dir()
    for candidate in ([name] if name.endswith((".yaml", ".yml")) else [f"{name}.yaml", f"{name}.yml"]):
        path = (base / candidate).resolve()
        if path.parent == base and path.is_file():
            return path
    return None


def create_app() -> Flask:
    """Application factory."""
    app = Flask(__name__)

    @app.get("/health")
    def health() -> Any:
        return jsonify(status="ok")

    @app.get("/api/suites")
    def list_suites() -> Any:
        files = sorted(p.name for p in suites_dir().glob("*.y*ml") if p.suffix in (".yaml", ".yml"))
        return jsonify(suites=files)

    @app.post("/api/runs")
    def create_run() -> Any:
        body = request.get_json(silent=True) or {}
        path = safe_suite_path(body.get("suite", ""))
        if path is None:
            return jsonify(error="unknown or invalid suite"), 404
        tests = body.get("tests") or None
        if tests is not None and not (isinstance(tests, list) and all(isinstance(t, str) for t in tests)):
            return jsonify(error="'tests' must be a list of test names"), 400
        try:
            run = runner.run_suite(load_suite(path), only=tests)
        except ConfigError as exc:
            return jsonify(error=str(exc)), 400
        return jsonify(run.to_dict(include_samples=False)), 201

    @app.get("/api/runs")
    def list_runs() -> Any:
        df = runner.history(limit=int(request.args.get("limit", 50)), suite=request.args.get("suite"))
        return jsonify(runs=df.to_dict("records"))

    @app.get("/api/runs/<run_id>")
    def get_run(run_id: str) -> Any:
        try:
            return jsonify(runner.load_run(run_id))
        except (KeyError, FileNotFoundError):
            abort(404)

    @app.get("/api/runs/<run_id>/report")
    def get_report(run_id: str) -> Any:
        try:
            path = runner.run_html_path(run_id)
        except KeyError:
            abort(404)
        if not path.is_file():
            abort(404)
        return send_file(path, mimetype="text/html")

    @app.errorhandler(404)
    def not_found(_: Exception) -> Any:
        return jsonify(error="not found"), 404

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)))
