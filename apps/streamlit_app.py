"""Streamlit UI: Suite Builder, Run, Results, History. Run with ``streamlit run apps/streamlit_app.py``."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps import bootstrap  # noqa: E402
from apps import ui_helpers as ui  # noqa: E402
from recon import runner  # noqa: E402
from recon.config import (  # noqa: E402
    AGG_FUNCS,
    Aggregate,
    ConfigError,
    Options,
    Suite,
    TestConfig,
    Thresholds,
    load_suite,
    save_suite,
    suite_yaml,
)
from recon.engine import TestResult  # noqa: E402
from recon.report import render_html  # noqa: E402

SUITES_DIR = ROOT / "suites"
# categorical slots 1-4 of the reference palette, in fixed order
BLUE, ORANGE, AQUA, YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
STATUS_ICON = {"PASS": "✅ PASS", "FAIL": "❌ FAIL", "ERROR": "⚠️ ERROR"}

st.set_page_config(page_title="Data Reconciliation", page_icon="🔍", layout="wide")


@st.cache_resource(show_spinner="Preparing demo data (first start only)…")
def _bootstrap() -> str:
    """Seed demo data once per server process; in the cloud also redirect output to a temp dir."""
    return str(bootstrap.ensure_demo_data())


_bootstrap()


# ------------------------------------------------------------------ shared
@st.cache_data(show_spinner=False)
def cached_preview(spec_json: str, n: int = 50) -> pd.DataFrame:
    """Cached first-n-rows preview keyed by the (unresolved) spec."""
    return ui.load_preview(json.loads(spec_json), n)


def result_from(d: dict[str, Any]) -> TestResult:
    return TestResult.from_dict(d)


# ------------------------------------------------------------------ Suite Builder
def connector_form(prefix: str, spec: dict[str, Any], label: str) -> dict[str, Any]:
    """Render fields for one side; the fields change with the connector type."""
    st.markdown(f"**{label}**")
    cur = spec.get("type", "csv")
    ctype = st.selectbox("Connector type", ui.ALL_TYPES, index=ui.ALL_TYPES.index(cur) if cur in ui.ALL_TYPES else 0,
                         key=f"{prefix}:type")
    out: dict[str, Any] = {"type": ctype}
    group = ui.type_group(ctype)
    if group == "sql":
        out["url"] = st.text_input("SQLAlchemy URL", spec.get("url", ""), key=f"{prefix}:url",
                                   help="Use ${VAR} for secrets, e.g. snowflake://user:${SF_PASSWORD}@account/db")
        mode = st.radio("Read", ["query", "table"], horizontal=True, key=f"{prefix}:mode",
                        index=0 if spec.get("query") or not spec.get("table") else 1)
        if mode == "query":
            out["query"] = st.text_area("SQL query", spec.get("query", ""), key=f"{prefix}:query", height=90)
        else:
            out["table"] = st.text_input("Table", spec.get("table", ""), key=f"{prefix}:table")
            if schema := st.text_input("Schema (optional)", spec.get("schema", ""), key=f"{prefix}:schema"):
                out["schema"] = schema
            if where := st.text_input("WHERE (optional)", spec.get("where", ""), key=f"{prefix}:where"):
                out["where"] = where
        if st.checkbox("Lowercase column names", bool(spec.get("lowercase_columns")), key=f"{prefix}:lc"):
            out["lowercase_columns"] = True
    elif group == "file":
        out["path"] = st.text_input("Path (local or s3://)", spec.get("path", ""), key=f"{prefix}:path")
        if ctype == "fwf":
            out["widths"] = ui.parse_list(st.text_input("Column widths", ", ".join(map(str, spec.get("widths", []))),
                                                        key=f"{prefix}:widths"), int)
            out["names"] = ui.parse_list(st.text_input("Column names", ", ".join(spec.get("names", [])), key=f"{prefix}:names"))
        if ctype == "xlsx" and (sheet := st.text_input("Sheet name (optional)", (spec.get("read_options") or {}).get("sheet_name", ""),
                                                       key=f"{prefix}:sheet")):
            out["read_options"] = {"sheet_name": sheet}
    elif ctype == "salesforce":
        for k in ("username", "password", "security_token"):
            out[k] = st.text_input(k, spec.get(k, "${SF_" + k.upper() + "}"), key=f"{prefix}:{k}",
                                   help="Reference an environment variable, never a literal secret")
        out["soql"] = st.text_area("SOQL", spec.get("soql", ""), key=f"{prefix}:soql", height=90)
    else:
        out["table"] = st.text_input("DynamoDB table", spec.get("table", ""), key=f"{prefix}:table")
        out["region"] = st.text_input("Region", spec.get("region", "us-east-1"), key=f"{prefix}:region")
    return out


def pick(label: str, options: list[str], default: list[str], key: str) -> list[str]:
    """Multiselect that never drops values already in the config."""
    opts = list(dict.fromkeys([*options, *default]))
    return st.multiselect(label, opts, default=default, key=key)


def builder_page() -> None:
    st.header("Suite Builder")
    files = ui.list_suite_files(SUITES_DIR)
    choice = st.selectbox("Suite", ["➕ New suite", *files], index=1 if files else 0)
    if st.session_state.get("loaded_choice") != choice:
        st.session_state["loaded_choice"] = choice
        st.session_state["suite"] = Suite(suite="new_suite") if choice == "➕ New suite" else load_suite(SUITES_DIR / choice, resolve=False)
        st.session_state["suite_file"] = "new_suite.yaml" if choice == "➕ New suite" else choice
    suite: Suite = st.session_state["suite"]

    c1, c2 = st.columns([1, 2])
    suite.suite = c1.text_input("Suite name", suite.suite, key=f"sn:{choice}")
    suite.description = c2.text_input("Description", suite.description, key=f"sd:{choice}")

    names = [t.name for t in suite.tests]
    if "pending_sel" in st.session_state:  # applied before the widget exists (Streamlit forbids setting it afterwards)
        st.session_state[f"sel:{choice}"] = st.session_state.pop("pending_sel")
    sel = st.selectbox("Test", ["➕ New test", *names], key=f"sel:{choice}")
    is_new = sel == "➕ New test"
    cur = TestConfig(name="new_test", source={"type": "csv"}, target={"type": "csv"}, keys=["id"]) if is_new else next(t for t in suite.tests if t.name == sel)
    tkey = f"{choice}:{sel}"

    with st.container(border=True):
        name = st.text_input("Test name", "" if is_new else cur.name, key=f"{tkey}:name")
        enabled = st.checkbox("Enabled", cur.enabled, key=f"{tkey}:en")
        left, right = st.columns(2)
        with left:
            source = connector_form(f"{tkey}:src", cur.source, "Source")
        with right:
            target = connector_form(f"{tkey}:tgt", cur.target, "Target")

        st.markdown("**Preview** — load the first 50 rows of each side to pick columns from dropdowns")
        if st.button("Preview source & target", key=f"{tkey}:prev"):
            try:
                st.session_state[f"prev:{tkey}"] = (cached_preview(json.dumps(source, sort_keys=True)),
                                                    cached_preview(json.dumps(target, sort_keys=True)))
            except Exception as exc:  # noqa: BLE001
                st.error(f"Preview failed: {exc}")
        prev = st.session_state.get(f"prev:{tkey}")
        s_cols = [str(c) for c in prev[0].columns] if prev else []
        t_cols = [str(c) for c in prev[1].columns] if prev else []
        if prev:
            a, b = st.columns(2)
            a.caption(f"Source columns: {', '.join(s_cols)}")
            a.dataframe(prev[0], width="stretch", height=180)
            b.caption(f"Target columns: {', '.join(t_cols)}")
            b.dataframe(prev[1], width="stretch", height=180)

        keys = pick("Key column(s)", s_cols, [] if is_new else cur.keys, f"{tkey}:keys")
        only_s, only_t = ui.unmatched_columns(s_cols, t_cols)
        column_map: dict[str, str] = {}
        if only_s and only_t:
            st.caption("Source columns with no same-named target column — map them:")
            for c in only_s:
                opts = ["(no mapping)", *only_t]
                dflt = cur.column_map.get(c)
                pick_t = st.selectbox(f"{c} →", opts, index=opts.index(dflt) if dflt in opts else 0, key=f"{tkey}:map:{c}")
                if pick_t != "(no mapping)":
                    column_map[c] = pick_t
        for k, v in cur.column_map.items():  # keep mappings that cannot be re-derived without a preview
            if not prev:
                column_map[k] = v
        all_cols = list(dict.fromkeys([*s_cols, *t_cols]))
        ignore = pick("Ignore columns", all_cols, cur.ignore_columns, f"{tkey}:ign")
        num = ui.numeric_columns(prev[0]) if prev else []
        tol_cols = pick("Numeric tolerance on columns", num, list(cur.tolerance), f"{tkey}:tolc")
        tolerance = {c: st.number_input(f"Tolerance for {c}", min_value=0.0, value=float(cur.tolerance.get(c, 0.01)),
                                        format="%.6f", key=f"{tkey}:tol:{c}") for c in tol_cols}

        st.markdown("**Aggregates**")
        agg_df = pd.DataFrame([{"column": a.column, "func": a.func, "tolerance": a.tolerance} for a in cur.aggregates],
                              columns=["column", "func", "tolerance"])
        col_cfg = (st.column_config.SelectboxColumn("column", options=list(dict.fromkeys([*s_cols, *agg_df["column"]])), required=True)
                   if s_cols else st.column_config.TextColumn("column", required=True))
        agg_edit = st.data_editor(agg_df, num_rows="dynamic", key=f"{tkey}:agg", width="stretch", column_config={
            "column": col_cfg,
            "func": st.column_config.SelectboxColumn("func", options=list(AGG_FUNCS), required=True, default="sum"),
            "tolerance": st.column_config.NumberColumn("tolerance", min_value=0.0, default=0.0, format="%.6f"),
        })

        o = cur.options
        st.markdown("**Options & thresholds**")
        o1, o2, o3, o4 = st.columns(4)
        opts = Options(
            trim_strings=o1.checkbox("Trim strings", o.trim_strings, key=f"{tkey}:o1"),
            case_insensitive=o2.checkbox("Case insensitive", o.case_insensitive, key=f"{tkey}:o2"),
            null_equals_null=o3.checkbox("NULL equals NULL", o.null_equals_null, key=f"{tkey}:o3"),
            empty_string_as_null=o4.checkbox("Empty string = NULL", o.empty_string_as_null, key=f"{tkey}:o4"),
            max_mismatch_samples=int(st.number_input("Max mismatch samples", 1, 100000, o.max_mismatch_samples, key=f"{tkey}:o5")),
        )
        t = cur.thresholds
        h1, h2, h3, h4 = st.columns(4)
        th = Thresholds(
            max_missing_pct=h1.number_input("Max missing %", 0.0, 100.0, float(t.max_missing_pct), key=f"{tkey}:t1"),
            max_mismatch_pct=h2.number_input("Max mismatch %", 0.0, 100.0, float(t.max_mismatch_pct), key=f"{tkey}:t2"),
            allow_duplicate_keys=h3.checkbox("Allow duplicate keys", t.allow_duplicate_keys, key=f"{tkey}:t3"),
            allow_schema_drift=h4.checkbox("Allow schema drift", t.allow_schema_drift, key=f"{tkey}:t4"),
        )

        b1, b2, _ = st.columns([1, 1, 3])
        if b1.button("💾 Save test", type="primary", key=f"{tkey}:save"):
            try:
                aggs = [Aggregate(str(r["column"]), str(r["func"]), float(r["tolerance"] or 0))
                        for _, r in agg_edit.iterrows() if r["column"] and r["func"]]
                new = TestConfig(name=name, source=source, target=target, keys=keys, column_map=column_map,
                                 ignore_columns=ignore, tolerance=tolerance, options=opts, aggregates=aggs,
                                 thresholds=th, enabled=enabled)
                if is_new:
                    suite.tests.append(new)
                else:
                    suite.tests[names.index(sel)] = new
                Suite(suite=suite.suite, tests=suite.tests)  # validates duplicate names
                st.session_state["pending_sel"] = name
                st.rerun()
            except ConfigError as exc:
                if is_new and suite.tests and suite.tests[-1].name == name:
                    suite.tests.pop()
                st.error(str(exc))
        if not is_new and b2.button("🗑 Delete test", key=f"{tkey}:del"):
            suite.tests.pop(names.index(sel))
            st.session_state["pending_sel"] = "➕ New test"
            st.rerun()

    st.subheader("Suite YAML")
    fname = st.text_input("File name", st.session_state["suite_file"], key=f"fn:{choice}")
    if st.button("Save suite to YAML"):
        if "/" in fname or "\\" in fname or not fname.endswith((".yaml", ".yml")):
            st.error("File name must end in .yaml and contain no path separators")
        else:
            save_suite(suite, SUITES_DIR / fname)
            st.success(f"Saved suites/{fname} (placeholders such as ${{VAR}} are kept unresolved)")
    st.code(suite_yaml(suite), language="yaml")


# ------------------------------------------------------------------ Run
def run_page() -> None:
    st.header("Run")
    files = ui.list_suite_files(SUITES_DIR)
    if not files:
        st.info("No suites yet. Create one in Suite Builder.")
        return
    fname = st.selectbox("Suite", files)
    suite = load_suite(SUITES_DIR / fname)
    chosen = st.multiselect("Tests", [t.name for t in suite.tests], default=[t.name for t in suite.tests if t.enabled])
    workers = st.slider("Parallel workers", 1, 8, 4)
    if st.button("▶ Run reconciliation", type="primary", disabled=not chosen):
        bar, done = st.progress(0.0, text="Starting…"), []

        def on_result(r: TestResult) -> None:
            done.append(r)
            bar.progress(len(done) / len(chosen), text=f"{len(done)}/{len(chosen)} done — {r.name}: {r.status}")

        run = runner.run_suite(suite, only=chosen, workers=workers, on_result=on_result)
        st.session_state["last_run_id"] = run.run_id
        bar.progress(1.0, text="Finished")
        c = run.counts()
        m = st.columns(5)
        m[0].metric("Suite status", STATUS_ICON[run.status])
        m[1].metric("Tests", c["tests"])
        m[2].metric("Passed", c["passed"])
        m[3].metric("Failed", c["failed"])
        m[4].metric("Errors", c["errors"])
        st.dataframe(pd.DataFrame([{"test": r.name, "status": r.status, "match %": r.match_rate, "missing": r.missing_in_target,
                                    "extra": r.missing_in_source, "diff rows": r.mismatched_rows, "seconds": round(r.duration_sec, 2)}
                                   for r in run.results]), width="stretch", hide_index=True)
        st.caption(f"Run {run.run_id} saved. Open the Results page for details.")


# ------------------------------------------------------------------ Results
def donut(t: TestResult) -> go.Figure:
    parts = [("Identical", t.identical_rows, BLUE), ("Mismatched", t.mismatched_rows, ORANGE),
             ("Missing in target", t.missing_in_target, AQUA), ("Extra in target", t.missing_in_source, YELLOW)]
    parts = [p for p in parts if p[1] > 0]
    fig = go.Figure(go.Pie(labels=[p[0] for p in parts], values=[p[1] for p in parts], hole=0.6,
                           marker={"colors": [p[2] for p in parts], "line": {"color": "rgba(0,0,0,0)", "width": 2}},
                           textinfo="label+value", sort=False))
    fig.update_layout(showlegend=True, margin={"t": 10, "b": 10, "l": 10, "r": 10}, height=320,
                      annotations=[{"text": f"{t.match_rate:.1f}%<br>match", "showarrow": False, "font": {"size": 18}}])
    return fig


def results_page() -> None:
    st.header("Results")
    hist = runner.history(limit=100)
    if hist.empty:
        st.info("No runs yet. Use the Run page first.")
        return
    ids = hist["run_id"].tolist()
    default = ids.index(st.session_state["last_run_id"]) if st.session_state.get("last_run_id") in ids else 0
    label = {r.run_id: f"{r.suite} · {r.started_at[:19]} · {r.status} · {r.run_id}" for r in hist.itertuples()}
    run_id = st.selectbox("Run", ids, index=default, format_func=label.get)
    run = runner.load_run(run_id)
    st.subheader(f"{run['suite']} — {STATUS_ICON[run['status']]}")
    tests = [result_from(d) for d in run["tests"]]
    tname = st.selectbox("Test", [t.name for t in tests], format_func=lambda n: f"{STATUS_ICON[next(t.status for t in tests if t.name == n)]}  {n}")
    t = next(x for x in tests if x.name == tname)
    st.caption(f"{t.source_desc}  →  {t.target_desc}")
    if t.error:
        st.error(t.error)
    for f in t.failures if not t.error else []:
        st.warning(f)

    k = st.columns(6)
    k[0].metric("Match rate", f"{t.match_rate:.2f}%")
    k[1].metric("Source rows", f"{t.source_rows:,}")
    k[2].metric("Target rows", f"{t.target_rows:,}")
    k[3].metric("Missing in target", t.missing_in_target)
    k[4].metric("Mismatched rows", t.mismatched_rows)
    k[5].metric("Duplicate keys", f"{t.duplicate_keys_source}/{t.duplicate_keys_target}")

    if not t.error:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Mismatches by column**")
            if t.mismatches_by_column:
                df = pd.DataFrame({"column": list(t.mismatches_by_column), "mismatched cells": list(t.mismatches_by_column.values())})
                fig = px.bar(df, x="column", y="mismatched cells", text="mismatched cells", color_discrete_sequence=[BLUE])
                fig.update_layout(height=320, margin={"t": 10, "b": 10}, xaxis_title=None)
                st.plotly_chart(fig, width="stretch")
            else:
                st.success("No value mismatches")
        with c2:
            st.markdown("**Row outcome**")
            st.plotly_chart(donut(t), width="stretch")

        mm = t.samples.get("mismatches", pd.DataFrame())
        st.markdown(f"**Mismatch samples** ({len(mm)} shown of {sum(t.mismatches_by_column.values())} cells)")
        if len(mm):
            f1, f2 = st.columns(2)
            cols = f1.multiselect("Column", sorted(mm["column"].unique()))
            needle = f2.text_input("Search key / value")
            view = mm[mm["column"].isin(cols)] if cols else mm
            if needle:
                mask = view.astype(str).apply(lambda s: s.str.contains(needle, case=False, regex=False)).any(axis=1)
                view = view[mask]
            st.dataframe(view, width="stretch", hide_index=True)
        for key, title in (("missing_in_target", "Missing in target"), ("missing_in_source", "Extra in target (missing in source)"),
                           ("duplicates_source", "Duplicate keys in source"), ("duplicates_target", "Duplicate keys in target")):
            if len(t.samples.get(key, [])):
                with st.expander(f"{title} — sample"):
                    st.dataframe(t.samples[key], width="stretch", hide_index=True)
        if t.aggregates:
            st.markdown("**Aggregates**")
            adf = pd.DataFrame(t.aggregates)
            adf["result"] = adf["passed"].map({True: "✅ PASS", False: "❌ FAIL"})
            st.dataframe(adf.drop(columns=["passed"]), width="stretch", hide_index=True)
        sc = t.schema
        if sc.get("only_in_source") or sc.get("only_in_target"):
            st.markdown("**Schema drift**")
            st.write(f"Only in source: `{sc.get('only_in_source')}` · Only in target: `{sc.get('only_in_target')}`")
        if sc.get("columns"):
            with st.expander("Column types"):
                st.dataframe(pd.DataFrame(sc["columns"]), width="stretch", hide_index=True)

    d1, d2, d3 = st.columns(3)
    d1.download_button("⬇ HTML report", render_html(run), f"{run['suite']}_{run_id}.html", "text/html")
    d2.download_button("⬇ JSON", json.dumps(run, indent=2), f"{run['suite']}_{run_id}.json", "application/json")
    mm = t.samples.get("mismatches", pd.DataFrame())
    d3.download_button("⬇ Mismatches CSV", mm.to_csv(index=False), f"{t.name}_mismatches.csv", "text/csv", disabled=mm.empty)


# ------------------------------------------------------------------ History
def history_page() -> None:
    st.header("History")
    hist = runner.history(limit=200)
    if hist.empty:
        st.info("No runs recorded yet.")
        return
    st.dataframe(hist.drop(columns=["json_path", "html_path", "description"]), width="stretch", hide_index=True)
    th = runner.test_history(limit=2000)
    if len(th):
        st.markdown("**Match rate over time**")
        th["started_at"] = pd.to_datetime(th["started_at"], format="ISO8601", utc=True)
        fig = px.line(th, x="started_at", y="match_rate", color="test_name", markers=True,
                      color_discrete_sequence=[BLUE, ORANGE, AQUA, YELLOW], hover_data=["suite", "run_id"])
        fig.update_layout(height=360, yaxis_title="match rate %", xaxis_title=None, margin={"t": 10})
        st.plotly_chart(fig, width="stretch")
    rid = st.selectbox("Open a past report", hist["run_id"].tolist(),
                       format_func=lambda i: f"{hist.set_index('run_id').loc[i, 'suite']} · {hist.set_index('run_id').loc[i, 'started_at'][:19]} · {i}")
    try:
        html = render_html(runner.load_run(rid))
    except FileNotFoundError:
        st.error("The saved report file is no longer on disk.")
        return
    st.download_button("⬇ Download HTML", html, f"report_{rid}.html", "text/html")
    st.components.v1.html(html, height=700, scrolling=True)


PAGES = {"Suite Builder": builder_page, "Run": run_page, "Results": results_page, "History": history_page}
st.sidebar.title("🔍 Data Reconciliation")
PAGES[st.sidebar.radio("Navigate", list(PAGES))]()
