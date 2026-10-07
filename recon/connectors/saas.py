"""SaaS / NoSQL connectors: Salesforce (SOQL) and DynamoDB (paginated scan). Imports are lazy."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pandas as pd

from recon.connectors.base import register


@register("salesforce")
def load_salesforce(spec: dict[str, Any]) -> pd.DataFrame:
    """Run a SOQL query. Credentials: username, password, security_token (use ``${VAR}``)."""
    from simple_salesforce import Salesforce  # lazy: optional dependency

    sf = Salesforce(
        username=spec["username"],
        password=spec["password"],
        security_token=spec.get("security_token", ""),
        domain=spec.get("domain", "login"),
    )
    records = sf.query_all(spec["soql"])["records"]
    df = pd.DataFrame(records)
    return df.drop(columns=["attributes"], errors="ignore")


def _convert(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _convert(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_convert(v) for v in value]
    return value


def scan_table(table: Any, **kwargs: Any) -> list[dict[str, Any]]:
    """Scan a boto3 DynamoDB ``Table``, following ``LastEvaluatedKey`` pagination."""
    items: list[dict[str, Any]] = []
    resp = table.scan(**kwargs)
    items.extend(resp.get("Items", []))
    while resp.get("LastEvaluatedKey"):
        resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"], **kwargs)
        items.extend(resp.get("Items", []))
    return [_convert(i) for i in items]


@register("dynamodb")
def load_dynamodb(spec: dict[str, Any]) -> pd.DataFrame:
    """Scan a DynamoDB table into a DataFrame (Decimals become floats)."""
    import boto3  # lazy: optional dependency

    kwargs: dict[str, Any] = {}
    if spec.get("region"):
        kwargs["region_name"] = spec["region"]
    if spec.get("endpoint_url"):
        kwargs["endpoint_url"] = spec["endpoint_url"]
    table = boto3.resource("dynamodb", **kwargs).Table(spec["table"])
    return pd.DataFrame(scan_table(table))
