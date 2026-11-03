"""Rule-based document classification from titles and characteristic phrases.

Deterministic and instant, so every document gets a category even when the LLM is offline.
A phrase in the title area counts far more than the same phrase in the body: a hospital bill
may mention "discharge summary" in passing, but its title says "bill".
"""

import re
from dataclasses import dataclass

from app.models import DocumentCategory

_RULES: dict[DocumentCategory, tuple[str, ...]] = {
    DocumentCategory.DISCHARGE_SUMMARY: (
        r"discharge\s+summary",
        r"discharge\s+card",
        r"summary\s+of\s+discharge",
        r"discharge\s+certificate",
    ),
    DocumentCategory.HOSPITAL_BILL: (
        r"(?:final|hospital|inpatient|ip|interim|provisional)\s+bill",
        r"hospital\s+invoice",
        r"room\s+(?:and|&)\s+nursing",
        r"operation\s+theatre",
        r"room\s+(?:charges|rent)",
    ),
    DocumentCategory.PHARMACY_BILL: (
        r"pharmacy\s+(?:bill|invoice|receipt)",
        r"chemist",
        r"medical\s+store",
        r"drug\s+licen[cs]e",
        r"\bd\.?\s?l\.?\s+no\b",
    ),
    DocumentCategory.INVESTIGATION_REPORT: (
        r"investigation\s+(?:note|report)",
        r"lab(?:oratory)?\s+report",
        r"test\s+report",
        r"radiology",
        r"\bx-?ray\b",
        r"\bmri\b",
        r"\bct\s+scan\b",
        r"\bultrasound\b|\busg\b",
        r"pathology",
        r"ha?ematology",
        r"biochemistry",
        r"\becg\b|electrocardiogram",
    ),
    DocumentCategory.CONSULTATION_PAPER: (
        r"consultation\s+(?:note|paper|record|sheet)",
        r"\bopd\b",
        r"out-?patient",
        r"\bprescription\b",
        r"case\s+sheet",
        r"chief\s+complaints?",
    ),
    DocumentCategory.POLICY_COPY: (
        r"policy\s+schedule",
        r"certificate\s+of\s+insurance",
        r"policy\s+wording",
        r"sum\s+insured",
        r"policy\s+period",
    ),
    DocumentCategory.HEALTH_CARD: (
        r"health\s+card",
        r"\be-?card\b",
        r"tpa\s+card",
        r"member\s+id",
    ),
    DocumentCategory.KYC_DOCUMENT: (
        r"aadh?aar",
        r"permanent\s+account\s+number",
        r"\bpan\s+card\b",
        r"\bpassport\b",
        r"voter\s+id|election\s+commission",
        r"driving\s+licen[cs]e",
        r"income\s+tax\s+department",
    ),
}
_COMPILED = {
    category: [re.compile(p, re.IGNORECASE) for p in patterns]
    for category, patterns in _RULES.items()
}

TITLE_LINES = 8
TITLE_WEIGHT = 5


@dataclass(frozen=True)
class RuleClassification:
    category: DocumentCategory | None
    confidence: float
    matched: tuple[str, ...]


def _title_categories(first_page_text: str) -> set[DocumentCategory]:
    """Categories named by the title: the first line near the top that names any category."""
    lines = [line for line in first_page_text.splitlines() if line.strip()][:TITLE_LINES]
    for line in lines:
        hits = {
            category
            for category, patterns in _COMPILED.items()
            if any(pattern.search(line) for pattern in patterns)
        }
        if hits:
            return hits
    return set()


def classify_text(first_page_text: str, full_text: str) -> RuleClassification:
    titled = _title_categories(first_page_text)

    scores: dict[DocumentCategory, int] = {}
    matched: dict[DocumentCategory, list[str]] = {}
    for category, patterns in _COMPILED.items():
        for pattern in patterns:
            found = pattern.search(full_text)
            if found:
                scores[category] = scores.get(category, 0) + 1
                matched.setdefault(category, []).append(found.group(0))
        if category in titled:
            scores[category] = scores.get(category, 0) + TITLE_WEIGHT

    if not scores:
        return RuleClassification(None, 0.0, ())

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    top, top_score = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0

    if titled == {top}:
        confidence = 0.9
    elif top_score >= 2 * max(runner_up, 1):
        confidence = 0.7
    elif top_score > runner_up:
        confidence = 0.5
    else:
        confidence = 0.3
    return RuleClassification(top, confidence, tuple(matched[top]))
