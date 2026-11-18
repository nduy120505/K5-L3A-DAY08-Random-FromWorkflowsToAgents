"""CI gate: compare a PR eval report with the main-branch baseline using the metric spec.

    python -m lab9.gate --pr reports/eval_workflow.json --baseline reports/baseline_v1.json

exit 0 = merge, exit 1 = blocked. Safety metrics: max 0, no averaging away.
Capability metrics: meet the floor AND the paired CI of (PR - main) is not entirely below 0.
Thresholds may only get stricter on the same eval-set version (ratchet).
"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

from lab8.console import utf8_stdio
from lab9 import stats
from lab9.eval import SPEC, load_spec, per_item
from lab9.graders import GRADERS


def integrity_check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> list[str]:
    """M7 (Expert): refuse to compare reports that cannot be trusted. Return a list of problems
    (empty = trustworthy). A gate that passes a broken report is worse than no gate.
    Must catch at least:
      - any metric value in pr["metrics"] that is NaN or inf
      - len(pr["rows"]) != manifest items x len(manifest seeds)   (dropped / duplicated rows)
      - the (item_id, seed) set of PR differs from the baseline's (cherry-picked seeds/items)
      - pr["evaluator_failures"] non-empty, or a row whose value for a graded spec metric is
        None (a crashed grader silently scores as "missing" and drops out of the mean)
      - base_spec given and a threshold in spec is LOOSER than in base_spec for the same
        eval-set version (ratchet: max may not rise, min may not fall, a blocking metric may
        not become non-blocking, a metric may not disappear; the same for spec["slices"],
        including dropping no_significant_drop)
      - an item's slice tags in PR rows differ from the baseline's (relabelling an item out of a
        slice hides its regression from the slice gate)"""
    problems: list[str] = []
    graded = [m["id"] for m in spec.get("metrics", []) if m["id"] in GRADERS]

    # 1. Aggregates must be finite numbers (NaN < min is False: a naive gate passes it).
    for k, v in (pr.get("metrics") or {}).items():
        if v is not None and not _finite(v):
            problems.append(f"metric {k} is {v!r} (NaN/inf/non-numeric)")
    # A NaN threshold makes every comparison False as well.
    for where, entries in (("metric", spec.get("metrics", [])), ("slice", spec.get("slices") or [])):
        for m in entries:
            for tk, tv in _thresholds(m).items():
                if isinstance(tv, float) and not math.isfinite(tv):
                    problems.append(f"spec {where} {_key(m)} threshold {tk} is {tv!r}")

    # 2. Graders must have run on every row.
    if pr.get("evaluator_failures"):
        problems.append(f"{len(pr['evaluator_failures'])} evaluator failure(s): {pr['evaluator_failures'][0]}")
    rows = pr.get("rows") or []
    bad_cells = [(r.get("item_id"), r.get("seed"), m) for r in rows for m in graded
                 if not isinstance(r.get(m), bool)]
    if bad_cells:
        problems.append(f"{len(bad_cells)} row value(s) missing/non-boolean for graded metrics, "
                        f"first {bad_cells[0]}")

    # 3. Row count and (item, seed) coverage: nothing dropped, duplicated or cherry-picked.
    man, bman = pr.get("manifest") or {}, base.get("manifest") or {}
    want = (man.get("items") or 0) * len(man.get("seeds") or [])
    if len(rows) != want:
        problems.append(f"{len(rows)} rows, manifest says {man.get('items')} items x "
                        f"{len(man.get('seeds') or [])} seeds = {want}")
    keys = [(r.get("item_id"), r.get("seed")) for r in rows]
    seen: set = set()
    dup = [k for k in keys if k in seen or seen.add(k)]
    if dup:
        problems.append(f"{len(dup)} duplicated (item, seed) row(s), first {dup[0]}")
    base_keys = {(r.get("item_id"), r.get("seed")) for r in base.get("rows") or []}
    if set(keys) != base_keys:
        miss, extra = base_keys - set(keys), set(keys) - base_keys
        problems.append(f"(item, seed) set differs from baseline: {len(miss)} missing, {len(extra)} extra")
    if sorted(man.get("seeds") or []) != sorted(bman.get("seeds") or []) or man.get("items") != bman.get("items"):
        problems.append(f"manifest seeds/items {man.get('seeds')}/{man.get('items')} != baseline "
                        f"{bman.get('seeds')}/{bman.get('items')}")

    # 4. Slice tags per item must match the baseline's (no relabelling an item out of a slice).
    def tags(rep: dict) -> dict:
        out: dict = {}
        for r in rep.get("rows") or []:
            out.setdefault(r.get("item_id"), set()).update(r.get("slices") or [])
        return out
    pt, bt = tags(pr), tags(base)
    relabelled = sorted(str(i) for i in set(pt) & set(bt) if pt[i] != bt[i])
    if relabelled:
        problems.append(f"slice tags differ from baseline for {len(relabelled)} item(s), first {relabelled[0]}")

    # 5. Reported aggregates must be what the rows say (hand-edited metrics), and each row must
    #    be self-consistent: lab8's trial_pass cannot pass a trial that leaked a secret, moved
    #    money without approval, or left the wrong amount in the ledger (row values edited by hand).
    if not bad_cells and rows:
        problems += _recheck_aggregates(pr, graded, spec)
        broken = [(r.get("item_id"), r.get("seed")) for r in rows if r.get("pass_rate") is True and (
            r.get("secret_leak") is True or r.get("approval_bypass") is True
            or r.get("refund_amount_correct") is False)]
        if broken:
            problems.append(f"{len(broken)} row(s) pass pass_rate while failing a safety/amount grader, "
                            f"first {broken[0]} (rows edited?)")

    # 6. Ratchet: same eval-set version => thresholds may only get stricter.
    if base_spec is not None and base_spec.get("eval_set_version") == spec.get("eval_set_version"):
        problems += _ratchet(base_spec, spec)
    return problems


def _finite(v) -> bool:
    if isinstance(v, bool):
        return True
    return isinstance(v, (int, float)) and math.isfinite(v)


def _thresholds(entry: dict) -> dict:
    """Metric entries keep thresholds under "threshold"; slice entries keep them inline."""
    th = dict(entry.get("threshold") or {})
    for k in ("min", "max", "max_increase", "no_significant_drop"):
        if k in entry:
            th[k] = entry[k]
    return th


def _key(entry: dict) -> str:
    return f"{entry['name']}/{entry.get('metric')}" if "name" in entry else str(entry.get("id"))


def _recheck_aggregates(pr: dict, graded: list, spec: dict) -> list[str]:
    from lab9.eval import aggregate
    rows, reported = pr["rows"], pr.get("metrics") or {}
    try:
        fresh = aggregate(rows, graded, k_values=spec.get("pass_k", []))
    except (KeyError, TypeError, ValueError):            # rows without cost etc.: graded only
        fresh = {}
        for m in graded:
            vals = [r[m] for r in rows]
            fresh[m] = sum(vals) if m in ("secret_leak", "approval_bypass") else round(sum(vals) / len(vals), 4)
    out = []
    for k, v in fresh.items():
        r = reported.get(k)
        if v is None or r is None or not _finite(r) or not _finite(v):
            continue
        if abs(float(r) - float(v)) > 1e-3:
            out.append(f"metric {k} reported {r} but rows give {v}")
    return out


def _ratchet(old: dict, new: dict) -> list[str]:
    out: list[str] = []

    def compare(kind: str, before: list, after: list) -> None:
        now = {_key(e): e for e in after}
        for e in before:
            k = _key(e)
            if k not in now:
                out.append(f"ratchet: {kind} {k} removed")
                continue
            a, b = _thresholds(e), _thresholds(now[k])
            if e.get("blocking", kind == "slice") and not now[k].get("blocking", kind == "slice"):
                out.append(f"ratchet: {kind} {k} no longer blocking")
            if a.get("no_significant_drop") and not b.get("no_significant_drop"):
                out.append(f"ratchet: {kind} {k} dropped no_significant_drop")
            for t, looser in (("min", lambda x, y: y < x), ("max", lambda x, y: y > x),
                              ("max_increase", lambda x, y: y > x)):
                if t in a and (b.get(t) is None or not _finite(b[t]) or looser(a[t], b[t])):
                    out.append(f"ratchet: {kind} {k} {t} {a[t]} -> {b.get(t)}")

    compare("metric", old.get("metrics", []), new.get("metrics", []))
    compare("slice", old.get("slices") or [], new.get("slices") or [])
    for field in ("seeds", "bootstrap", "eval_set", "labels"):
        if old.get(field) != new.get(field):
            out.append(f"ratchet: spec {field} changed on the same eval-set version")
    return out


PASS_K = re.compile(r"pass_(at|hat)_(\d+)")


def slice_per_item(report: dict, metric: str, slice_name: str) -> dict[str, float]:
    """per_item for a slice; also understands pass_at_k / pass_hat_k, computed per item from the
    pass_rate rows (n seeds, c passes) so a slice can demand reliability, not just an average."""
    m = PASS_K.fullmatch(metric)
    if not m:
        return per_item(report, metric, slice_name=slice_name)
    fn = stats.pass_at_k if m.group(1) == "at" else stats.pass_hat_k
    k = int(m.group(2))
    acc: dict[str, list[bool]] = {}
    for r in report["rows"]:
        if slice_name in (r.get("slices") or []) and isinstance(r.get("pass_rate"), bool):
            acc.setdefault(r["item_id"], []).append(r["pass_rate"])
    return {i: fn(len(v), sum(v), k) for i, v in acc.items() if len(v) >= k}


def slice_check(pr: dict, base: dict, spec: dict) -> tuple[list[str], list[str]]:
    """M5 (Advanced): an average can hide a regression on a small, important slice.
    For each entry of spec["slices"] ({"name", "metric", "min"?, "no_significant_drop"?, "blocking"}):
      - value = mean of per_item(pr, metric, slice_name=name)
      - fail if below "min", or if no_significant_drop and the paired CI over the slice's items
        lies entirely below 0
    Return (blocked ids like "slice:money_movement/pass_rate", report lines)."""
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for s in spec.get("slices") or []:
        name, metric = s["name"], s["metric"]
        sid = f"slice:{name}/{metric}"
        a = slice_per_item(pr, metric, name)
        verdict, note, val = "ok", "", None
        if not a:
            verdict = "missing"                          # an empty slice cannot vouch for anything
        else:
            val = round(sum(a.values()) / len(a), 4)
            if ("min" in s and val < s["min"]) or ("max" in s and val > s["max"]):
                verdict = "fail"
            if s.get("no_significant_drop"):
                b = slice_per_item(base, metric, name)
                keys = sorted(set(a) & set(b))
                if keys:
                    d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                                       n_resamples=bs.get("n_resamples", 1000),
                                                       rng=bs.get("rng", 2027),
                                                       confidence=bs.get("confidence", 0.95))
                    note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
                    if hi < 0:
                        verdict = "fail"
        if verdict != "ok" and s.get("blocking", True):
            blocked.append(sid)
        th = {k: s[k] for k in ("min", "max", "no_significant_drop") if k in s}
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if s.get("blocking", True) else "warn")
        lines.append(f"  {sid:<34} {val!s:<8} {json.dumps(th):<40} {note:<28} {label}")
    return blocked, lines


def check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> tuple[list[str], list[str]]:
    if pr["manifest"]["eval_set_sha"] != base["manifest"]["eval_set_sha"]:
        return ["eval_set_sha"], ["  EVAL SET differs from the baseline's: re-run the baseline on the same eval set"]
    problems = integrity_check(pr, base, spec, base_spec)
    if problems:
        return ["integrity"], [f"  INTEGRITY {p}" for p in problems]
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for m in spec["metrics"]:
        mid, th, val = m["id"], m.get("threshold", {}), pr["metrics"].get(m["id"])
        verdict, note = "ok", ""
        if val is None:
            verdict = "missing"
        elif "max" in th and val > th["max"]:
            verdict = "fail"
        elif "min" in th and val < th["min"]:
            verdict = "fail"
        elif "max_increase" in th and base["metrics"].get(mid):
            if val > base["metrics"][mid] * (1 + th["max_increase"]):
                verdict = "fail"
        if th.get("no_significant_drop") and val is not None:
            a, b = per_item(pr, mid), per_item(base, mid)
            keys = sorted(set(a) & set(b))
            d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                               n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027))
            note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
            if hi < 0:
                verdict = "fail"
        if verdict != "ok" and m.get("blocking"):
            blocked.append(mid)
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if m.get("blocking") else "warn")
        lines.append(f"  {mid:<22} {val!s:<8} {json.dumps(th):<40} {note:<28} {label}")
    if spec.get("slices"):
        sb, sl = slice_check(pr, base, spec)
        blocked += sb
        lines += sl
    return blocked, lines


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="lab9.gate")
    ap.add_argument("--pr", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--spec", default=str(SPEC))
    ap.add_argument("--base-spec", help="metric spec on main, for the ratchet check (M7)")
    args = ap.parse_args(argv)
    pr = json.loads(Path(args.pr).read_text(encoding="utf-8"))
    base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    base_spec = load_spec(Path(args.base_spec)) if args.base_spec else None
    blocked, lines = check(pr, base, load_spec(Path(args.spec)), base_spec)
    print(f"gate: PR {pr['run_id']} vs main {base['run_id']} · eval set {pr['manifest']['eval_set_sha']}")
    print("\n".join(lines))
    if blocked:
        print(f"exit 1 — blocked: {', '.join(blocked)}")
        return 1
    print("exit 0 — merge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
