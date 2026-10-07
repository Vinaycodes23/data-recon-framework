import pandas as pd

from recon.cli import main
from recon.config import Suite, TestConfig, save_suite


def suite_file(tmp_path, tgt_rows):
    pd.DataFrame({"id": [1, 2]}).to_csv(tmp_path / "s.csv", index=False)
    pd.DataFrame({"id": tgt_rows}).to_csv(tmp_path / "t.csv", index=False)
    t = TestConfig(name="x", source={"type": "csv", "path": str(tmp_path / "s.csv")},
                   target={"type": "csv", "path": str(tmp_path / "t.csv")}, keys=["id"])
    p = tmp_path / "suite.yaml"
    save_suite(Suite(suite="cli", tests=[t]), p)
    return str(p)


def test_run_exit_codes(tmp_path, capsys):
    ok = suite_file(tmp_path, [1, 2])
    assert main(["run", ok, "--fail-on-mismatch"]) == 0
    bad = suite_file(tmp_path, [1])
    assert main(["run", bad]) == 0
    assert main(["run", bad, "--fail-on-mismatch", "--no-save"]) == 1
    assert "missing in target" in capsys.readouterr().out


def test_run_error_exits_1(tmp_path):
    p = suite_file(tmp_path, [1, 2])
    (tmp_path / "t.csv").unlink()
    assert main(["run", p, "--no-save"]) == 1


def test_validate(tmp_path, capsys):
    p = suite_file(tmp_path, [1, 2])
    assert main(["validate", p]) == 0
    bad = tmp_path / "bad.yaml"
    bad.write_text("suite: x\ntests:\n  - name: a\n    source: {type: zzz}\n    target: {type: csv}\n    keys: [id]\n")
    assert main(["validate", str(bad)]) == 1
    assert "unknown connector" in capsys.readouterr().out
    assert main(["validate", str(tmp_path / "missing.yaml")]) == 1


def test_history_command(tmp_path, capsys):
    assert main(["history"]) == 0 and "No runs" in capsys.readouterr().out
    main(["run", suite_file(tmp_path, [1, 2])])
    capsys.readouterr()
    assert main(["history"]) == 0 and "cli" in capsys.readouterr().out
