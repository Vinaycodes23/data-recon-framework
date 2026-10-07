import pandas as pd
import pytest

from recon.config import Aggregate, Options, Thresholds, TestConfig
from recon.engine import ERROR, FAIL, PASS, TestResult, compare_frames, run_test


def cfg(**kw):
    base = dict(name="t", source={"type": "csv", "path": "x"}, target={"type": "csv", "path": "y"}, keys=["id"])
    base.update(kw)
    return TestConfig(**base)


def run(src, tgt, **kw):
    return run_test(cfg(**kw), pd.DataFrame(src), pd.DataFrame(tgt))


BASE = {"id": [1, 2, 3], "name": ["a", "b", "c"], "amount": [10.0, 20.0, 30.0]}


def test_identical_passes():
    r = run(BASE, BASE)
    assert r.status == PASS and r.match_rate == 100 and r.identical_rows == 3 and not r.failures


def test_missing_and_extra():
    tgt = {"id": [2, 3, 4, 5], "name": list("bcde"), "amount": [20.0, 30.0, 1.0, 2.0]}
    r = run(BASE, tgt)
    assert r.status == FAIL
    assert (r.missing_in_target, r.missing_in_source, r.matched_keys) == (1, 2, 2)
    assert r.samples["missing_in_target"]["id"].tolist() == [1]
    assert sorted(r.samples["missing_in_source"]["id"].tolist()) == [4, 5]
    assert r.missing_pct == pytest.approx(33.3333, abs=1e-3)


def test_numeric_tolerance_boundary():
    tgt = {**BASE, "amount": [10.01, 20.0, 30.02]}
    r = run(BASE, tgt, tolerance={"amount": 0.01})
    assert r.mismatched_rows == 1 and r.mismatches_by_column == {"amount": 1}
    s = r.samples["mismatches"]
    assert s["difference"].iloc[0] == pytest.approx(0.02) and s["id"].iloc[0] == 3
    assert run(BASE, tgt).mismatched_rows == 2  # no tolerance


def test_case_insensitive_option():
    tgt = {**BASE, "name": ["A", "b", "C"]}
    assert run(BASE, tgt).mismatched_rows == 2
    assert run(BASE, tgt, options=Options(case_insensitive=True)).status == PASS


def test_trim_option():
    tgt = {**BASE, "name": [" a ", "b  ", "c"]}
    assert run(BASE, tgt).status == PASS
    r = run(BASE, tgt, options=Options(trim_strings=False))
    assert r.mismatched_rows == 2


def test_null_handling():
    src = {"id": [1, 2, 3], "email": ["a@x", None, "c@x"]}
    tgt = {"id": [1, 2, 3], "email": ["a@x", None, None]}
    r = run(src, tgt)
    assert r.mismatched_rows == 1 and r.samples["mismatches"]["id"].tolist() == [3]
    assert run(src, tgt, options=Options(null_equals_null=False)).mismatched_rows == 2
    # empty string treated as null
    tgt2 = {"id": [1, 2, 3], "email": ["a@x", "", "c@x"]}
    assert run(src, tgt2).status == PASS
    assert run(src, tgt2, options=Options(empty_string_as_null=False)).mismatched_rows == 1


def test_duplicate_keys():
    tgt = {"id": [1, 1, 2, 3], "name": list("aabc"), "amount": [10.0, 10.0, 20.0, 30.0]}
    r = run(BASE, tgt)
    assert r.duplicate_keys_target == 1 and r.duplicate_keys_source == 0
    assert r.status == FAIL and any("duplicate" in f for f in r.failures)
    assert len(r.samples["duplicates_target"]) == 2
    ok = run(BASE, tgt, thresholds=Thresholds(allow_duplicate_keys=True))
    assert ok.status == PASS


def test_composite_keys():
    src = {"a": [1, 1, 2], "b": ["x", "y", "x"], "v": [1, 2, 3]}
    tgt = {"a": [1, 1, 2], "b": ["x", "y", "x"], "v": [1, 9, 3]}
    r = run(src, tgt, keys=["a", "b"])
    assert r.matched_keys == 3 and r.duplicate_keys_source == 0 and r.mismatched_rows == 1
    assert r.samples["mismatches"][["a", "b"]].iloc[0].tolist() == [1, "y"]


def test_key_normalisation():
    src = {"id": [1, 2, 3], "v": [1, 2, 3]}
    tgt = {"id": ["1", " 2 ", 3.0], "v": [1, 2, 3]}
    r = run(src, tgt)
    assert r.matched_keys == 3 and r.status == PASS
    r = run({"id": ["1", "2"], "v": [1, 2]}, {"id": [1.0, 2.0], "v": [1, 2]})
    assert r.matched_keys == 2


def test_column_map_and_case_insensitive_columns():
    src = {"ID": [1, 2], "cust_id": [7, 8], "Name": ["a", "b"]}
    tgt = {"id": [1, 2], "customer_id": [7, 9], "NAME": ["a", "b"]}
    r = run(src, tgt, column_map={"cust_id": "customer_id"})
    assert r.mismatches_by_column == {"customer_id": 1}
    assert r.schema["only_in_source"] == [] and r.schema["only_in_target"] == []


def test_ignore_and_schema_drift():
    src = {"id": [1], "a": [1], "legacy": [1]}
    tgt = {"id": [1], "a": [1], "load_ts": ["2024"], "extra": [5]}
    r = run(src, tgt, ignore_columns=["load_ts"])
    assert r.schema["only_in_target"] == ["extra"] and r.schema["only_in_source"] == ["legacy"]
    assert "load_ts" in r.schema["ignored"] and r.status == PASS
    r = run(src, tgt, ignore_columns=["load_ts"], thresholds=Thresholds(allow_schema_drift=False))
    assert r.status == FAIL


def test_numeric_strings_and_fwf_style():
    src = {"id": [1, 2], "amt": [1234.5, 123.4]}
    tgt = {"id": ["001", "002"], "amt": ["1,234.50", "00123.40"]}
    r = run(src, tgt)
    assert r.status == PASS, r.failures


def test_datetime_vs_string_dates():
    src = {"id": [1, 2], "d": pd.to_datetime(["2024-01-05", "2024-02-01"])}
    tgt = {"id": [1, 2], "d": ["2024-01-05", "2024-02-02"]}
    r = run(src, tgt)
    assert r.mismatches_by_column == {"d": 1}
    assert [c for c in r.schema["columns"] if c["column"] == "d"][0]["kind_match"] is False


def test_aggregates():
    tgt = {**BASE, "amount": [10.0, 20.0, 30.04]}
    r = run(BASE, tgt, tolerance={"amount": 0.05}, aggregates=[
        Aggregate("amount", "sum", 0.05), Aggregate("id", "count_distinct"),
        Aggregate("amount", "max", 0.0), Aggregate("name", "null_count"),
    ])
    by = {(a["column"], a["func"]): a for a in r.aggregates}
    assert by[("amount", "sum")]["passed"] and by[("amount", "sum")]["difference"] == pytest.approx(0.04)
    assert by[("id", "count_distinct")]["passed"] and by[("name", "null_count")]["passed"]
    assert not by[("amount", "max")]["passed"] and r.status == FAIL
    bad = run(BASE, BASE, aggregates=[Aggregate("nope", "sum"), Aggregate("name", "sum")])
    assert bad.status == FAIL and len(bad.failures) == 2


def test_thresholds_allow_small_drift():
    tgt = {"id": list(range(1, 100)), "v": [1] * 99}
    src = {"id": list(range(1, 101)), "v": [1] * 100}
    assert run(src, tgt).status == FAIL
    assert run(src, tgt, thresholds=Thresholds(max_missing_pct=2)).status == PASS


def test_sample_cap_does_not_affect_counts():
    src = {"id": range(100), "v": range(100)}
    tgt = {"id": range(100), "v": range(1, 101)}
    r = run(src, tgt, options=Options(max_mismatch_samples=10))
    assert r.mismatched_rows == 100 and len(r.samples["mismatches"]) == 10


def test_null_keys_flagged():
    r = run({"id": [1, None], "v": [1, 2]}, {"id": [1, 2], "v": [1, 2]})
    assert r.null_keys_source == 1 and r.status == FAIL


def test_empty_frames():
    r = run({"id": [], "v": []}, {"id": [], "v": []})
    assert r.status == PASS and r.match_rate == 100


def test_error_on_bad_connector_and_bad_key():
    r = run_test(cfg(source={"type": "nope"}))
    assert r.status == ERROR and "Unknown connector" in r.error
    r = run(BASE, BASE, keys=["missing"])
    assert r.status == ERROR and "missing" in r.error


def test_result_serialisation_round_trip():
    tgt = {**BASE, "amount": [10.0, 99.0, None]}
    r = run(BASE, tgt)
    import json

    d = json.loads(json.dumps(r.to_dict()))
    assert d["mismatched_rows"] == 2 and d["samples"]["mismatches"][0]["column"] == "amount"
    back = TestResult.from_dict(d)
    assert back.mismatched_rows == 2 and len(back.samples["mismatches"]) == 2
    assert "FAIL" in r.summary()
    assert "ERROR" in run_test(cfg(source={"type": "nope"})).summary()


def test_never_hashes_away_real_diffs_with_neg_zero():
    r = run({"id": [1], "v": [0.0]}, {"id": [1], "v": [-0.0]})
    assert r.status == PASS
