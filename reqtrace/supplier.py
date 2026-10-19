"""Supplier response analysis.

Layered like the rest of the system:
  1. limit check  - requirements with a structured `limit` are checked
                    numerically against values stated in the answer
  2. phrase rules - deterministic markers (except / on request / TBD / comply)
  3. AI layer     - only where rules are not confident; its evidence quote
                    must exist verbatim in the supplier text or it is discarded
Disagreements and anything not clearly compliant go to a human.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

from .llm import LLM, LLMError, quote_in
from .model import Assessment, Compliance, Requirement, SupplierAnswer

REVIEW_THRESHOLD = 0.75

# Ordered: the first group that matches wins (non-compliance beats partial, ...).
_MARKERS: list[tuple[Compliance, list[str]]] = [
    (Compliance.NON_COMPLIANT, [
        r"\bnot (included|possible|provided|supported|offered|part of)\b", r"\bexcluded\b",
        r"\bcannot\b", r"\bno\b(?! problem)", r"\bdoes not comply\b", r"\bout of scope\b",
        r"\bby (the )?(buyer|customer)\b"]),
    (Compliance.PARTIAL, [
        r"\bexcept\b", r"\bpartly\b", r"\bpartially\b", r"\bon request\b",
        r"\bat extra cost\b", r"\bas (an )?option\b", r"\boptional\b", r"\bonly\b",
        r"\bbut\b", r"\binstead\b"]),
    (Compliance.UNCLEAR, [
        r"\bto be (discussed|clarified|confirmed|agreed)\b", r"\btbd\b", r"\btbc\b",
        r"\bwhere possible\b", r"\bif (required|needed)\b", r"\bgood engineering practice\b",
        r"\bas far as (possible|practicable)\b", r"\bstandard solution\b", r"\bsimilar to\b"]),
    (Compliance.COMPLIANT, [
        r"\bcompl(y|ies|iant)\b", r"\byes\b", r"\bincluded\b", r"\baccording to\b",
        r"\bin accordance with\b", r"\bwill be (delivered|provided|supplied)\b",
        r"\bprovided\b", r"\bfulfil+(ed|s)?\b", r"\bmeets?\b"]),
]

_BARE = re.compile(r"^\s*(yes|ok|comply|complies|compliant|accepted)\W*$", re.I)

_UNIT_PATTERNS = {
    "dB(A)": r"(\d+(?:[.,]\d+)?)\s*db\s*\(?a\)?",
    "months": r"(\d+(?:[.,]\d+)?)\s*months?",
    "years": r"(\d+(?:[.,]\d+)?)\s*(?:years?|yrs?)",
    "days": r"(\d+(?:[.,]\d+)?)\s*(?:days?)",
}


def _limit_check(req: Requirement, answer: str) -> Assessment | None:
    lim = req.limit
    if not lim or lim.unit not in _UNIT_PATTERNS:
        return None
    m = re.search(_UNIT_PATTERNS[lim.unit], answer, re.I)
    if not m:
        return None
    val = float(m.group(1).replace(",", "."))
    ok = val <= lim.value if lim.op == "<=" else val >= lim.value
    return Assessment(
        req_id=req.id, status=Compliance.COMPLIANT if ok else Compliance.NON_COMPLIANT,
        confidence=0.95, layer="rule", evidence=m.group(0),
        reason=f"stated {val:g} {lim.unit}; required {lim.op} {lim.value:g} {lim.unit}")


def assess_rules(req: Requirement, ans: SupplierAnswer) -> Assessment:
    text = ans.answer.strip()
    if not text:
        return Assessment(req_id=req.id, status=Compliance.UNCLEAR, confidence=0.9,
                          layer="rule", reason="no answer given")
    lim = _limit_check(req, text)
    if lim:
        return lim
    for status, pats in _MARKERS:
        for p in pats:
            m = re.search(p, text, re.I)
            if m:
                conf = 0.8
                reason = f"marker '{m.group(0)}'"
                if status == Compliance.COMPLIANT and _BARE.match(text):
                    conf, reason = 0.5, "bare confirmation without supporting detail"
                return Assessment(req_id=req.id, status=status, confidence=conf,
                                  layer="rule", evidence=m.group(0), reason=reason)
    return Assessment(req_id=req.id, status=Compliance.UNCLEAR, confidence=0.3,
                      layer="rule", reason="no recognised marker")


_SYSTEM = (
    "You assess supplier answers to procurement requirements for CE-marked "
    "test rigs. Classify whether the answer shows the requirement is met: "
    "compliant, partial (met with exceptions, options, extra cost, or only in "
    "part), non_compliant, or unclear (vague, deferred, no commitment). Quote "
    "the exact words from the answer that decide it. Be strict: a commitment "
    "without substance is unclear. Reply as JSON: "
    '{"status": str, "quote": str, "reason": str, "confidence": float}'
)


def assess_ai(req: Requirement, ans: SupplierAnswer, llm: LLM) -> Assessment:
    out = llm.json(_SYSTEM, f"Requirement {req.id} ({req.title}): {req.text}\n\n"
                            f"Supplier ticked: {ans.claimed or '-'}\n"
                            f"Supplier answer: {ans.answer}")
    flags = []
    try:
        status = Compliance(out.get("status", "unclear"))
    except ValueError:
        status, flags = Compliance.UNCLEAR, [f"invalid AI status '{out.get('status')}'"]
    quote = str(out.get("quote", ""))
    conf = float(out.get("confidence", 0.5) or 0.5)
    if not quote_in(quote, ans.answer):
        flags.append("AI evidence not found verbatim in supplier answer")
        conf = min(conf, 0.4)
    return Assessment(req_id=req.id, status=status, confidence=round(conf, 2), layer="ai",
                      evidence=quote, reason=str(out.get("reason", "")), flags=flags)


def _claimed_mismatch(ans: SupplierAnswer, a: Assessment) -> str | None:
    c = ans.claimed.strip().lower()
    if c in ("comply", "yes", "compliant") and a.status != Compliance.COMPLIANT:
        return f"supplier ticked '{ans.claimed}' but text reads as {a.status.value}"
    return None


def assess(req: Requirement, ans: SupplierAnswer, mode: str = "hybrid",
           llm: LLM | None = None) -> Assessment:
    """mode: 'rules' | 'ai' | 'hybrid'."""
    rule = assess_rules(req, ans)
    result = rule
    if mode in ("ai", "hybrid") and (mode == "ai" or rule.confidence < REVIEW_THRESHOLD):
        llm = llm or LLM()
        try:
            ai = assess_ai(req, ans, llm)
            if mode == "ai":
                result = ai
            elif ai.flags:                      # guardrail tripped: keep rule, flag it
                result = rule.model_copy(update={"flags": rule.flags + ai.flags})
            elif ai.status == rule.status:
                result = ai.model_copy(update={"layer": "rule+ai",
                                               "confidence": max(ai.confidence, rule.confidence)})
            else:
                result = ai.model_copy(update={
                    "layer": "rule+ai",
                    "flags": ai.flags + [f"rules said {rule.status.value}, AI said {ai.status.value}"]})
        except LLMError as e:
            result = rule.model_copy(update={"flags": rule.flags + [f"AI unavailable: {e}"]})

    mm = _claimed_mismatch(ans, result) if result.confidence >= REVIEW_THRESHOLD else None
    if mm:
        result.flags.append(mm)
    result.needs_review = (result.status != Compliance.COMPLIANT
                           or result.confidence < REVIEW_THRESHOLD or bool(result.flags))
    return result


def load_answers(path: str | Path) -> list[SupplierAnswer]:
    with open(path, encoding="utf-8", newline="") as f:
        return [SupplierAnswer(req_id=r["req_id"], answer=r.get("answer", ""),
                               claimed=r.get("claimed", "")) for r in csv.DictReader(f)]
