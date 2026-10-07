"""Self-contained HTML report (no external assets, mobile friendly, all values escaped)."""

from __future__ import annotations

import html
from typing import Any

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#1b2430;--mut:#667085;--bd:#e4e7ec;--ok:#0f9d58;--bad:#d93025;--warn:#b26a00;--acc:#2f5bea}
@media (prefers-color-scheme:dark){:root{--bg:#10141a;--card:#181e27;--fg:#e6eaf0;--mut:#9aa4b2;--bd:#2a3340;--ok:#34c47c;--bad:#ff6b5e;--warn:#e0a030;--acc:#7b9cff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1100px;margin:0 auto;padding:16px}
h1{font-size:1.5rem;margin:.2em 0}h2{font-size:1.2rem;margin:0}h3{font-size:1rem;margin:1.2em 0 .4em;color:var(--mut);text-transform:uppercase;letter-spacing:.04em;font-size:.78rem}
.card{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:16px;margin:16px 0}
.head{display:flex;flex-wrap:wrap;gap:8px;align-items:center;justify-content:space-between}
.badge{display:inline-block;padding:2px 10px;border-radius:999px;font-weight:600;font-size:.8rem;color:#fff}
.PASS{background:var(--ok)}.FAIL{background:var(--bad)}.ERROR{background:var(--warn)}
.mut{color:var(--mut);font-size:.85rem;word-break:break-all}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin:12px 0}
.kpi{border:1px solid var(--bd);border-radius:10px;padding:8px 10px}.kpi b{display:block;font-size:1.25rem}.kpi span{color:var(--mut);font-size:.75rem}
.bar{height:10px;border-radius:5px;background:var(--bd);overflow:hidden}.bar i{display:block;height:100%;background:var(--ok)}
ul.fail{margin:.4em 0;padding-left:1.2em;color:var(--bad)}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:.85rem}
th,td{border-bottom:1px solid var(--bd);padding:5px 8px;text-align:left;white-space:nowrap}th{color:var(--mut);font-weight:600}
.ok{color:var(--ok)}.bad{color:var(--bad)}
@media (max-width:600px){main{padding:10px}.card{padding:12px}}
"""


def esc(v: Any) -> str:
    """HTML-escape any value (None becomes an empty-looking dash)."""
    return "—" if v is None else html.escape(str(v))


def _num(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:,.4f}".rstrip("0").rstrip(".")
    if isinstance(v, int) and not isinstance(v, bool):
        return f"{v:,}"
    return esc(v)


def _table(rows: list[dict[str, Any]], cols: list[str] | None = None, limit: int = 50) -> str:
    if not rows:
        return '<p class="mut">None.</p>'
    cols = cols or list(rows[0].keys())
    head = "".join(f"<th>{esc(c)}</th>" for c in cols)
    body = "".join("<tr>" + "".join(f"<td>{_num(r.get(c))}</td>" for c in cols) + "</tr>" for r in rows[:limit])
    more = f'<p class="mut">Showing {limit} of {len(rows)} rows.</p>' if len(rows) > limit else ""
    return f'<div class="scroll"><table><tr>{head}</tr>{body}</table></div>{more}'


def _kpi(label: str, value: Any) -> str:
    return f'<div class="kpi"><b>{_num(value)}</b><span>{esc(label)}</span></div>'


def _test_section(t: dict[str, Any]) -> str:
    out = [f'<section class="card"><div class="head"><h2>{esc(t["name"])}</h2>'
           f'<span class="badge {esc(t["status"])}">{esc(t["status"])}</span></div>',
           f'<p class="mut">{esc(t.get("source_desc"))}<br>&rarr; {esc(t.get("target_desc"))}</p>']
    if t.get("error"):
        out.append(f'<ul class="fail"><li>{esc(t["error"])}</li></ul></section>')
        return "".join(out)
    rate = float(t.get("match_rate") or 0)
    out.append('<div class="kpis">' + "".join([
        _kpi("match rate %", rate), _kpi("source rows", t["source_rows"]), _kpi("target rows", t["target_rows"]),
        _kpi("identical", t["identical_rows"]), _kpi("mismatched rows", t["mismatched_rows"]),
        _kpi("missing in target", t["missing_in_target"]), _kpi("extra in target", t["missing_in_source"]),
        _kpi("dup keys (src/tgt)", f'{t["duplicate_keys_source"]}/{t["duplicate_keys_target"]}'),
        _kpi("seconds", t["duration_sec"]),
    ]) + f'</div><div class="bar" role="img" aria-label="match rate {rate}%"><i style="width:{min(max(rate, 0), 100)}%"></i></div>')
    if t.get("failures"):
        out.append("<h3>Failure reasons</h3><ul class='fail'>" + "".join(f"<li>{esc(f)}</li>" for f in t["failures"]) + "</ul>")
    if t.get("mismatches_by_column"):
        out.append("<h3>Mismatches by column</h3>" + _table([{"column": k, "mismatched cells": v} for k, v in t["mismatches_by_column"].items()]))
    if t.get("aggregates"):
        rows = [{**a, "result": "PASS" if a["passed"] else "FAIL", "note": a.get("error")} for a in t["aggregates"]]
        out.append("<h3>Aggregates</h3>" + _table(rows, ["column", "func", "source", "target", "difference", "tolerance", "result", "note"]))
    sc = t.get("schema") or {}
    if sc.get("only_in_source") or sc.get("only_in_target") or any(not c["kind_match"] for c in sc.get("columns", [])):
        out.append("<h3>Schema drift</h3>")
        out.append(f'<p>Only in source: <b>{esc(", ".join(sc.get("only_in_source", [])) or "none")}</b><br>'
                   f'Only in target: <b>{esc(", ".join(sc.get("only_in_target", [])) or "none")}</b></p>')
        kd = [c for c in sc.get("columns", []) if not c["kind_match"]]
        if kd:
            out.append(_table(kd, ["column", "source_dtype", "target_dtype", "source_kind", "target_kind"]))
    s = t.get("samples") or {}
    for key, title in (("mismatches", "Mismatch samples"), ("missing_in_target", "Missing in target (sample)"),
                       ("missing_in_source", "Extra in target (sample)"), ("duplicates_source", "Duplicate keys in source"),
                       ("duplicates_target", "Duplicate keys in target")):
        if s.get(key):
            out.append(f"<h3>{esc(title)}</h3>" + _table(s[key]))
    out.append("</section>")
    return "".join(out)


def render_html(run: dict[str, Any]) -> str:
    """Render a ``SuiteRun.to_dict()`` into one self-contained HTML document."""
    c = run["counts"]
    summary = "".join(
        f'<tr><td>{esc(t["name"])}</td><td><span class="badge {esc(t["status"])}">{esc(t["status"])}</span></td>'
        f'<td>{_num(t["match_rate"])}%</td><td>{_num(t["source_rows"])}</td><td>{_num(t["missing_in_target"])}</td>'
        f'<td>{_num(t["mismatched_rows"])}</td></tr>' for t in run["tests"]
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>Reconciliation report - {esc(run["suite"])}</title><style>{CSS}</style></head><body><main>'
        f'<div class="card"><div class="head"><h1>{esc(run["suite"])}</h1>'
        f'<span class="badge {esc(run["status"])}">{esc(run["status"])}</span></div>'
        f'<p class="mut">{esc(run.get("description"))}</p>'
        f'<p class="mut">Run {esc(run["run_id"])} &middot; {esc(run["started_at"])} &middot; {_num(run["duration_sec"])}s</p>'
        f'<div class="kpis">{_kpi("tests", c["tests"])}{_kpi("passed", c["passed"])}{_kpi("failed", c["failed"])}{_kpi("errors", c["errors"])}</div>'
        '<div class="scroll"><table><tr><th>Test</th><th>Status</th><th>Match</th><th>Source rows</th><th>Missing</th><th>Diff rows</th></tr>'
        f'{summary}</table></div></div>'
        + "".join(_test_section(t) for t in run["tests"])
        + "</main></body></html>"
    )
