"""Turn NEEDS_FROM_PATIENT points into the {missing_items} lines:

    1. Itemised final hospital bill: the insurer needs the breakup of room, operation theatre
       and consumable charges.

Usage rule 4 forbids medical details, because message previews can expose them. Rather than
trying to list every medical word, item wording may only use an allowlist of administrative
words. Anything else ("ultrasound", "antigen", "diabetes") makes the item fall back to a
generic description that points the patient to the insurer's own letter.
"""

import re
from dataclasses import dataclass

from app.models import RequirementType

ALLOWED_WORDS = set(
    """
    a an the and or of for from to by with in on at as is are be it its this that these those
    your you any all other each which whether when what date dates started start first
    symptoms symptom hold holding covers cover covering written writing confirmation
    confirm details detail document documents item items copy copies named letter dated
    bill bills invoice invoices receipt receipts hospital final itemised itemized itemwise
    breakup break up detailed room operation theatre consumable consumables charges charge
    nursing wise date-wise discharge summary summaries card certificate pharmacy medicine
    medicines chemist prescriptions prescription report reports test tests investigation
    investigations laboratory lab result results consultation consultations papers paper
    records record notes case indoor opd treatment previous past policy schedule current
    year period health insurance insurer hospitalisation hospitalization stay whole kyc id
    identity proof address pan aadhaar passport voter driving licence license photo self
    attested cancelled cheque bank account neft form claim declaration signed signature
    stamp stamped seal sealed doctor treating attending original model batch serial number
    numbers sticker stickers admission explanation needs need must show showing shown s
    should please note also
    """.split()
)

_GENERIC_LABEL = {
    RequirementType.DISCHARGE_SUMMARY: "Discharge summary",
    RequirementType.HOSPITAL_BILL: "Hospital bill",
    RequirementType.PHARMACY_BILL: "Pharmacy bills",
    RequirementType.INVESTIGATION_REPORT: "Test report",
    RequirementType.CONSULTATION_PAPER: "Consultation papers or prescriptions",
    RequirementType.POLICY_COPY: "Policy copy",
    RequirementType.HEALTH_CARD: "Health card",
    RequirementType.KYC_DOCUMENT: "Identity document",
    RequirementType.OTHER: "Document",
    RequirementType.CLARIFICATION: "Written explanation",
}

# Reasons from other conditions, in this order of preference. Signature and stamp are
# combined separately ("signed by the treating doctor and stamped by the hospital").
_CONDITION_REASON = {
    "itemised": "the insurer needs a detailed breakup of the charges.",
    "attested": "the insurer needs a self-attested copy.",
    "covers_period": "the insurer needs records covering your whole hospital stay.",
    "shows_identifier": "the insurer needs the model or batch number shown.",
    "current_period": "the insurer needs the policy for the current year.",
    "original": "the insurer needs the original document.",
}
DEFAULT_REASON = "the insurer needs this to review your claim."
WRITTEN_REASON = "the insurer needs this in writing to review your claim."

# A qualifier after the document name: "... bill showing the breakup of room charges".
_QUALIFIER = re.compile(r"\b(?:showing|with|including|indicating|giving|mentioning)\s+(.+)$", re.I)
_CLARIFY_VERB = re.compile(
    r"^(?:please\s+|kindly\s+|also\s+)*(?:clarify|confirm|explain|specify|justify|mention|state|"
    r"provide|submit|share|send)\s+(?:in\s+writing\s+)?(?:to\s+us\s+)?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class MissingItem:
    requirement: str
    requirement_type: RequirementType
    conditions: list[dict[str, str]]
    source_text: str


def is_safe(text: str) -> bool:
    """True if every word is on the administrative allowlist (numbers and dates allowed)."""
    words = re.findall(r"[a-z]+(?:-[a-z]+)?", text.lower())
    return all(word in ALLOWED_WORDS for word in words)


def _capitalise(text: str) -> str:
    return text[:1].upper() + text[1:]


def _sentence(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip().rstrip(".;:,")
    return text + "."


def _label(item: MissingItem, letter_reference: str) -> str:
    codes = {c["code"] for c in item.conditions}
    if item.requirement_type == RequirementType.CLARIFICATION:
        asked = _CLARIFY_VERB.sub("", item.requirement.strip()).rstrip(".")
        asked = re.sub(r"^the\s+", "", asked, flags=re.I)
        if asked.lower().startswith(("whether", "if ")):
            asked = "Written confirmation of " + asked
        label = _capitalise(asked)
    else:
        name = item.requirement.split(" — ")[0].split(";")[0].strip()
        if "itemised" in codes and "itemi" not in name.lower():
            name = "Itemised " + name[:1].lower() + name[1:]
        if "original" in codes and "original" not in name.lower():
            name = "Original " + name[:1].lower() + name[1:]
        label = name
    if is_safe(label):
        return label
    return f"{_GENERIC_LABEL[item.requirement_type]} requested in {letter_reference}"


def _reason(item: MissingItem) -> str:
    if item.requirement_type == RequirementType.CLARIFICATION:
        return WRITTEN_REASON if "in writing" in item.source_text.lower() else DEFAULT_REASON

    qualifier = _QUALIFIER.search(item.source_text)
    if qualifier:
        wanted = qualifier.group(1).strip().rstrip(".")
        candidate = _sentence(f"the insurer needs {wanted}")
        if is_safe(candidate) and len(candidate) <= 160:
            return candidate
    codes = {c["code"] for c in item.conditions}
    marks = []
    if "signed_by_doctor" in codes:
        marks.append("signed by the treating doctor")
    elif "signed" in codes:
        marks.append("signed")
    if "stamped" in codes:
        marks.append("stamped by the hospital")
    if marks:
        return f"the insurer needs it {' and '.join(marks)}."
    for code, reason in _CONDITION_REASON.items():
        if code in codes:
            return reason
    return DEFAULT_REASON


def letter_reference(letter_date: str | None) -> str:
    return f"the insurer's letter dated {letter_date}" if letter_date else "the insurer's letter"


def format_missing_items(
    items: list[MissingItem], *, letter_date: str | None, notes: list[str] | None = None
) -> str:
    """The numbered {missing_items} block. Every line passes the medical-detail allowlist."""
    reference = letter_reference(letter_date)
    lines = [f"{n}. {_label(item, reference)}: {_reason(item)}" for n, item in enumerate(items, 1)]
    for note in notes or []:
        sentence = _sentence(re.sub(r"^\s*(?:note|nb|n\.b\.)\s*[:\-]\s*", "", note, flags=re.I))
        if is_safe(sentence):
            lines.append(f"Please note: {sentence[:1].lower() + sentence[1:]}")
    return "\n".join(lines)
