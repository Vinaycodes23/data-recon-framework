"""Seed the demo data, run the demo suite and assert the exact planted counts."""

from pathlib import Path

import pytest
import seed_demo

from recon import run_suite

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    d = tmp_path_factory.mktemp("demo")
    manifest = seed_demo.seed(d)
    import os

    os.environ["DEMO_DATA_DIR"] = str(d)
    os.environ["RECON_REPORTS_DIR"] = str(d / "reports")
    try:
        run = run_suite(ROOT / "suites" / "demo_migration.yaml", save=False)
    finally:
        del os.environ["DEMO_DATA_DIR"]
        del os.environ["RECON_REPORTS_DIR"]
    return manifest, {r.name: r for r in run.results}, run


def test_orders_planted_counts(demo):
    m, res, _ = demo
    r, p = res["orders"], m["orders"]
    assert r.status == "FAIL"
    assert r.missing_in_target == p["missing_in_target"] == 12
    assert r.missing_in_source == p["missing_in_source"] == 5
    assert r.duplicate_keys_target == 2 and r.duplicate_keys_source == 0
    assert r.mismatched_rows == 18
    assert r.mismatches_by_column == {"amount": 10, "status": 8}
    assert sorted(r.samples["missing_in_target"]["order_id"]) == p["missing_ids"]
    assert r.identical_rows == r.matched_keys - 18 and r.matched_keys == 4988
    assert r.schema["only_in_target"] == [] and "load_ts" in r.schema["ignored"]
    big = r.samples["mismatches"].query("column == 'amount'")
    assert sorted(big["order_id"]) == p["big_drift_ids"]
    assert big["difference"].round(2).eq(2.5).all()


def test_customers_planted(demo):
    r = demo[1]["customers"]
    assert r.mismatches_by_column == {"email": 3} and r.mismatched_rows == 3
    assert r.schema["only_in_target"] == ["loyalty_tier"]
    assert sorted(r.samples["mismatches"]["customer_id"]) == demo[0]["customers"]["email_null_ids"]


def test_products_pass_and_payments_fwf(demo):
    assert demo[1]["products"].status == "PASS" and demo[1]["products"].match_rate == 100
    p = demo[1]["payments"]
    assert p.missing_in_target == 3 and p.mismatched_rows == 6 and p.missing_in_source == 0
    assert all(abs(d - 1.0) < 1e-6 for d in p.samples["mismatches"]["difference"])


def test_suite_outcome(demo):
    run = demo[2]
    assert run.status == "FAIL" and run.counts() == {"tests": 4, "passed": 1, "failed": 3, "errors": 0}
