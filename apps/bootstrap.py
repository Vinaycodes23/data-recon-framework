"""Startup helpers so the app works on a fresh checkout and on Streamlit Community Cloud.

* Missing demo data is seeded automatically (the caller caches this so it runs once per process).
* In the cloud (or when ``RECON_CLOUD`` is set) reports, the history DB and demo data are written
  under the system temp directory, because the repository checkout should be treated as read-only.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def in_cloud() -> bool:
    """True on Streamlit Community Cloud (apps are mounted under /mount/src) or when RECON_CLOUD is set."""
    return bool(os.environ.get("RECON_CLOUD")) or Path("/mount/src").exists()


def configure_paths() -> None:
    """Point reports / demo data at a writable temp location when running in the cloud."""
    if in_cloud():
        base = Path(tempfile.gettempdir()) / "recon_app"
        os.environ.setdefault("RECON_REPORTS_DIR", str(base / "reports"))
        os.environ.setdefault("DEMO_DATA_DIR", str(base / "demo_data"))


def ensure_demo_data() -> Path:
    """Seed the demo data if it is missing and return its directory."""
    configure_paths()
    data_dir = Path(os.environ.get("DEMO_DATA_DIR", "demo_data"))
    if not (data_dir / "legacy_crm.db").exists():
        sys.path.insert(0, str(ROOT / "scripts"))
        import seed_demo

        seed_demo.seed(data_dir)
    os.environ["DEMO_DATA_DIR"] = str(data_dir)
    return data_dir
