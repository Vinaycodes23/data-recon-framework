import pandas as pd
import pytest
import sqlalchemy as sa

from recon import connectors
from recon.connectors import ConnectorError, describe, load, mask_url
from recon.connectors.saas import scan_table

DF = pd.DataFrame({"id": [1, 2, 3], "name": ["a", "b", "c"], "amt": [1.5, 2.5, 3.5]})


def test_csv_tsv(tmp_path):
    DF.to_csv(tmp_path / "a.csv", index=False)
    DF.to_csv(tmp_path / "a.tsv", index=False, sep="\t")
    pd.testing.assert_frame_equal(load({"type": "csv", "path": str(tmp_path / "a.csv")}), DF, check_dtype=False)
    assert list(load({"type": "tsv", "path": str(tmp_path / "a.tsv")}).columns) == list(DF.columns)


def test_xlsx_parquet(tmp_path):
    DF.to_excel(tmp_path / "a.xlsx", index=False)
    DF.to_parquet(tmp_path / "a.parquet")
    assert len(load({"type": "xlsx", "path": str(tmp_path / "a.xlsx")})) == 3
    assert len(load({"type": "parquet", "path": str(tmp_path / "a.parquet")})) == 3


def test_json_jsonl(tmp_path):
    DF.to_json(tmp_path / "a.json", orient="records")
    DF.to_json(tmp_path / "a.jsonl", orient="records", lines=True)
    assert len(load({"type": "json", "path": str(tmp_path / "a.json")})) == 3
    assert len(load({"type": "jsonl", "path": str(tmp_path / "a.jsonl")})) == 3


def test_fwf_reads_strings(tmp_path):
    p = tmp_path / "a.fwf"
    p.write_text("001  12.50 \n002 007.00\n")
    df = load({"type": "fwf", "path": str(p), "widths": [3, 8], "names": ["id", "amt"]})
    assert df["id"].tolist() == ["001", "002"]
    assert df["amt"].tolist() == ["12.50", "007.00"]
    with pytest.raises(ConnectorError):
        load({"type": "fwf", "path": str(p)})


def test_sqlite_query_and_table(tmp_path):
    url = f"sqlite:///{tmp_path / 'x.db'}"
    eng = sa.create_engine(url)
    DF.to_sql("t", eng, index=False)
    assert len(load({"type": "sqlite", "url": url, "query": "select * from t where id > 1"})) == 2
    assert len(load({"type": "sqlite", "url": url, "table": "t"})) == 3
    assert len(load({"type": "sqlite", "url": url, "table": "t", "where": "id = 1"})) == 1
    up = load({"type": "sqlite", "url": url, "table": "t", "lowercase_columns": True})
    assert list(up.columns) == ["id", "name", "amt"]
    with pytest.raises(ConnectorError):
        load({"type": "sqlite", "url": url})


def test_unknown_type_and_missing_file(tmp_path):
    with pytest.raises(ConnectorError, match="Unknown connector"):
        load({"type": "nope"})
    with pytest.raises(ConnectorError):
        load({"type": "csv", "path": str(tmp_path / "missing.csv")})


def test_masking_and_describe():
    assert mask_url("postgresql://u:secret@h/db") == "postgresql://u:****@h/db"
    d = describe({"type": "snowflake", "url": "snowflake://u:pw@acct/db", "table": "T"})
    assert "pw" not in d and "****" in d
    assert "secret" not in describe({"type": "salesforce", "username": "u", "password": "secret"})
    assert describe({"type": "csv", "path": "a.csv"}) == "csv: a.csv"


def test_registry_extensible():
    @connectors.register("memtest")
    def _load(spec):
        return pd.DataFrame({"a": [1]})

    assert "memtest" in connectors.available_types()
    assert load({"type": "memtest"}).shape == (1, 1)


def test_dynamodb_pagination_and_decimal():
    from decimal import Decimal

    class FakeTable:
        def __init__(self):
            self.calls = 0

        def scan(self, **kw):
            self.calls += 1
            if "ExclusiveStartKey" not in kw:
                return {"Items": [{"id": 1, "p": Decimal("1.5")}], "LastEvaluatedKey": {"id": 1}}
            return {"Items": [{"id": 2, "p": Decimal("2.5")}]}

    t = FakeTable()
    items = scan_table(t)
    assert t.calls == 2 and items == [{"id": 1, "p": 1.5}, {"id": 2, "p": 2.5}]
