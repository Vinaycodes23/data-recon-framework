"""Generate deterministic demo data with known, planted defects.

Usage: python scripts/seed_demo.py [output_dir]      (default: demo_data)

Source  = demo_data/legacy_crm.db (SQLite: customers, orders, products, payments)
Target  = demo_data/warehouse/{orders.parquet, customers.csv, products.xlsx, payments.fwf}
The planted defects are printed and written to demo_data/planted_defects.json.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42
N_CUSTOMERS, N_ORDERS, N_PRODUCTS, N_PAYMENTS = 1000, 5000, 100, 3000

FIRST = ["Ava", "Liam", "Noah", "Emma", "Olivia", "Mia", "Lucas", "Zoe", "Ethan", "Ivy", "Raj", "Priya", "Chen", "Aiko"]
LAST = ["Smith", "Patel", "Garcia", "Kim", "Nguyen", "Brown", "Singh", "Lopez", "Khan", "Rossi", "Meyer", "Sato"]
CITIES = ["Austin", "Boston", "Chicago", "Denver", "Seattle", "Miami", "Pune", "Berlin", "Tokyo"]
STATUSES = ["Shipped", "Pending", "Cancelled", "Delivered"]
CATEGORIES = ["Hardware", "Software", "Services", "Books"]
METHODS = ["card", "paypal", "bank", "cash"]


def build_source(rng: np.random.Generator) -> dict[str, pd.DataFrame]:
    """Create the clean 'legacy' tables."""
    ids = np.arange(1, N_CUSTOMERS + 1)
    first, last = rng.choice(FIRST, N_CUSTOMERS), rng.choice(LAST, N_CUSTOMERS)
    customers = pd.DataFrame({
        "customer_id": ids,
        "name": [f"{f} {ln}" for f, ln in zip(first, last, strict=True)],
        "email": [f"{f.lower()}.{ln.lower()}{i}@example.com" for f, ln, i in zip(first, last, ids, strict=True)],
        "city": rng.choice(CITIES, N_CUSTOMERS),
        "signup_date": (pd.Timestamp("2020-01-01") + pd.to_timedelta(rng.integers(0, 1500, N_CUSTOMERS), unit="D")).strftime("%Y-%m-%d"),
        "credit_limit": rng.integers(1, 20, N_CUSTOMERS) * 500,
    })
    orders = pd.DataFrame({
        "order_id": np.arange(1, N_ORDERS + 1),
        "cust_id": rng.integers(1, N_CUSTOMERS + 1, N_ORDERS),
        "product_id": rng.integers(1, N_PRODUCTS + 1, N_ORDERS),
        "amount": np.round(rng.uniform(5, 900, N_ORDERS), 2),
        "status": rng.choice(STATUSES, N_ORDERS),
        "order_date": (pd.Timestamp("2023-01-01") + pd.to_timedelta(rng.integers(0, 700, N_ORDERS), unit="D")).strftime("%Y-%m-%d"),
        "quantity": rng.integers(1, 10, N_ORDERS),
    })
    products = pd.DataFrame({
        "product_id": np.arange(1, N_PRODUCTS + 1),
        "sku": [f"SKU-{i:05d}" for i in range(1, N_PRODUCTS + 1)],
        "name": [f"Product {i}" for i in range(1, N_PRODUCTS + 1)],
        "price": np.round(rng.uniform(1, 500, N_PRODUCTS), 2),
        "category": rng.choice(CATEGORIES, N_PRODUCTS),
    })
    payments = pd.DataFrame({
        "payment_id": np.arange(1, N_PAYMENTS + 1),
        "order_id": rng.integers(1, N_ORDERS + 1, N_PAYMENTS),
        "amount": np.round(rng.uniform(5, 900, N_PAYMENTS), 2),
        "method": rng.choice(METHODS, N_PAYMENTS),
        "paid_date": (pd.Timestamp("2023-01-01") + pd.to_timedelta(rng.integers(0, 700, N_PAYMENTS), unit="D")).strftime("%Y-%m-%d"),
    })
    return {"customers": customers, "orders": orders, "products": products, "payments": payments}


def plant_orders(src: pd.DataFrame, rng: np.random.Generator) -> tuple[pd.DataFrame, dict]:
    """Build the warehouse orders table with planted defects."""
    tgt = src.rename(columns={"cust_id": "customer_id"}).copy()
    tgt["order_date"] = pd.to_datetime(tgt["order_date"])
    tgt["load_ts"] = pd.Timestamp("2024-06-01 02:00:00")
    pool = [int(x) for x in rng.permutation(src["order_id"].to_numpy())]
    missing, small, big = pool[:12], pool[12:22], pool[22:32]
    shipped = tgt[(tgt["status"] == "Shipped") & ~tgt["order_id"].isin(missing + small + big)]["order_id"].to_numpy()
    case_ids = [int(x) for x in rng.choice(shipped, 8, replace=False)]
    dup_ids = [int(x) for x in rng.choice([i for i in pool[32:] if i not in case_ids], 2, replace=False)]

    tgt.loc[tgt["order_id"].isin(small), "amount"] += 0.001
    tgt.loc[tgt["order_id"].isin(big), "amount"] += 2.50
    tgt.loc[tgt["order_id"].isin(case_ids), "status"] = "shipped"
    extra = pd.DataFrame({
        "order_id": np.arange(100001, 100006), "customer_id": 1, "product_id": 1,
        "amount": [10.0, 20.0, 30.0, 40.0, 50.0], "status": "Pending",
        "order_date": pd.Timestamp("2024-01-01"), "quantity": 1, "load_ts": pd.Timestamp("2024-06-01 02:00:00"),
    })
    dups = tgt[tgt["order_id"].isin(dup_ids)]
    tgt = tgt[~tgt["order_id"].isin(missing)]
    tgt = pd.concat([tgt, extra, dups], ignore_index=True)
    planted = {
        "missing_in_target": 12, "missing_in_source": 5, "amount_drift_within_tolerance": 10,
        "amount_drift_beyond_tolerance": 10, "status_case_differences": 8, "duplicate_keys_target": 2,
        "expected_mismatched_rows": 18, "expected_mismatches_by_column": {"amount": 10, "status": 8},
        "missing_ids": sorted(missing), "case_ids": sorted(case_ids), "big_drift_ids": sorted(big),
    }
    return tgt, planted


def write_fwf(df: pd.DataFrame, path: Path) -> None:
    """Write payments as fixed width: id(8) order(8) amount(12) method(10) date(10)."""
    with open(path, "w", encoding="utf-8") as fh:
        for r in df.itertuples(index=False):
            fh.write(f"{r.payment_id:08d}{r.order_id:08d}{r.amount:012.2f}{r.method:<10}{r.paid_date}\n")


def seed(out: Path | str = "demo_data") -> dict:
    """Generate all demo data under ``out`` and return the planted-defect manifest."""
    out = Path(out)
    wh = out / "warehouse"
    wh.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(SEED)
    src = build_source(rng)

    db = out / "legacy_crm.db"
    db.unlink(missing_ok=True)
    with sqlite3.connect(db) as con:
        for name, df in src.items():
            df.to_sql(name, con, index=False)

    orders_t, planted_orders = plant_orders(src["orders"], rng)
    orders_t.to_parquet(wh / "orders.parquet", index=False)

    cust = src["customers"].copy()
    null_email = [int(x) for x in rng.choice(cust["customer_id"], 3, replace=False)]
    padded = [int(x) for x in rng.choice(cust["customer_id"], 15, replace=False)]
    cust.loc[cust["customer_id"].isin(null_email), "email"] = None
    cust.loc[cust["customer_id"].isin(padded), "name"] = cust["name"] + "  "
    cust["loyalty_tier"] = rng.choice(["bronze", "silver", "gold"], len(cust))  # schema drift: target only
    cust.to_csv(wh / "customers.csv", index=False)

    src["products"].to_excel(wh / "products.xlsx", index=False)

    pay = src["payments"]
    pay_t = pay.copy()
    pool = [int(x) for x in rng.permutation(pay["payment_id"].to_numpy())]
    pay_missing, pay_off = pool[:3], pool[3:9]
    pay_t.loc[pay_t["payment_id"].isin(pay_off), "amount"] += 1.00
    write_fwf(pay_t[~pay_t["payment_id"].isin(pay_missing)], wh / "payments.fwf")

    manifest = {
        "orders": planted_orders,
        "customers": {"emails_nulled": 3, "names_with_trailing_whitespace": 15, "expected_mismatched_rows": 3,
                      "schema_drift_target_only": ["loyalty_tier"], "email_null_ids": sorted(null_email)},
        "products": {"expected": "PASS (perfect reconciliation)"},
        "payments": {"missing_in_target": 3, "amount_off_by_1.00": 6, "format": "fixed-width with zero-padded numbers"},
        "rows": {k: len(v) for k, v in src.items()},
    }
    (out / "planted_defects.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def main() -> None:
    """CLI entry."""
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("demo_data")
    m = seed(out)
    o = m["orders"]
    print(f"Demo data written to {out}/  (rows: {m['rows']})\n\nPlanted defects")
    print(f"  orders    : {o['missing_in_target']} missing in target, {o['missing_in_source']} extra in target, "
          f"{o['duplicate_keys_target']} duplicate keys in target")
    print(f"              {o['amount_drift_within_tolerance']} amount drifts within tolerance (+0.001), "
          f"{o['amount_drift_beyond_tolerance']} beyond (+2.50), {o['status_case_differences']} status case differences")
    print("              renamed column cust_id -> customer_id; extra load_ts column in target (ignored)")
    c = m["customers"]
    print(f"  customers : {c['emails_nulled']} emails nulled, {c['names_with_trailing_whitespace']} names with trailing "
          f"whitespace (ignored by trim), schema drift column {c['schema_drift_target_only']} only in target")
    print("  products  : identical (should PASS)")
    p = m["payments"]
    print(f"  payments  : {p['missing_in_target']} missing in target, {p['amount_off_by_1.00']} amounts off by 1.00 (fixed-width target)")


if __name__ == "__main__":
    main()
