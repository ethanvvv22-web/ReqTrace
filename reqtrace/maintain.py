"""Requirements-library maintenance.

Deterministic checks find candidates; the optional AI layer only drafts
rewording for vague requirements. Every output is a *proposal* - nothing is
written to the library until a person approves it (see Store.new_version).
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from itertools import combinations

from .llm import LLM, LLMError
from .model import Requirement

DUPLICATE_THRESHOLD = 0.55
STALE_AFTER_DAYS = 3 * 365
VAGUE_TERMS = ["adequate", "sufficient", "appropriate", "user-friendly", "intuitive",
               "as far as possible", "where possible", "suitable", "reasonable", "robust"]
OUTDATED_REFS = [
    (re.compile(r"Directive 2006/42/EC,\s*Annex I"), "Reg. (EU) 2023/1230, Annex III",
     "Machinery Directive 2006/42/EC is replaced by Regulation (EU) 2023/1230 "
     "(applies from 20 Jan 2027); section numbering is largely retained - verify "
     "against the official text before approving"),
]

_STOP = set("the a an and or of to in on at for be shall with by that is are it its "
            "so can from as this".split())


@dataclass
class Finding:
    kind: str                     # duplicate | conflict | outdated_ref | vague | no_verification | stale
    req_ids: list[str]
    message: str
    proposal: dict = field(default_factory=dict)   # field changes for a new version
    layer: str = "rule"


def _tokens(text: str) -> Counter:
    return Counter(w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP)


def _cosine(a: Counter, b: Counter) -> float:
    dot = sum(a[k] * b[k] for k in a)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def check_library(reqs: list[Requirement], today: date | None = None) -> list[Finding]:
    today = today or date.today()
    out: list[Finding] = []
    toks = {r.id: _tokens(r.title + " " + r.text) for r in reqs}

    for a, b in combinations(reqs, 2):
        # conflict: same structured parameter, different limit
        if a.limit and b.limit and a.limit.param == b.limit.param and \
                (a.limit.op, a.limit.value) != (b.limit.op, b.limit.value):
            out.append(Finding("conflict", [a.id, b.id],
                               f"both constrain '{a.limit.param}' but disagree: "
                               f"{a.limit.op} {a.limit.value:g} vs {b.limit.op} {b.limit.value:g} "
                               f"{a.limit.unit}"))
            continue
        sim = _cosine(toks[a.id], toks[b.id])
        if sim >= DUPLICATE_THRESHOLD:
            older = min((a, b), key=lambda r: r.last_reviewed)
            keep = b if older is a else a
            out.append(Finding("duplicate", [a.id, b.id],
                               f"near-duplicate (similarity {sim:.2f}); consider retiring "
                               f"{older.id} in favour of {keep.id}",
                               {"status": "retired",
                                "rationale": f"Duplicate of {keep.id}.",
                                "_target": older.id}))

    for r in reqs:
        for pat, new, why in OUTDATED_REFS:
            if pat.search(r.source_ref):
                out.append(Finding("outdated_ref", [r.id], f"outdated reference: {why}",
                                   {"source_ref": pat.sub(new, r.source_ref)}))
        vague = [t for t in VAGUE_TERMS if re.search(rf"\b{re.escape(t)}\b", r.text, re.I)]
        if vague:
            out.append(Finding("vague", [r.id],
                               f"not verifiable as written - vague term(s): {', '.join(vague)}"))
        if not r.verification:
            out.append(Finding("no_verification", [r.id], "no verification method defined"))
        if (today - r.last_reviewed).days > STALE_AFTER_DAYS:
            out.append(Finding("stale", [r.id],
                               f"last reviewed {r.last_reviewed.isoformat()} "
                               f"(> {STALE_AFTER_DAYS // 365} years)"))
    return out


_SYSTEM = (
    "You help maintain a requirements library for procuring CE-marked test rigs. "
    "Rewrite the requirement so it is verifiable: replace vague terms with "
    "measurable criteria or a defined acceptance method. Keep its intent; do not "
    "add obligations that were not implied. If a number is needed that you cannot "
    "know, write it as [TO BE DEFINED]. Reply as JSON: "
    '{"text": str, "verification": str, "rationale": str}'
)


def propose_rewrite(req: Requirement, llm: LLM) -> Finding | None:
    """AI draft for a vague requirement. Returns None if the LLM is unavailable."""
    try:
        out = llm.json(_SYSTEM, f"{req.id} {req.title}: {req.text}")
    except LLMError:
        return None
    return Finding("vague", [req.id], f"AI draft: {out.get('rationale', '')}",
                   {"text": out.get("text", req.text),
                    "verification": out.get("verification", req.verification)},
                   layer="ai")
