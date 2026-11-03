"""Read labelled values ("Admission date 18 October 2026", "Invoice number: X") from text.

Rules, not the LLM: labelled fields are exact, instant and need no verification. A value is
kept only if it has the right shape for its field (a date must look like a date, and so on).
"""

import re
from typing import Any

_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATE_RE = re.compile(
    rf"^(?:\d{{1,2}}(?:st|nd|rd|th)?[\s\-/.]+{_MONTH}[\s\-/.,]+\d{{2,4}}"
    rf"|{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}"
    r"|\d{1,2}[\-/.]\d{1,2}[\-/.]\d{2,4}"
    r"|\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)
_AMOUNT_RE = re.compile(
    r"^(?:₹|rs\.?|inr)?\s*(\d{1,3}(?:,\d{2,3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)\b",
    re.IGNORECASE,
)
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/\-_.]{1,40}")

# (field, label pattern, value shape). More specific labels come first, because the first
# alternative that matches at a position wins.
_LABELS: list[tuple[str, str, str]] = [
    ("patient_id", r"patient\s+id|uhid|mrn|ipd?\s+no\.?|registration\s+no\.?", "id"),
    ("patient_name", r"patient(?:'s)?\s+name|name\s+of\s+(?:the\s+)?patient", "text"),
    ("date_of_birth", r"date\s+of\s+birth|dob", "date"),
    ("admission_date", r"date\s+of\s+admission|admission\s+date|admitted\s+on", "date"),
    ("discharge_date", r"date\s+of\s+discharge|discharge\s+date|discharged\s+on", "date"),
    ("invoice_number", r"(?:invoice|bill|receipt)\s+(?:number|no\.?)", "id"),
    ("invoice_date", r"(?:invoice|bill|receipt)\s+date", "date"),
    ("policy_number", r"policy\s+(?:number|no\.?)", "id"),
    ("claim_number", r"claim\s+(?:number|no\.?|id)", "id"),
    (
        "doctor_name",
        r"treating\s+(?:consultant|doctor)|attending\s+doctor|consultant|prescriber",
        "text",
    ),
    (
        "total_amount",
        r"net\s+(?:amount\s+)?payable|amount\s+payable|grand\s+total|total\s+amount|total",
        "amount",
    ),
    ("hospital_name", r"hospital\s+name|hospital", "text"),
    ("admission_date", r"admission", "date"),
    ("discharge_date", r"discharge", "date"),
    ("patient_name", r"patient", "text"),
    ("document_date", r"date", "date"),
]
_LABEL_RE = re.compile(
    r"(?<![A-Za-z])(?:" + "|".join(f"(?P<l{i}>{p})" for i, (_, p, _) in enumerate(_LABELS)) + r")"
    r"(?![A-Za-z])\s*(?P<colon>[:\-])?",
    re.IGNORECASE,
)

# A line ending in a money amount, with words before it: one billed item.
_LINE_ITEM_RE = re.compile(
    r"^(?=.*[A-Za-z]{3}).+?\s(?:₹|rs\.?|inr)?\s*\d{1,3}(?:,\d{2,3})*\.\d{2}\s*$", re.IGNORECASE
)
_SUMMARY_LINE_RE = re.compile(
    r"^\s*(?:gross|net|sub-?\s?total|total|discount|amount|balance|paid|tax|gst)", re.IGNORECASE
)


def _shape(value: str, kind: str) -> str | None:
    value = value.strip(" :-|\t")
    if kind == "date":
        match = _DATE_RE.match(value)
        return match.group(0).strip() if match else None
    if kind == "amount":
        match = _AMOUNT_RE.match(value)
        return match.group(1) if match else None
    if kind == "id":
        match = _ID_RE.match(value)
        return match.group(0) if match and re.search(r"\d", match.group(0)) else None
    if 2 <= len(value) <= 120 and re.search(r"[A-Za-z]{2}", value):
        return value[:120]
    return None


def _label_index(match: re.Match) -> int:
    """Which entry of _LABELS matched (its group is named l<index>)."""
    groups = match.groupdict()
    return next(int(name[1:]) for name, value in groups.items() if value and name[0] == "l")


def extract_fields(pages: list[tuple[int, str]]) -> dict[str, Any]:
    """Labelled values by field (first occurrence wins), plus a count of billed line items."""
    fields: dict[str, dict[str, Any]] = {}
    line_items = 0
    for page_number, text in pages:
        for line in text.splitlines():
            if _LINE_ITEM_RE.match(line) and not _SUMMARY_LINE_RE.match(line):
                line_items += 1

            accepted = []
            for match in _LABEL_RE.finditer(line):
                at_line_start = not line[: match.start()].strip()
                if at_line_start or match.group("colon"):
                    accepted.append(match)
            # Each value runs from its label to the next accepted label on the line.
            ends = [m.start() for m in accepted[1:]] + [len(line)] if accepted else []
            for match, end in zip(accepted, ends, strict=True):
                field, _, kind = _LABELS[_label_index(match)]
                if field in fields:
                    continue
                value = _shape(line[match.end() : end], kind)
                if value:
                    fields[field] = {"value": value, "page": page_number}
    return {"fields": fields, "line_item_count": line_items}
