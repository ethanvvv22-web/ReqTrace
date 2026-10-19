"""Structured information model.

Requirements are not free-text paragraphs but typed objects with applicability
conditions, verification method, lifecycle status and version. Everything the
AI layer does is anchored to these fields, which is what keeps results
traceable and reviewable.
"""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Status(str, Enum):
    ACTIVE = "active"
    UNDER_REVIEW = "under_review"
    RETIRED = "retired"


class SourceType(str, Enum):
    REGULATION = "regulation"
    STANDARD = "standard"
    INTERNAL = "internal"


class Limit(BaseModel):
    """A machine-checkable limit, e.g. noise at operator position <= 80 dB(A)."""
    param: str
    op: str                     # "<=" or ">="
    value: float
    unit: str


class Requirement(BaseModel):
    id: str
    title: str
    text: str
    source_type: SourceType
    source_ref: str
    applies_if: list[str] = Field(default_factory=list)  # any-of features; empty = always
    verification: str = ""      # inspection | test | document review | ...
    limit: Optional[Limit] = None
    version: int = 1
    status: Status = Status.ACTIVE
    supersedes: Optional[str] = None
    rationale: str = ""
    last_reviewed: date


class ProcurementCase(BaseModel):
    id: str
    title: str
    description: str
    # Ground-truth features, labeled by a person (see eval/README).
    features: list[str] = Field(default_factory=list)
    labeled_by: str = ""


class Compliance(str, Enum):
    COMPLIANT = "compliant"
    PARTIAL = "partial"
    NON_COMPLIANT = "non_compliant"
    UNCLEAR = "unclear"


class SupplierAnswer(BaseModel):
    req_id: str
    answer: str
    claimed: str = ""           # what the supplier ticked, e.g. "Comply"


class Assessment(BaseModel):
    req_id: str
    status: Compliance
    confidence: float
    layer: str                  # rule | ai | rule+ai
    evidence: str = ""          # verbatim quote from the supplier answer
    reason: str = ""
    flags: list[str] = Field(default_factory=list)
    needs_review: bool = True


class Decision(BaseModel):
    """A human decision on any AI/rule output - the audit trail."""
    ts: datetime = Field(default_factory=datetime.now)
    actor: str
    action: str                 # accept | reject | override | new_version | ...
    obj: str                    # e.g. "req:R021", "case:C1:R030", "resp:C1_A:R014"
    detail: dict = Field(default_factory=dict)
