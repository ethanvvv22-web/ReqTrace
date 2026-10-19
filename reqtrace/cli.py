"""Command line entry point.

  python -m reqtrace.cli maintain
  python -m reqtrace.cli select C1 [--mode rules|ai|hybrid]
  python -m reqtrace.cli assess C1 data/responses/C1_supplierA.csv [--mode ...]
  python -m reqtrace.cli export C1 data/responses/C1_supplierA.csv --out out/
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import maintain, supplier
from .export import deviations_markdown, matrix_rows, spec_markdown, write_matrix
from .llm import LLM
from .model import ProcurementCase
from .select import extract_features, select_requirements
from .store import ROOT, open_store

CASES = ROOT / "data" / "cases.json"


def load_cases() -> dict[str, ProcurementCase]:
    items = json.loads(CASES.read_text(encoding="utf-8"))
    return {c["id"]: ProcurementCase.model_validate(c) for c in items}


def _print_mode(mode: str, llm: LLM) -> None:
    if mode != "rules" and not llm.available:
        print(f"[note] GROQ_API_KEY not set - '{mode}' falls back to rules "
              f"(cached AI answers are still used if present)\n")


def cmd_maintain(args) -> int:
    store = open_store()
    llm = LLM()
    findings = maintain.check_library(store.current())
    for f in findings:
        print(f"[{f.kind:15}] {', '.join(f.req_ids):10} {f.message}")
        if f.proposal:
            shown = {k: v for k, v in f.proposal.items() if not k.startswith("_")}
            print(f"{'':28} proposal: {shown}")
    if args.ai:
        _print_mode("ai", llm)
        for f in [f for f in findings if f.kind == "vague"]:
            draft = maintain.propose_rewrite(store.get(f.req_ids[0]), llm)
            if draft:
                print(f"[ai draft] {f.req_ids[0]}: {draft.proposal['text']}")
    print(f"\n{len(findings)} findings - proposals are applied only after review "
          f"(streamlit run app.py)")
    return 0


def cmd_select(args) -> int:
    case = load_cases()[args.case]
    llm = LLM()
    _print_mode(args.mode, llm)
    hits, notes = extract_features(case.description, args.mode, llm)
    print(f"{case.id} {case.title}\n")
    for h in hits:
        print(f"  feature {h.feature:18} [{h.source:7}] \"{h.evidence}\"")
    for n in notes:
        print(f"  note: {n}")
    sel = select_requirements({h.feature for h in hits}, open_store().current())
    print(f"\n{len(sel)} requirements selected:")
    for s in sel:
        print(f"  {s.req.id} {s.req.title:38} <- {', '.join(s.because) or 'always'}")
    return 0


def _assess(case_id: str, path: str, mode: str):
    store = open_store()
    lib = {r.id: r for r in store.current(include_retired=True)}
    llm = LLM()
    _print_mode(mode, llm)
    answers = supplier.load_answers(path)
    out = []
    for a in answers:
        req = lib.get(a.req_id)
        if req is None:
            from .model import Assessment, Compliance
            out.append(Assessment(req_id=a.req_id, status=Compliance.UNCLEAR, confidence=0,
                                  layer="rule", flags=["unknown requirement id"]))
        else:
            out.append(supplier.assess(req, a, mode, llm))
    return answers, out, lib


def cmd_assess(args) -> int:
    answers, results, _ = _assess(args.case, args.response, args.mode)
    flagged = 0
    for a, r in zip(answers, results):
        mark = "REVIEW" if r.needs_review else "ok"
        flagged += r.needs_review
        print(f"{r.req_id} {r.status.value:13} {r.layer:7} {r.confidence:.2f} {mark:6} "
              f"{a.answer[:60]}")
        for fl in r.flags:
            print(f"{'':37}! {fl}")
    print(f"\n{flagged}/{len(results)} answers need human review")
    return 0


def cmd_export(args) -> int:
    case = load_cases()[args.case]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    store = open_store()
    hits, _ = extract_features(case.description, args.mode, LLM())
    sel = select_requirements({h.feature for h in hits}, store.current())
    (out / f"{case.id}_specification.md").write_text(spec_markdown(case, hits, sel), encoding="utf-8")
    written = [out / f"{case.id}_specification.md"]
    if args.response:
        answers, results, lib = _assess(case.id, args.response, args.mode)
        rows = matrix_rows(answers, results, lib)
        stem = Path(args.response).stem
        written += write_matrix(rows, out / f"{stem}_compliance_matrix")
        (out / f"{stem}_deviations.md").write_text(deviations_markdown(rows), encoding="utf-8")
        written.append(out / f"{stem}_deviations.md")
    for p in written:
        print(f"wrote {p}")
    return 0


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="reqtrace")
    sub = p.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("maintain")
    m.add_argument("--ai", action="store_true", help="draft rewrites for vague requirements")
    m.set_defaults(fn=cmd_maintain)
    for name, fn in (("select", cmd_select), ("assess", cmd_assess), ("export", cmd_export)):
        sp = sub.add_parser(name)
        sp.add_argument("case")
        if name == "assess":
            sp.add_argument("response")
        if name == "export":
            sp.add_argument("response", nargs="?")
            sp.add_argument("--out", default="out")
        sp.add_argument("--mode", choices=["rules", "ai", "hybrid"], default="hybrid")
        sp.set_defaults(fn=fn)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
