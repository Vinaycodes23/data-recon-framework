"""Boot every Streamlit page with AppTest and assert there are no exceptions."""

from pathlib import Path

import pytest
import seed_demo
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "apps" / "streamlit_app.py")


@pytest.fixture(scope="module")
def demo_env(tmp_path_factory):
    import os

    d = tmp_path_factory.mktemp("ui")
    seed_demo.seed(d / "demo")
    os.environ.update(DEMO_DATA_DIR=str(d / "demo"), RECON_REPORTS_DIR=str(d / "reports"))
    yield
    del os.environ["DEMO_DATA_DIR"], os.environ["RECON_REPORTS_DIR"]


def test_all_pages_boot(demo_env):
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    for page in ("Run", "Results", "History", "Suite Builder"):
        at.sidebar.radio[0].set_value(page).run()
        assert not at.exception, (page, at.exception)


def test_run_then_results(demo_env):
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.sidebar.radio[0].set_value("Run").run()
    at.button[0].click().run()
    assert not at.exception
    assert any("Suite status" in m.label for m in at.metric)
    at.sidebar.radio[0].set_value("Results").run()
    assert not at.exception
    assert any(m.label == "Match rate" for m in at.metric)
    at.sidebar.radio[0].set_value("History").run()
    assert not at.exception


def test_builder_preview(demo_env):
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.sidebar.radio[0].set_value("Suite Builder").run()
    assert not at.exception
    at.selectbox(key="sel:demo_migration.yaml").set_value("orders").run()
    [b for b in at.button if b.label.startswith("Preview")][0].click().run()
    assert not at.exception and not at.error
    assert any("Source columns" in c.value for c in at.caption)


def test_builder_save_test_keeps_placeholders(demo_env):
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.sidebar.radio[0].set_value("Suite Builder").run()
    at.selectbox(key="sel:demo_migration.yaml").set_value("orders").run()
    [b for b in at.button if b.label.endswith("Save test")][0].click().run()
    assert not at.exception and not at.error
    yaml_text = [c for c in at.code if "suite: demo_migration" in c.value][0].value
    assert "${DEMO_DATA_DIR:-demo_data}" in yaml_text  # secrets/placeholders never resolved into the editor
    assert "column_map" in yaml_text or "customer_id" in yaml_text
