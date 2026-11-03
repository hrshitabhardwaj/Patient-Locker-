"""Decide whether a patient's locker already satisfies one query point.

COVERED is given only when a document passes every check, and its evidence is recorded.
NEEDS_FROM_PATIENT is given only when the system is sure nothing in the locker qualifies.
Anything uncertain is NEEDS_REVIEW. A document's category alone never proves a requirement.
"""

import re
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.models import (
    ClassificationMethod,
    DocumentCategory,
    MatchStatus,
    ProcessingStatus,
    RequirementType,
)

MET, NOT_MET, UNKNOWN = "met", "not_met", "unknown"

# A billed line count at or above this is "itemised"; at or below the lower one it is not.
ITEMISED_MIN_LINES = 3
LUMP_SUM_MAX_LINES = 1
# OCR confidence below this means a missing word may just be a misread one.
LOW_OCR_CONFIDENCE = 70.0

_CATEGORY_LABEL = {
    "discharge_summary": "discharge summary",
    "hospital_bill": "hospital bill",
    "investigation_report": "investigation report",
    "consultation_paper": "consultation paper or prescription",
    "pharmacy_bill": "pharmacy bill",
    "policy_copy": "policy copy",
    "health_card": "health card",
    "kyc_document": "KYC document",
    "other": "document",
}

# Words that only restate the document type; they do not narrow down which document is meant.
_GENERIC_WORDS = {
    "report",
    "reports",
    "bill",
    "bills",
    "invoice",
    "invoices",
    "paper",
    "papers",
    "record",
    "records",
    "copy",
    "copies",
    "test",
    "tests",
    "result",
    "results",
    "document",
    "documents",
    "count",
    "counts",
    "all",
    "the",
    "of",
    "and",
    "with",
    "for",
    "final",
    "serial",
    "itemised",
    "itemized",
    "original",
    "any",
    "current",
    "year",
    "pre",
    "post",
    "operative",
    "detailed",
}
_CATEGORY_WORDS: dict[RequirementType, set[str]] = {
    RequirementType.DISCHARGE_SUMMARY: {"discharge", "summary", "summaries", "card", "certificate"},
    RequirementType.HOSPITAL_BILL: {
        "hospital",
        "inpatient",
        "ip",
        "main",
        "break",
        "up",
        "breakup",
    },
    RequirementType.PHARMACY_BILL: {
        "pharmacy",
        "medicine",
        "chemist",
        "medical",
        "store",
        "receipts",
    },
    RequirementType.INVESTIGATION_REPORT: {"investigation", "investigations", "laboratory", "lab"},
    RequirementType.CONSULTATION_PAPER: {
        "consultation",
        "opd",
        "first",
        "initial",
        "previous",
        "prescription",
        "prescriptions",
        "case",
        "sheet",
        "sheets",
        "treatment",
        "notes",
        "note",
        "slips",
        "slip",
    },
    RequirementType.POLICY_COPY: {"policy", "schedule", "certificate"},
    RequirementType.HEALTH_CARD: {"health", "insurance", "card", "ecard", "tpa", "id"},
    RequirementType.KYC_DOCUMENT: {
        "kyc",
        "id",
        "identity",
        "proof",
        "photo",
        "address",
        "card",
        "self",
        "attested",
    },
    RequirementType.OTHER: set(),
}
# Spellings joined before comparing, so "X-ray" and "x ray" are one word.
_JOINED = [
    (r"\bx[\s-]?rays?\b", "xray"),
    (r"\ba[\s-]?scan\b", "ascan"),
    (r"\bns[\s-]?1\b", "ns1"),
    (r"\be[\s-]?card\b", "ecard"),
    (r"\bbreak[\s-]?up\b", "breakup"),
]
_SYNONYMS = {
    "ultrasound": ["usg", "sonograph", "ultrasonograph"],
    "usg": ["ultrasound", "sonograph"],
    "xray": ["radiograph"],
    "ascan": ["biometry"],
    "biometry": ["ascan"],
    "cbc": ["complete blood count", "haemogram", "hemogram"],
    "platelet": ["plt"],
    "intraocular": ["iol"],
    "iol": ["intraocular"],
    "haemoglobin": ["hemoglobin"],
    "left": ["os"],
    "right": ["od"],
}

# Text on the document saying a signature or stamp is not genuine.
_MARK_DISCLAIMER = re.compile(
    r"not\s+authentic|not\s+(?:a\s+)?(?:valid|genuine)|not\s+a\s+(?:clinician\s+|doctor'?s?\s+)?"
    r"signature|illustrative|specimen|training\s+stamp|signature[- ]like|for\s+training",
    re.IGNORECASE,
)
_MONTHS = {
    m: i
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1
    )
}
_NUMERIC_DATE = re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})\b")
_WORDED_DATE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?[\s\-]+([A-Za-z]{3,9})\.?[\s\-,]+(\d{4})\b")
_DATE_TOKEN = (
    r"(\d{1,2}[/\-.]\d{1,2}[/\-.]\d{4}|\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,9}\.?,?\s+\d{4})"
)
_DATE_RANGE = re.compile(
    rf"{_DATE_TOKEN}\s*(?:to|till|until|upto|up\s+to|-|–)\s*{_DATE_TOKEN}", re.I
)
_IDENTIFIER = re.compile(
    r"\b(?:model|batch|lot|serial)\b\s*(?:no\.?|number|#)?\s*[:\-]?\s*[A-Za-z0-9]", re.IGNORECASE
)


# ------------------------------------------------------------------ inputs and outputs


@dataclass
class LockerDocument:
    id: uuid.UUID
    filename: str
    status: ProcessingStatus
    declared_category: DocumentCategory | None
    detected_category: DocumentCategory | None
    classification_method: ClassificationMethod | None
    pages: list[tuple[int, str, float | None]]  # (page number, text, OCR confidence)
    fields: dict[str, Any] = field(default_factory=dict)
    line_item_count: int = 0
    # Verified AI profile facts, or None if there is no successful profile.
    profile: dict[str, Any] | None = None

    @property
    def text(self) -> str:
        return "\n".join(text for _, text, _ in self.pages)

    @property
    def categories(self) -> set[str]:
        """Categories this document can be relied on to be. Staff's choice wins."""
        if self.declared_category:
            return {self.declared_category.value}
        if self.detected_category and self.classification_method != ClassificationMethod.CONFLICT:
            return {self.detected_category.value}
        return set()

    @property
    def low_ocr(self) -> bool:
        return any(c is not None and c < LOW_OCR_CONFIDENCE for _, _, c in self.pages)


@dataclass
class PointToMatch:
    requirement: str
    requirement_type: RequirementType
    conditions: list[dict[str, str]]
    source_text: str
    needs_review: bool = False
    review_reason: str | None = None


@dataclass
class LetterContext:
    patient_name: str
    admission: date | None = None
    discharge: date | None = None


@dataclass
class Decision:
    status: MatchStatus
    explanation: str
    evidence: list[dict[str, Any]] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)


# ------------------------------------------------------------------ text helpers


def normalise(text: str) -> str:
    text = text.lower()
    for pattern, replacement in _JOINED:
        text = re.sub(pattern, replacement, text)
    return re.sub(r"\s+", " ", text)


def _stem(term: str) -> str:
    return term if len(term) <= 5 else term[: len(term) - 2]


def _has_term(term: str, text: str) -> bool:
    for variant in [term, *_SYNONYMS.get(term, [])]:
        if re.search(rf"\b{re.escape(_stem(variant))}", text):
            return True
    return False


def subject_terms(point: PointToMatch) -> list[str]:
    """Words that say which document is meant ("ultrasound", "abdomen", "left"), not its type."""
    name = point.requirement.split(" — ")[0]
    skip = _GENERIC_WORDS | _CATEGORY_WORDS[point.requirement_type]
    terms = [w for w in re.findall(r"[a-z0-9]+", normalise(name)) if len(w) > 1 and w not in skip]

    source = normalise(point.source_text)
    # "report of the abdomen": the organ narrows it down.
    for first_name in name.split(";")[:1]:
        found = source.find(normalise(first_name).strip())
        if found >= 0:
            after = source[found + len(normalise(first_name).strip()) :][:40]
            organ = re.match(r"\s+of\s+(?:the\s+)?([a-z]{4,})", after)
            if organ and organ.group(1) not in skip:
                terms.append(organ.group(1))
    # Laterality must match: a right-eye report does not answer a left-eye request.
    side = re.search(r"\b(left|right)\b", source)
    if side and point.requirement_type == RequirementType.INVESTIGATION_REPORT:
        terms.append(side.group(1))
    return list(dict.fromkeys(terms))


def _missing_terms(document: LockerDocument, terms: list[str]) -> list[str]:
    text = normalise(document.text)
    return [term for term in terms if not _has_term(term, text)]


def _missing_any(document: LockerDocument, words: list[str]) -> bool:
    """True if none of the words appear in the document."""
    return len(_missing_terms(document, words)) == len(words)


def _name_words(point: PointToMatch) -> list[str]:
    name = point.requirement.split(" — ")[0]
    return [w for w in re.findall(r"[a-z]{4,}", normalise(name)) if w not in _GENERIC_WORDS]


def parse_date(text: str) -> date | None:
    numeric = _NUMERIC_DATE.search(text)
    if numeric:
        day, month, year = map(int, numeric.groups())
        try:
            return date(year, month, day)  # Indian documents write day first
        except ValueError:
            return None
    worded = _WORDED_DATE.search(text)
    if worded:
        month = _MONTHS.get(worded.group(2)[:3].lower())
        if month:
            try:
                return date(int(worded.group(3)), month, int(worded.group(1)))
            except ValueError:
                return None
    return None


def _all_dates(text: str) -> set[date]:
    found = set()
    for match in _NUMERIC_DATE.finditer(text):
        parsed = parse_date(match.group(0))
        if parsed:
            found.add(parsed)
    for match in _WORDED_DATE.finditer(text):
        parsed = parse_date(match.group(0))
        if parsed:
            found.add(parsed)
    return found


def _person_tokens(name: str) -> set[str]:
    titles = {"mr", "mrs", "ms", "miss", "dr", "shri", "smt", "master", "kumari", "sri"}
    return {t for t in re.findall(r"[a-z]+", name.lower()) if t not in titles}


def belongs_to_someone_else(document: LockerDocument, patient_name: str) -> bool:
    on_document = (document.fields.get("patient_name") or {}).get("value")
    if not on_document:
        return False
    a, b = _person_tokens(on_document), _person_tokens(patient_name)
    return bool(a and b) and not (a <= b or b <= a)


def _quote(document: LockerDocument, terms: list[str], category: str) -> dict[str, Any]:
    """The line that shows what the document is: a subject word, else its title."""
    label_words = category.replace("_", " ").split()
    for page, text, _ in document.pages:
        for line in text.splitlines():
            normalised = normalise(line)
            if terms and all(_has_term(t, normalised) for t in terms[:1]):
                return _evidence(document, page, line)
            if not terms and all(w[:5] in normalised for w in label_words):
                return _evidence(document, page, line)
    page, text, _ = document.pages[0] if document.pages else (1, "", None)
    first = next((line for line in text.splitlines() if line.strip()), "")
    return _evidence(document, page, first)


def _evidence(document: LockerDocument, page: int, line: str) -> dict[str, Any]:
    quote = line.strip()[:200]
    page_text = next((t for p, t, _ in document.pages if p == page), "")
    return {
        "document_id": str(document.id),
        "filename": document.filename,
        "page": page,
        "quote": quote,
        "verified": bool(quote) and quote in page_text,
    }


# ------------------------------------------------------------------ condition checks


def _check(code: str, result: str, detail: str, document: LockerDocument | None = None) -> dict:
    return {
        "code": code,
        "result": result,
        "detail": detail,
        "document_id": str(document.id) if document else None,
    }


def _mark_check(code: str, kind: str, document: LockerDocument) -> dict:
    """Signature ("signatures") or stamp ("stamps") from the vision profile."""
    noun = "signature" if kind == "signatures" else "stamp or seal"
    profile = document.profile
    if profile is None:
        return _check(
            code, UNKNOWN, f"No AI check of the pages is available to see a {noun}.", document
        )
    marks = profile.get(kind) or []
    unchecked = profile.get("pages_unchecked_for_marks") or []
    checked = profile.get("pages_checked_for_marks") or []
    if marks:
        pages = ", ".join(str(m["page"]) for m in marks)
        if _MARK_DISCLAIMER.search(document.text):
            return _check(
                code,
                UNKNOWN,
                f"A {noun} is visible (page {pages}), but the document says it is "
                "illustrative or not genuine.",
                document,
            )
        return _check(code, MET, f"{noun.capitalize()} seen on page {pages}.", document)
    if unchecked or len(checked) < len(document.pages):
        return _check(code, UNKNOWN, f"Not every page could be checked for a {noun}.", document)
    return _check(code, NOT_MET, f"No {noun} found on any page.", document)


def _document_check(
    condition: dict[str, str], document: LockerDocument, context: LetterContext
) -> dict:
    code = condition["code"]
    if code in ("signed", "signed_by_doctor"):
        return _mark_check(code, "signatures", document)
    if code == "stamped":
        return _mark_check(code, "stamps", document)
    if code == "itemised":
        lines = document.line_item_count
        if lines >= ITEMISED_MIN_LINES:
            return _check(code, MET, f"{lines} billed line items listed.", document)
        if lines <= LUMP_SUM_MAX_LINES:
            return _check(
                code, NOT_MET, "The bill shows a single total, not an itemised break-up.", document
            )
        return _check(code, UNKNOWN, "Only two billed lines found; may not be itemised.", document)
    if code == "shows_identifier":
        if _IDENTIFIER.search(document.text):
            return _check(code, MET, "A model, batch or serial number is printed.", document)
        return _check(
            code, UNKNOWN, "No model, batch or serial number found in the text.", document
        )
    if code == "current_period":
        if context.admission is None:
            return _check(
                code, UNKNOWN, "Admission date unknown; cannot check the policy period.", document
            )
        ranges = []
        for start, end in _DATE_RANGE.findall(document.text):
            begin, finish = parse_date(start), parse_date(end)
            if begin and finish:
                ranges.append((begin, finish))
        if any(begin <= context.admission <= finish for begin, finish in ranges):
            return _check(code, MET, "The policy period includes the admission date.", document)
        if ranges:
            return _check(
                code,
                NOT_MET,
                "The policy period shown does not include the admission date.",
                document,
            )
        return _check(code, UNKNOWN, "No policy period found in the document.", document)
    if code == "original":
        return _check(
            code,
            UNKNOWN,
            "The insurer asks for the original; the locker holds a scanned copy.",
            document,
        )
    if code == "attested":
        return _check(code, UNKNOWN, "Attestation cannot be confirmed from a scan.", document)
    return _check(
        code, UNKNOWN, f"Condition '{condition['text']}' cannot be checked automatically.", document
    )


def _period_check(candidates: list[LockerDocument], context: LetterContext) -> dict:
    dates: set[date] = set()
    for document in candidates:
        dates |= _all_dates(document.text)
    if context.admission:
        last = context.discharge or context.admission
        window = (context.admission - timedelta(days=1), last + timedelta(days=1))
        dates = {d for d in dates if window[0] <= d <= window[1]}
    if len(dates) >= 2:
        return _check(
            "covers_period", MET, f"Results on {len(dates)} different dates during the stay."
        )
    return _check(
        "covers_period",
        UNKNOWN,
        f"Only {len(candidates)} matching report(s), on {len(dates) or 'no'} date(s) within the "
        "stay; cannot confirm values for the whole period.",
    )


# ------------------------------------------------------------------ the decision


def decide(
    point: PointToMatch, documents: list[LockerDocument], context: LetterContext
) -> Decision:
    kind = point.requirement_type
    label = _CATEGORY_LABEL.get(kind.value, kind.value.replace("_", " "))

    if point.needs_review:
        return Decision(
            MatchStatus.NEEDS_REVIEW,
            "This requirement could not be read with confidence from the letter "
            f"({point.review_reason}); staff should confirm what is being asked.",
        )
    if kind == RequirementType.CLARIFICATION:
        return Decision(
            MatchStatus.NEEDS_FROM_PATIENT,
            "The insurer asks for a written explanation, which only the patient can give.",
        )

    terms = subject_terms(point)
    usable = [d for d in documents if d.status == ProcessingStatus.PROCESSED]
    unreadable = [
        d for d in documents if d.status in (ProcessingStatus.UNREADABLE, ProcessingStatus.FAILED)
    ]
    unclassified = [d for d in usable if not d.categories]

    if kind == RequirementType.OTHER:
        if not terms:
            return Decision(
                MatchStatus.NEEDS_REVIEW,
                "The requested document is not a standard category and has no distinctive "
                "name to search for.",
            )
        typed: list[LockerDocument] = []
        candidates = [d for d in usable if not _missing_terms(d, terms)]
    else:
        typed = [d for d in usable if kind.value in d.categories]
        candidates = [d for d in typed if not _missing_terms(d, terms)] if terms else typed

    foreign = [d for d in candidates if belongs_to_someone_else(d, context.patient_name)]
    candidates = [d for d in candidates if d not in foreign]
    searched = f" mentioning “{' '.join(terms)}”" if terms else ""
    article = "An" if label[0] in "aeiou" else "A"

    if not candidates:
        doubts = []
        if foreign:
            doubts.append(f"'{foreign[0].filename}' looks right but names a different patient")
        # An unreadable file could be anything; an unclassified one only if it looks related.
        looks_related = terms or _name_words(point)
        maybe = unreadable + [d for d in unclassified if not _missing_any(d, looks_related)]
        if maybe:
            doubts.append(f"'{maybe[0].filename}' could not be read or classified and may be it")
        misread = [d for d in typed if d.low_ocr]
        if terms and misread:
            doubts.append(
                f"'{misread[0].filename}' is a poor-quality scan; the words may be misread"
            )
        if doubts:
            return Decision(
                MatchStatus.NEEDS_REVIEW,
                f"No {label}{searched} was confirmed in the locker, but " + "; ".join(doubts) + ".",
            )
        if typed and terms:
            names = ", ".join(f"'{d.filename}'" for d in typed[:3])
            return Decision(
                MatchStatus.NEEDS_FROM_PATIENT,
                f"The locker has {label}(s) ({names}), but none{searched}.",
            )
        return Decision(MatchStatus.NEEDS_FROM_PATIENT, f"No {label}{searched} is in the locker.")

    conditions = point.conditions
    per_document = {
        d.id: [_document_check(c, d, context) for c in conditions if c["code"] != "covers_period"]
        for d in candidates
    }
    period = (
        _period_check(candidates, context)
        if any(c["code"] == "covers_period" for c in conditions)
        else None
    )
    satisfying = [
        d
        for d in candidates
        if all(check["result"] == MET for check in per_document[d.id])
        and (period is None or period["result"] == MET)
    ]
    all_checks = [check for d in candidates for check in per_document[d.id]]
    if period:
        all_checks.append(period)

    if satisfying:
        wants_marks = any(
            c["code"] in ("signed", "signed_by_doctor", "stamped") for c in conditions
        )
        evidence = [_quote(d, terms, kind.value) for d in satisfying]
        for document in satisfying if wants_marks else []:
            for mark_kind in ("signatures", "stamps"):
                for mark in (document.profile or {}).get(mark_kind, []):
                    evidence.append(
                        {
                            "document_id": str(document.id),
                            "filename": document.filename,
                            "page": mark["page"],
                            "kind": mark_kind[:-1],
                            "source": "vision",
                        }
                    )
        details = [c["detail"] for d in satisfying for c in per_document[d.id]]
        if period:
            details.append(period["detail"])
        names = ", ".join(f"'{d.filename}'" for d in satisfying)
        explanation = f"Covered by {names} ({label}{searched})."
        if details:
            explanation += " " + " ".join(dict.fromkeys(details))
        return Decision(MatchStatus.COVERED, explanation, evidence, all_checks)

    unknown = [c for c in all_checks if c["result"] == UNKNOWN]
    if unknown:
        return Decision(
            MatchStatus.NEEDS_REVIEW,
            f"{article} {label}{searched} is in the locker, but it cannot be confirmed that it "
            "meets the requirement: " + " ".join(dict.fromkeys(c["detail"] for c in unknown)),
            [_quote(d, terms, kind.value) for d in candidates],
            all_checks,
        )
    failed = [c for c in all_checks if c["result"] == NOT_MET]
    names = ", ".join(f"'{d.filename}'" for d in candidates)
    return Decision(
        MatchStatus.NEEDS_FROM_PATIENT,
        f"The {label} in the locker ({names}) does not meet the requirement: "
        + " ".join(dict.fromkeys(c["detail"] for c in failed)),
        [_quote(d, terms, kind.value) for d in candidates],
        all_checks,
    )


def letter_outcome(statuses: list[MatchStatus], letter_needs_review: bool) -> str:
    from app.models import LetterOutcome

    if letter_needs_review or MatchStatus.NEEDS_REVIEW in statuses:
        return LetterOutcome.HOLD_FOR_STAFF_REVIEW
    if MatchStatus.NEEDS_FROM_PATIENT in statuses:
        return LetterOutcome.SEND_MISSING_ITEMS_MESSAGE
    return LetterOutcome.NO_ADDITIONAL_DOCUMENTS_REQUIRED
