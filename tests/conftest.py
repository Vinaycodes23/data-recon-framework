import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_reports(tmp_path, monkeypatch):
    """Send reports and history to a temp dir for every test."""
    monkeypatch.setenv("RECON_REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.delenv("RECON_HISTORY_URL", raising=False)
