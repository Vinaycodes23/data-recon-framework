import json
from pathlib import Path

import pandas as pd
import pytest

from recon import runner
from recon.config import Suite, TestConfig
from recon.report import render_html
from recon.runner import run_suite


def make_suite(tmp_path: Path, name="s") -> Suite:
    pd.DataFrame({"id": [1, 2, 3], "v": ["a", "b", "<script>x</script>"]}).to_csv(tmp_path / "src.csv", index=False)
    pd.DataFrame({"id": [1, 2, 3], "v": ["a", "b", "<script>x</script>"]}).to_csv(tmp_path / "ok.csv", index=False)
    pd.DataFrame({"id": [1, 2], "v": ["a", "ZZ"]}).to_csv(tmp_path / "bad.csv", index=False)

    def t(n, tgt, enabled=True):
        return TestConfig(name=n, source={"type": "csv", "path": str(tmp_path / "src.csv")},
                          target={"type": "csv", "path": str(tmp_path / tgt)}, keys=["id"], enabled=enabled)

    return Suite(suite=name, description="d", tests=[t("good", "ok.csv"), t("bad", "bad.csv"), t("off", "ok.csv", False)])


def test_run_suite_saves_reports_and_history(tmp_path):
    run = run_suite(make_suite(tmp_path), workers=2)
    assert [r.name for r in run.results] == ["good", "bad"] and run.status == "FAIL"
    assert Path(run.json_path).exists() and Path(run.html_path).exists()
    h = runner.history()
    assert len(h) == 1 and h.iloc[0]["tests_failed"] == 1 and h.iloc[0]["tests_passed"] == 1
    th = runner.test_history(test_name="bad")
    assert len(th) == 1 and th.iloc[0]["missing_in_target"] == 1
    full = runner.load_run(run.run_id)
    assert full["suite"] == "s" and full["tests"][1]["samples"]["mismatches"][0]["column"] == "v"
    assert runner.run_html_path(run.run_id) == Path(run.html_path)
    with pytest.raises(KeyError):
        runner.load_run("nope")


def test_only_and_no_save(tmp_path):
    run = run_suite(make_suite(tmp_path), only=["good"], save=False)
    assert run.status == "PASS" and run.json_path is None
    assert runner.history().empty


def test_error_status_and_path_loading(tmp_path):
    suite = make_suite(tmp_path)
    suite.tests[0].source = {"type": "nope"}
    run = run_suite(suite, save=False)
    assert run.status == "ERROR" and "ERROR" in run.summary()


def test_history_url_override(tmp_path, monkeypatch):
    monkeypatch.setenv("RECON_HISTORY_URL", f"sqlite:///{tmp_path / 'custom.db'}")
    run_suite(make_suite(tmp_path))
    assert (tmp_path / "custom.db").exists() and len(runner.history()) == 1


def test_html_escapes_and_is_self_contained(tmp_path):
    run = run_suite(make_suite(tmp_path), save=False)
    data = json.loads(json.dumps(run.to_dict()))
    data["tests"][0]["failures"] = ["<b>boom</b>"]
    html = render_html(data)
    assert "<b>boom</b>" not in html and "&lt;b&gt;boom&lt;/b&gt;" in html
    assert "<script" not in html and "http://" not in html and "https://" not in html
    assert 'name="viewport"' in html and "PASS" in html and "FAIL" in html
