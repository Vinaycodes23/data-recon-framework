"""Daily data reconciliation: one Airflow task per suite file in ``suites/``.

The repository location comes from the Airflow Variable ``recon_repo_path``
(default: two levels above this file). A task fails (and alerts fire) when its run is FAIL or ERROR.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from airflow.decorators import dag, task
from airflow.exceptions import AirflowFailException
from airflow.models import Variable

DEFAULT_REPO = str(Path(__file__).resolve().parents[2])
REPO = Variable.get("recon_repo_path", default_var=DEFAULT_REPO)
SUITE_FILES = sorted(p.name for p in (Path(REPO) / "suites").glob("*.y*ml"))


@dag(
    dag_id="data_reconciliation",
    schedule="@daily",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["reconciliation", "data-quality"],
    default_args={"owner": "data-eng", "retries": 0},
)
def data_reconciliation():
    """Reconcile every suite and fail loudly on drift."""

    @task
    def reconcile(suite_file: str) -> dict:
        if REPO not in sys.path:
            sys.path.insert(0, REPO)
        from recon import run_suite

        run = run_suite(Path(REPO) / "suites" / suite_file)
        print(run.summary())
        if run.status != "PASS":
            raise AirflowFailException(f"Suite {run.suite} finished {run.status}: report {run.html_path}")
        return {"suite": run.suite, "run_id": run.run_id, "status": run.status}

    for name in SUITE_FILES:
        reconcile.override(task_id=f"reconcile_{Path(name).stem}")(name)


data_reconciliation()
