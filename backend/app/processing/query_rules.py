"""Read the requirements in one sentence or list item of an insurer's query letter.

Rules recognise named documents, split compound requests into separate points, attach each
condition to the document it qualifies, and recognise requests for clarification. A point is
"confident" when it names a known document or asks a clear question; anything else becomes an
"unrecognised" point that must be reviewed, never dropped.
"""

import re
from dataclasses import dataclass, field

from app.models.query import RequirementType

# Named documents, most specific first (an "itemised pharmacy bill" is not a hospital bill).
_DOCUMENTS: list[tuple[RequirementType, str]] = [
    (
        RequirementType.PHARMACY_BILL,
        r"(?:pharmacy|medicine|chemist|medical\s+store)\s+(?:bills?|invoices?|receipts?)",
    ),
    (RequirementType.DISCHARGE_SUMMARY, r"discharge\s+(?:summary|summaries|card|certificate)"),
    (
        RequirementType.HOSPITAL_BILL,
        r"(?:final\s+)?(?:hospital|inpatient|ip|main|final)\s+(?:bills?|invoices?)"
        r"|(?:detailed|itemi[sz]ed)\s+bills?|bill\s+break[- ]?up"
        r"|(?:intraocular\s+)?(?:lens|iol|implant|stent|prosthesis)\s+invoices?",
    ),
    (
        RequirementType.INVESTIGATION_REPORT,
        r"(?:investigation|lab(?:oratory)?|test|pathology|radiology|imaging|blood\s+test|"
        r"x-?ray|mri|ct(?:\s+scan)?|ultrasound|usg|ecg|histopathology|biopsy|scan)\s+reports?"
        r"|reports?\s+of\s+(?:all\s+)?(?:the\s+)?(?:investigations?|tests?)|test\s+results"
        r"|(?:a-?scan\s+)?biometry(?:\s+reports?)?|a-?scan"
        r"|(?:ns1\s+)?antigen\s+(?:test\s+)?reports?|(?:serial\s+)?platelet\s+counts?"
        r"|blood\s+tests?(?:\s+reports?)?|\bcbc\b(?:\s+reports?)?|complete\s+blood\s+counts?",
    ),
    (
        RequirementType.CONSULTATION_PAPER,
        r"(?:first\s+|initial\s+|previous\s+)?consultation\s+(?:papers?|notes?|records?|slips?)"
        r"|opd\s+(?:papers?|notes?|records?|slips?)|prescriptions?"
        r"|case\s+sheets?|treatment\s+(?:papers?|records?)",
    ),
    (
        RequirementType.POLICY_COPY,
        r"policy\s+(?:copy|document|schedule|certificate)|copy\s+of\s+(?:the\s+)?policy",
    ),
    (
        RequirementType.HEALTH_CARD,
        r"health\s+(?:insurance\s+)?card|e-?cards?|tpa\s+(?:id\s+)?card|insurance\s+card",
    ),
    (
        RequirementType.KYC_DOCUMENT,
        r"kyc(?:\s+documents?)?|(?:photo\s+)?id(?:entity)?\s+proof|address\s+proof"
        r"|aadh?aar(?:\s+card)?|pan\s+card|passport|voter\s+id(?:\s+card)?|driving\s+licen[cs]e",
    ),
    (
        RequirementType.OTHER,
        r"claim\s+form|cancell?ed\s+cheque|bank\s+(?:account\s+)?details|neft\s+(?:details|form)"
        r"|\bfir\b|\bmlc\b|medico[- ]legal\s+certificate|police\s+report"
        r"|implant\s+(?:stickers?|certificates?)|indoor\s+case\s+papers?"
        r"|ot\s+notes|operation\s+notes|anaesthesia\s+(?:notes|records?)|death\s+certificate"
        r"|ambulance\s+(?:bills?|receipts?)|(?:previous|past)\s+(?:medical\s+|treatment\s+)?records?"
        r"|declaration(?:\s+form)?|questionnaire",
    ),
    # Last resort: any other named report ("NS1 report", "histology report") is an investigation.
    (RequirementType.INVESTIGATION_REPORT, r"[a-z0-9][a-z0-9\-]*\s+reports?"),
]
# Words that cannot name a report on their own ("the report", "all reports").
_NOT_A_NAME = {
    "the", "this", "that", "a", "an", "your", "all", "any", "said", "above", "such", "these",
    "those", "following", "other", "medical", "relevant", "required", "supporting", "and", "or",
}
# Generic words that do not make two mentions the same item ("X report" vs "Y report").
_GENERIC_NOUNS = {
    "report", "reports", "bill", "bills", "invoice", "invoices", "paper", "papers", "record",
    "records", "copy", "test", "tests", "result", "results", "document", "documents", "the",
}
# "all laboratory reports, including the NS1 report and platelet counts": the umbrella is
# replaced by the specific items it introduces.
_INTRODUCES_EXAMPLES = re.compile(
    r"\b(?:including|such\s+as|namely|viz\.?|like|especially|i\.e\.?)\b", re.IGNORECASE
)
_DOCUMENT_RES = [
    (kind, re.compile(rf"\b(?:{pattern})\b", re.IGNORECASE)) for kind, pattern in _DOCUMENTS
]

# A document named only as a reference ("as mentioned in the discharge summary") is not requested.
_REFERENCE_BEFORE = re.compile(
    r"(?:as\s+per|according\s+to|mentioned\s+in|stated\s+in|recorded\s+in|given\s+in|"
    r"\bin|\bon|\bfrom|\bper)\s+(?:the\s+|your\s+|this\s+)?$",
    re.IGNORECASE,
)

_CLARIFICATION = re.compile(
    r"\b(?:clarif\w*|explain\w*|explanation|confirm\w*|justif\w*|specify|reason\s+for|"
    r"details?\s+(?:of|regarding|about|on)|history\s+of|duration\s+of|whether)\b",
    re.IGNORECASE,
)

# (code, pattern, wording used in the requirement)
_CONDITIONS: list[tuple[str, str, str]] = [
    (
        "signed_by_doctor",
        r"sign(?:ed|ature)\s+(?:and\s+(?:stamped|sealed)\s+)?(?:of|by)\s+(?:the\s+)?"
        r"(?:treating\s+|attending\s+|concerned\s+)?"
        r"(?:doctor|consultant|physician|surgeon|medical\s+officer)"
        r"|(?:doctor|consultant|physician|surgeon)(?:'s)?\s+sign(?:ature|ed)?",
        "signed by the treating doctor",
    ),
    ("signed", r"\b(?:duly\s+)?sign(?:ed|ature)\b", "signed"),
    ("stamped", r"\b(?:stamp(?:ed)?|seal(?:ed)?)\b", "with hospital stamp or seal"),
    ("original", r"\boriginals?\b", "original copy"),
    ("itemised", r"\bitemi[sz]ed\b|\bitem[- ]?wise\b|\bbreak[- ]?up\b|\bdetailed\b", "itemised"),
    ("attested", r"\b(?:self[- ])?attested\b", "self-attested"),
    (
        "covers_period",
        r"\b(?:pre|post)[- ]?hospitali[sz]ation\b|\bfor\s+the\s+period\b"
        r"|\bserial\b(?!\s+(?:number|no\b))|\bdate[- ]wise\b|\bday[- ]wise\b",
        "covering the whole period",
    ),
    (
        "shows_identifier",
        r"\b(?:lens\s+model|model\s+(?:number|no\b\.?)|batch\s+(?:number|no\b\.?)"
        r"|serial\s+(?:number|no\b\.?)|lot\s+(?:number|no\b\.?)|stickers?)",
        "showing the model, batch or serial number",
    ),
    (
        "current_period",
        r"\bcurrent\s+(?:policy\s+)?(?:year|period)\b"
        r"|\bvalid\s+(?:on|for)\s+the\s+date\s+of\s+admission\b",
        "for the current policy period",
    ),
]
_CONDITION_RES = [(code, re.compile(p, re.IGNORECASE), text) for code, p, text in _CONDITIONS]

# Joining words that make "X and Y duly signed" a list sharing its trailing conditions.
_LIST_JOINER = re.compile(r"^[\s,&/]*(?:and|or|as\s+well\s+as)?[\s,&/]*(?:the|all|also)?\s*$", re.I)

# A new request verb after "and" starts a new clause: "... bill and clarify the delay".
_CLAUSE_SPLIT = re.compile(
    r";|(?<=[a-z0-9)])\.\s+(?=[A-Z])|,?\s+and\s+(?=(?:also\s+)?(?:please\s+|kindly\s+)?"
    r"(?:submit|provide|send|furnish|share|clarify|explain|confirm|specify|mention|justify)\b)",
)

_LEADING_FILLER = re.compile(
    r"^(?:(?:kindly|please|also|further(?:more)?|additionally|we\s+(?:request|require)"
    r"(?:\s+you)?(?:\s+to)?|you\s+are\s+(?:also\s+)?(?:requested|required)\s+to|"
    r"request\s+you\s+to|"
    r"submit|provide|furnish|send|share|forward|attach|enclose|arrange|the|a|an|all)\s+)+",
    re.IGNORECASE,
)


@dataclass
class RulePoint:
    requirement: str
    requirement_type: RequirementType
    conditions: list[dict[str, str]] = field(default_factory=list)
    # False when the rules could not tell what is being asked for.
    confident: bool = True


def _capitalise(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def clean_requirement(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip(" .;:,-")
    return _capitalise(_LEADING_FILLER.sub("", text).strip(" .;:,-"))


def _conditions_in(text: str) -> list[dict[str, str]]:
    found = []
    for code, pattern, wording in _CONDITION_RES:
        if pattern.search(text):
            found.append({"code": code, "text": wording})
    codes = {c["code"] for c in found}
    if "signed_by_doctor" in codes:
        found = [c for c in found if c["code"] != "signed"]
    return found


def _mentions(clause: str) -> list[tuple[RequirementType, re.Match]]:
    """Requested documents in a clause, in order, without overlaps or mere references."""
    taken: list[tuple[int, int]] = []
    found = []
    last = len(_DOCUMENT_RES) - 1
    for index, (kind, pattern) in enumerate(_DOCUMENT_RES):
        for match in pattern.finditer(clause):
            if any(match.start() < end and start < match.end() for start, end in taken):
                continue
            if _REFERENCE_BEFORE.search(clause[: match.start()]):
                continue
            if index == last and match.group(0).split()[0].lower() in _NOT_A_NAME:
                continue
            taken.append((match.start(), match.end()))
            found.append((kind, match))
    return sorted(found, key=lambda item: item[1].start())


def _significant_words(text: str) -> set[str]:
    return {
        word[:5]
        for word in re.findall(r"[a-z0-9]+", text.lower())
        if len(word) > 1 and word not in _GENERIC_NOUNS
    }


def _merge(*groups: list[dict[str, str]]) -> list[dict[str, str]]:
    merged, seen = [], set()
    for group in groups:
        for condition in group:
            if condition["code"] not in seen:
                seen.add(condition["code"])
                merged.append(condition)
    if "signed_by_doctor" in seen:
        merged = [c for c in merged if c["code"] != "signed"]
    return merged


def _points_for_clause(clause: str) -> list[RulePoint]:
    mentions = _mentions(clause)
    if not mentions:
        if _CLARIFICATION.search(clause):
            return [RulePoint(clean_requirement(clause), RequirementType.CLARIFICATION)]
        return []

    # Conditions written between two documents belong to the first ("summary signed by the
    # doctor and the final bill"); a few words before a document belong to it ("itemised
    # pharmacy bill"); conditions after the last document belong to every document when the
    # documents are a plain list ("summary and final bill duly signed").
    own: list[list[dict[str, str]]] = []
    is_plain_list = True
    previous_end = 0
    for index, (_, match) in enumerate(mentions):
        before = clause[max(previous_end, match.start() - 30) : match.start()]
        after_end = mentions[index + 1][1].start() if index + 1 < len(mentions) else match.end()
        between = clause[match.end() : after_end]
        if index + 1 < len(mentions) and not _LIST_JOINER.match(between):
            is_plain_list = False
        own.append(_merge(_conditions_in(before + " " + match.group(0)), _conditions_in(between)))
        previous_end = match.end()
    trailing = _conditions_in(clause[mentions[-1][1].end() :])

    # An umbrella followed by "including ..." items of the same type is replaced by them:
    # "all laboratory reports, including the NS1 report and platelet counts" is two points.
    umbrellas = set()
    for i, (kind, match) in enumerate(mentions):
        for later_kind, later in mentions[i + 1 :]:
            if later_kind == kind and _INTRODUCES_EXAMPLES.search(
                clause[match.end() : later.start()]
            ):
                umbrellas.add(i)
                break

    # Mentions of the same type that share a significant word are one item restated
    # ("Lens invoice: intraocular lens invoice"); otherwise they are separate items.
    groups: list[tuple[RequirementType, set[str], list[str], list[dict[str, str]]]] = []
    for index, (kind, match) in enumerate(mentions):
        if index in umbrellas:
            continue
        name = _capitalise(re.sub(r"\s+", " ", match.group(0)))
        words = _significant_words(name)
        shared = trailing if (is_plain_list or index == len(mentions) - 1) else []
        group = next((g for g in groups if g[0] == kind and g[1] & words), None)
        if group is None:
            group = (kind, set(), [], [])
            groups.append(group)
        group[1].update(words)
        group[2].append(name)
        group[3][:] = _merge(group[3], own[index], shared)

    points = []
    for kind, _, names, conditions in groups:
        requirement = "; ".join(dict.fromkeys(names))
        if conditions:
            requirement += " — " + "; ".join(c["text"] for c in conditions)
        points.append(RulePoint(requirement, kind, conditions))
    return points


def extract_rule_points(segment: str) -> list[RulePoint]:
    """Every requirement in one sentence or list item. Never empty for a real request."""
    points: list[RulePoint] = []
    for clause in _CLAUSE_SPLIT.split(segment):
        if clause and clause.strip():
            points.extend(_points_for_clause(clause))
    if not points:
        points.append(RulePoint(clean_requirement(segment), RequirementType.OTHER, confident=False))
    return points


def conditions_in(text: str) -> list[dict[str, str]]:
    """Condition codes stated in free text (used for letter-wide notes)."""
    return _conditions_in(text)


def names_a_requirement(text: str) -> bool:
    """Whether the text names a requestable document or asks for a clarification."""
    return bool(_mentions(text)) or bool(_CLARIFICATION.search(text))
