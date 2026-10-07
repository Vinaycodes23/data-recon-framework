import py_compile
from pathlib import Path

import pandas as pd
import pytest

from apps import api
from recon.config import Suite, TestConfig, save_suite

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    sd = tmp_path / "suites"
    sd.mkdir()
    pd.DataFrame({"id": [1, 2]}).to_csv(tmp_path / "s.csv", index=False)
    pd.DataFrame({"id": [1]}).to_csv(tmp_path / "t.csv", index=False)
    t = TestConfig(name="x", source={"type": "csv", "path": str(tmp_path / "s.csv")},
                   target={"type": "csv", "path": str(tmp_path / "t.csv")}, keys=["id"])
    save_suite(Suite(suite="apisuite", tests=[t]), sd / "apisuite.yaml")
    (tmp_path / "secret.yaml").write_text("suite: secret\ntests: []\n")
    monkeypatch.setenv("RECON_SUITES_DIR", str(sd))
    return api.create_app().test_client()


def test_health_and_suites(client):
    assert client.get("/health").get_json() == {"status": "ok"}
    assert client.get("/api/suites").get_json() == {"suites": ["apisuite.yaml"]}


def test_run_history_detail_report(client):
    r = client.post("/api/runs", json={"suite": "apisuite.yaml", "tests": ["x"]})
    assert r.status_code == 201
    body = r.get_json()
    assert body["status"] == "FAIL" and body["tests"][0]["missing_in_target"] == 1
    rid = body["run_id"]
    assert client.get("/api/runs").get_json()["runs"][0]["run_id"] == rid
    detail = client.get(f"/api/runs/{rid}").get_json()
    assert detail["tests"][0]["samples"]["missing_in_target"][0]["id"] == 2
    rep = client.get(f"/api/runs/{rid}/report")
    assert rep.status_code == 200 and b"<html" in rep.data
    assert client.get("/api/runs/deadbeef").status_code == 404
    assert client.get("/api/runs/deadbeef/report").status_code == 404


def test_bad_requests(client):
    assert client.post("/api/runs", json={"suite": "missing"}).status_code == 404
    assert client.post("/api/runs", json={}).status_code == 404
    assert client.post("/api/runs", json={"suite": "apisuite", "tests": "x"}).status_code == 400
    assert client.post("/api/runs", json={"suite": "apisuite", "tests": ["nope"]}).status_code == 400


@pytest.mark.parametrize("name", ["../secret.yaml", "..%2Fsecret.yaml", "/etc/passwd", "a/../../secret", "..", "sub/x.yaml", "..\\secret.yaml"])
def test_path_traversal_blocked(client, name):
    assert client.post("/api/runs", json={"suite": name}).status_code == 404
    assert api.safe_suite_path(name) is None


def test_dag_compiles():
    py_compile.compile(str(ROOT / "airflow" / "dags" / "recon_dag.py"), doraise=True)


def _load_dag(monkeypatch, repo):
    """Execute the DAG file against a minimal Airflow stub, using ``repo`` as the repo path."""
    import importlib.util
    import sys
    import types

    calls = []

    class Fail(Exception):
        pass

    def task(fn):
        class T:
            def override(self, task_id):
                def run(*a):
                    calls.append(task_id)
                    return fn(*a)
                return run
        return T()

    def dag(**kw):
        calls.append(("dag", kw["dag_id"], kw["catchup"], kw["schedule"]))
        return lambda fn: fn

    mods = {
        "airflow": types.ModuleType("airflow"),
        "airflow.decorators": types.SimpleNamespace(dag=dag, task=task),
        "airflow.exceptions": types.SimpleNamespace(AirflowFailException=Fail),
        "airflow.models": types.SimpleNamespace(Variable=types.SimpleNamespace(get=lambda k, default_var=None: str(repo))),
    }
    for k, v in mods.items():
        monkeypatch.setitem(sys.modules, k, v)
    spec = importlib.util.spec_from_file_location("recon_dag_under_test", ROOT / "airflow" / "dags" / "recon_dag.py")
    module = importlib.util.module_from_spec(spec)
    return spec, module, calls, Fail


def _repo(tmp_path, names):
    (tmp_path / "suites").mkdir()
    pd.DataFrame({"id": [1, 2]}).to_csv(tmp_path / "a.csv", index=False)
    pd.DataFrame({"id": [1]}).to_csv(tmp_path / "b.csv", index=False)
    for name, tgt in names.items():
        t = TestConfig(name="x", source={"type": "csv", "path": str(tmp_path / "a.csv")},
                       target={"type": "csv", "path": str(tmp_path / tgt)}, keys=["id"])
        save_suite(Suite(suite=name, tests=[t]), tmp_path / "suites" / f"{name}.yaml")


def test_dag_one_task_per_suite_passes(monkeypatch, tmp_path):
    _repo(tmp_path, {"alpha": "a.csv", "beta": "a.csv"})
    spec, module, calls, _ = _load_dag(monkeypatch, tmp_path)
    spec.loader.exec_module(module)
    assert ("dag", "data_reconciliation", False, "@daily") in calls
    assert calls.count("reconcile_alpha") == 1 and calls.count("reconcile_beta") == 1
    assert module.SUITE_FILES == ["alpha.yaml", "beta.yaml"]


def test_dag_task_fails_on_drift(monkeypatch, tmp_path):
    _repo(tmp_path, {"drifty": "b.csv"})
    spec, module, calls, Fail = _load_dag(monkeypatch, tmp_path)
    with pytest.raises(Fail, match="FAIL"):
        spec.loader.exec_module(module)
