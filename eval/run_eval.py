"""Ablation: rules vs AI vs hybrid, for requirement selection and supplier analysis.

    python -m eval.run_eval            # writes eval/results.md

Read eval/README.md before quoting any number from this: the corpus is small
and was written for this prototype, and labels are marked with who made them.
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from reqtrace import supplier
from reqtrace.llm import LLM
from reqtrace.model import Compliance, ProcurementCase
from reqtrace.select import extract_features, select_requirements
from reqtrace.store import ROOT, open_store

MODES = ["rules", "ai", "hybrid"]
EVAL = ROOT / "eval"


def _prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def eval_selection(llm: LLM, lib) -> tuple[list[str], dict]:
    cases = [ProcurementCase.model_validate(c)
             for c in json.loads((ROOT / "data" / "cases.json").read_text(encoding="utf-8"))]
    lines, summary = [], {}
    for mode in MODES:
        ft = Counter()
        rt = Counter()
        missed_reqs: list[str] = []
        ran_ai = True
        for c in cases:
            hits, notes = extract_features(c.description, mode, llm)
            if any("AI layer unavailable" in n for n in notes):
                ran_ai = False
            got, gold = {h.feature for h in hits}, set(c.features)
            ft.update(tp=len(got & gold), fp=len(got - gold), fn=len(gold - got))
            sel = {s.req.id for s in select_requirements(got, lib)}
            want = {s.req.id for s in select_requirements(gold, lib)}
            rt.update(tp=len(sel & want), fp=len(sel - want), fn=len(want - sel))
            missed_reqs += [f"{c.id}:{r}" for r in sorted(want - sel)]
        if mode != "rules" and not ran_ai:
            summary[mode] = None
            continue
        summary[mode] = {"feature": _prf(ft["tp"], ft["fp"], ft["fn"]),
                         "req": _prf(rt["tp"], rt["fp"], rt["fn"]),
                         "missed": len(missed_reqs)}
    lines += ["## 1. Requirement selection (8 cases)", "",
              "| Mode | Feature P / R | Requirement P / R | Applicable requirements missed |",
              "|---|---|---|---|"]
    for mode in MODES:
        s = summary[mode]
        if s is None:
            lines.append(f"| {mode} | not run (no GROQ_API_KEY, no cache) | | |")
        else:
            fp_, fr, _ = s["feature"]
            rp, rr, _ = s["req"]
            lines.append(f"| {mode} | {fp_:.2f} / {fr:.2f} | {rp:.2f} / {rr:.2f} | {s['missed']} |")
    labelers = Counter(c.labeled_by or "unlabeled" for c in cases)
    lines += ["", f"Case labels by: {dict(labelers)}", ""]
    return lines, summary


def eval_supplier(llm: LLM, lib) -> tuple[list[str], dict]:
    with open(EVAL / "gold_responses.csv", encoding="utf-8", newline="") as f:
        gold_rows = list(csv.DictReader(f))
    gold = {(g["response"], g["req_id"]): Compliance(g["gold"]) for g in gold_rows}
    answers = {}
    for path in sorted((ROOT / "data" / "responses").glob("*.csv")):
        for a in supplier.load_answers(path):
            answers[(path.stem, a.req_id)] = a

    labels = list(Compliance)
    lines, summary = [], {}
    for mode in MODES:
        pairs, flagged, silent_miss, ran_ai = [], 0, [], True
        for key, g in gold.items():
            a = supplier.assess(lib[key[1]], answers[key], mode, llm)
            if any(fl.startswith("AI unavailable") for fl in a.flags):
                ran_ai = False
            pairs.append((g, a.status))
            flagged += a.needs_review
            # the costly error: a real deviation passes without review
            if g != Compliance.COMPLIANT and not a.needs_review:
                silent_miss.append(f"{key[0]}:{key[1]}")
        if mode != "rules" and not ran_ai:
            summary[mode] = None
            continue
        acc = sum(g == p for g, p in pairs) / len(pairs)
        f1s = []
        for lab in labels:
            tp = sum(g == lab and p == lab for g, p in pairs)
            fp = sum(g != lab and p == lab for g, p in pairs)
            fn = sum(g == lab and p != lab for g, p in pairs)
            f1s.append(_prf(tp, fp, fn)[2])
        summary[mode] = {"acc": acc, "macro_f1": sum(f1s) / len(f1s), "flagged": flagged,
                         "n": len(pairs), "silent_miss": silent_miss}
    lines += [f"## 2. Supplier answer classification ({len(gold)} answers, 2 suppliers)", "",
              "| Mode | Accuracy | Macro-F1 | Sent to human review | Deviations passed without review |",
              "|---|---|---|---|---|"]
    for mode in MODES:
        s = summary[mode]
        if s is None:
            lines.append(f"| {mode} | not run (no GROQ_API_KEY, no cache) | | | |")
        else:
            lines.append(f"| {mode} | {s['acc']:.2f} | {s['macro_f1']:.2f} | "
                         f"{s['flagged']}/{s['n']} | {len(s['silent_miss'])} "
                         f"{'(' + ', '.join(s['silent_miss']) + ')' if s['silent_miss'] else ''} |")
    labelers = Counter(g["labeled_by"] for g in gold_rows)
    verified = sum(1 for g in gold_rows if g["verified_by"].strip())
    lines += ["", f"Answer labels by: {dict(labelers)}; human-verified: {verified}/{len(gold_rows)}", ""]
    return lines, summary


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    llm = LLM()
    store = open_store()
    lib_list = store.current(include_retired=True)
    lib = {r.id: r for r in lib_list}
    active = [r for r in lib_list if r.status.value != "retired"]

    out = [f"# Evaluation results ({date.today().isoformat()})", "",
           f"AI model: `{llm.model}` via Groq" + ("" if llm.available else
           " - **GROQ_API_KEY not set**; AI rows use cached answers only, or are marked not run"),
           "", "See eval/README.md for what these numbers do and do not show.", ""]
    sel_lines, _ = eval_selection(llm, active)
    sup_lines, _ = eval_supplier(llm, lib)
    out += sel_lines + sup_lines
    text = "\n".join(out)
    (EVAL / "results.md").write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
