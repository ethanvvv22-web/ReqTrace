"""Selecting requirements for a procurement case.

Two steps, kept separate so each is reviewable:
  1. case text -> features   (keywords = baseline; LLM = finds implicit ones)
  2. features  -> requirements (deterministic: the `applies_if` field)

Every selected requirement carries the features that triggered it, and every
feature carries its evidence (matched keyword, or an LLM quote verified to
exist verbatim in the case text). An engineer can therefore challenge any
selection by following the chain back to the source sentence.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .features import FEATURES, describe
from .llm import LLM, LLMError, quote_in
from .model import Requirement


@dataclass
class FeatureHit:
    feature: str
    source: str        # "keyword" | "ai"
    evidence: str      # matched keyword or verbatim quote
    reason: str = ""


@dataclass
class Selected:
    req: Requirement
    because: list[str] = field(default_factory=list)   # triggering features; [] = always


def features_by_keyword(text: str) -> list[FeatureHit]:
    t = text.lower()
    hits = []
    for name, spec in FEATURES.items():
        for kw in spec["kw"]:
            if kw in t:
                hits.append(FeatureHit(name, "keyword", kw))
                break
    return hits


_SYSTEM = (
    "You support procurement engineers who buy CE-marked test rigs. Given a "
    "procurement case description, decide which machine features are present, "
    "including ones that are only implied (e.g. 'operators mount the part' "
    "implies operator access). Only use the feature names provided. For each "
    "feature you return, quote the exact phrase from the description that "
    "supports it. Do not invent features without textual support. Reply as JSON: "
    '{"features": [{"feature": str, "quote": str, "reason": str}]}'
)


def features_by_llm(text: str, llm: LLM) -> tuple[list[FeatureHit], list[str]]:
    """Returns (accepted hits, rejected notes). Hits failing the guardrails are dropped."""
    out = llm.json(_SYSTEM, f"Features:\n{describe()}\n\nCase description:\n{text}")
    hits, rejected = [], []
    for item in out.get("features", []):
        name, quote = item.get("feature", ""), item.get("quote", "")
        if name not in FEATURES:
            rejected.append(f"unknown feature '{name}'")
        elif not quote_in(quote, text):
            rejected.append(f"{name}: quote not found in case text ('{quote[:60]}')")
        else:
            hits.append(FeatureHit(name, "ai", quote, item.get("reason", "")))
    return hits, rejected


def extract_features(text: str, mode: str = "hybrid", llm: LLM | None = None):
    """mode: 'rules' | 'ai' | 'hybrid' (union; AI additions are marked for review).

    Returns (hits, notes). Falls back to rules if the LLM is unavailable.
    """
    notes: list[str] = []
    kw = features_by_keyword(text) if mode in ("rules", "hybrid") else []
    ai: list[FeatureHit] = []
    if mode in ("ai", "hybrid"):
        llm = llm or LLM()
        try:
            ai, rejected = features_by_llm(text, llm)
            notes += [f"AI suggestion rejected: {r}" for r in rejected]
        except LLMError as e:
            notes.append(f"AI layer unavailable ({e}); using keyword rules only")
            if mode == "ai":
                kw = features_by_keyword(text)
    seen = {h.feature for h in kw}
    return kw + [h for h in ai if h.feature not in seen], notes


def select_requirements(features: set[str], library: list[Requirement]) -> list[Selected]:
    out = []
    for r in library:
        if not r.applies_if:
            out.append(Selected(r, []))
        else:
            trig = [f for f in r.applies_if if f in features]
            if trig:
                out.append(Selected(r, trig))
    return out
