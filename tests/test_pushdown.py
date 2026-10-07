import pandas as pd
import pytest
import sqlalchemy as sa

from recon import pushdown
from recon.config import Aggregate, TestConfig
from recon.engine import run_test

AGGS = [Aggregate("amount", "sum", 0.05), Aggregate("id", "count_distinct"), Aggregate("amount", "max"),
        Aggregate("amount", "min"), Aggregate("amount", "mean", 0.01), Aggregate("note", "null_count"), Aggregate("id", "count")]


def frames():
    src = pd.DataFrame({"id": [1, 2, 3, 4], "amount": [10.0, 20.0, 30.0, 40.0], "note": ["a", None, "c", None]})
    tgt = src.copy()
    tgt.loc[3, "amount"] = 44.0
    tgt["note"] = ["a", "b", "c", None]
    return src, tgt


def test_quote_ident_and_sql():
    assert pushdown.quote_ident("amount") == "amount"
    assert pushdown.quote_ident('we ird"col') == '"we ird""col"'
    sql = pushdown.aggregate_sql("SELECT * FROM t", [(Aggregate("a", "sum"), "a"), (Aggregate("b c", "null_count"), "b c")])
    assert sql.startswith("SELECT SUM(a) AS a0, SUM(CASE WHEN \"b c\" IS NULL THEN 1 ELSE 0 END) AS a1 FROM (SELECT * FROM t)")


def test_sqlite_pushdown_matches_in_memory_engine(tmp_path):
    src, tgt = frames()
    s_url, t_url = f"sqlite:///{tmp_path / 's.db'}", f"sqlite:///{tmp_path / 't.db'}"
    src.to_sql("t", sa.create_engine(s_url), index=False)
    tgt.to_sql("t", sa.create_engine(t_url), index=False)
    cfg = TestConfig(name="p", source={"type": "sqlite", "url": s_url, "table": "t"},
                     target={"type": "sqlite", "url": t_url, "table": "t"}, keys=["id"], aggregates=AGGS)
    pushed = pushdown.pushdown_aggregates(cfg)
    in_memory = run_test(cfg, src, tgt).aggregates
    for a, b in zip(pushed, in_memory, strict=True):
        assert (a["column"], a["func"], a["passed"]) == (b["column"], b["func"], b["passed"])
        assert a["source"] == pytest.approx(b["source"]) and a["target"] == pytest.approx(b["target"])
    assert [a["passed"] for a in pushed] == [False, True, False, True, False, False, True]


def test_duckdb_parquet_and_csv_with_column_map(tmp_path):
    pytest.importorskip("duckdb")
    src, tgt = frames()
    src.to_parquet(tmp_path / "s.parquet")
    tgt.rename(columns={"amount": "amt"}).to_csv(tmp_path / "t.csv", index=False)
    cfg = TestConfig(name="d", source={"type": "parquet", "path": str(tmp_path / "s.parquet")},
                     target={"type": "csv", "path": str(tmp_path / "t.csv")}, keys=["id"],
                     column_map={"amount": "amt"}, aggregates=[Aggregate("amount", "sum", 0.05), Aggregate("id", "count")])
    res = pushdown.pushdown_aggregates(cfg)
    assert res[0]["source"] == 100.0 and res[0]["target"] == 104.0 and not res[0]["passed"]
    assert res[1]["passed"]


def test_unsupported_and_empty():
    assert not pushdown.supports({"type": "salesforce"}) and pushdown.supports({"type": "sqlite", "url": "x"})
    assert pushdown.pushdown_aggregates(TestConfig(name="x", source={}, target={}, keys=["id"])) == []
    with pytest.raises(Exception, match="not supported"):
        pushdown._run_side({"type": "xlsx", "path": "a"}, [(Aggregate("a", "sum"), "a")])
