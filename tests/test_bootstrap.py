import os
from pathlib import Path

from apps import bootstrap


def test_seeds_when_missing_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.delenv("RECON_CLOUD", raising=False)
    monkeypatch.setattr(bootstrap, "in_cloud", lambda: False)
    d = bootstrap.ensure_demo_data()
    assert (d / "legacy_crm.db").exists() and (d / "warehouse" / "orders.parquet").exists()
    mtime = (d / "legacy_crm.db").stat().st_mtime_ns
    bootstrap.ensure_demo_data()
    assert (d / "legacy_crm.db").stat().st_mtime_ns == mtime  # not re-seeded


def test_cloud_uses_temp_paths(tmp_path, monkeypatch):
    monkeypatch.delenv("DEMO_DATA_DIR", raising=False)
    monkeypatch.delenv("RECON_REPORTS_DIR", raising=False)
    monkeypatch.setenv("RECON_CLOUD", "1")
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    import tempfile

    tempfile.tempdir = None
    try:
        bootstrap.configure_paths()
        assert os.environ["RECON_REPORTS_DIR"].startswith(str(Path(tempfile.gettempdir())))
        assert os.environ["DEMO_DATA_DIR"].endswith("demo_data")
    finally:
        monkeypatch.delenv("DEMO_DATA_DIR", raising=False)
        monkeypatch.delenv("RECON_REPORTS_DIR", raising=False)
        tempfile.tempdir = None
