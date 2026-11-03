"""Split a query letter into candidate requirement segments (list items and sentences).

Every line of the letter body ends up either as a candidate or as a skipped line with a
reason, and both are stored, so staff can see exactly what was considered.
"""

import re
from dataclasses import dataclass

from app.processing.query_rules import conditions_in, names_a_requirement

_LIST_MARKER = re.compile(
    r"^\s*(?:\(?(?:\d{1,2}|[a-hA-H]|[ivxIVX]{1,4})[.)]|\d{1,2}(?=\s+[A-Z])|[-•*●▪◦➢►])\s+"
)
# Column headings of a requirements table, e.g. "Sr. Document required Details".
_TABLE_HEADER = re.compile(
    r"^\s*(?:sr\.?|s\.?\s*no\.?|sl\.?\s*no\.?|no\.)\s+[A-Za-z]", re.IGNORECASE
)
_REQUEST_CUE = re.compile(
    r"\b(?:submit|provide|furnish|send|share|attach|enclose|forward|arrange|clarif\w*|"
    r"explain\w*|confirm\w*|specify|justif\w*|requir\w*|need(?:ed)?|kindly|please|pending|"
    r"awaited|not\s+received|missing)\b",
    re.IGNORECASE,
)
_SALUTATION = re.compile(r"^\s*(?:dear\b|respected\b)", re.IGNORECASE)
_CLOSING = re.compile(
    r"^\s*(?:yours\s+(?:sincerely|faithfully|truly)|(?:warm\s+|best\s+|kind\s+)?regards|"
    r"sincerely|thanking\s+you|thank\s+you|for\s+and\s+on\s+behalf|authori[sz]ed\s+signatory)\b",
    re.IGNORECASE,
)
_HEADER_LINE = re.compile(
    r"^\s*(?:subject|sub|re|ref(?:erence)?|date|claim\s+(?:no|number|ref\w*|id)|"
    r"policy\s+(?:no|number)|patient(?:\s+name)?|insured(?:\s+(?:name|person))?|member\s+id|"
    r"hospital(?:\s+name)?|date\s+of\s+(?:admission|discharge)|uhid|ccn|tpa)\b[^:\n]{0,25}:",
    re.IGNORECASE,
)
# Instructions about the documents as a whole, not requirements in themselves.
_GENERIC_INSTRUCTION = re.compile(
    r"\b(?:the\s+(?:above|following|said|requested|below)|above[- ]mentioned|these\s+documents"
    r"|within\s+\d+\s+days|failing\s+which|at\s+the\s+earliest|claim\s+(?:will|shall|may)\s+be"
    r"|this\s+letter|the\s+documents|address\s+above)\b",
    re.IGNORECASE,
)
_ABBREVIATIONS = {"dr", "mr", "mrs", "ms", "no", "nos", "vs", "sr", "jr", "st", "e.g", "i.e", "etc"}


@dataclass(frozen=True)
class Segment:
    index: int
    text: str
    kind: str  # "list_item", "sentence" or "table_header"
    used: bool
    reason: str  # why it was kept or skipped

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "text": self.text,
            "kind": self.kind,
            "used": self.used,
            "reason": self.reason,
        }


def _sentences(paragraph: str) -> list[str]:
    parts, start = [], 0
    for match in re.finditer(r"[.!?;](?=\s+[A-Z(\"'])", paragraph):
        word = re.findall(r"[\w.]+$", paragraph[start : match.start()])
        if word and word[0].lower().rstrip(".") in _ABBREVIATIONS:
            continue
        parts.append(paragraph[start : match.end()])
        start = match.end()
    parts.append(paragraph[start:])
    return [re.sub(r"\s+", " ", p).strip() for p in parts if p.strip()]


def _body_lines(lines: list[str]) -> list[str]:
    start = next((i + 1 for i, line in enumerate(lines) if _SALUTATION.match(line)), 0)
    end = next((i for i in range(start, len(lines)) if _CLOSING.match(lines[i])), len(lines))
    return [line for line in lines[start:end] if not _HEADER_LINE.match(line)]


def segment_letter(text: str) -> list[Segment]:
    blocks: list[tuple[str, str]] = []  # (kind, text)
    current: list[str] = []
    current_kind = ""

    def flush() -> None:
        nonlocal current, current_kind
        if current:
            blocks.append((current_kind, " ".join(current)))
        current, current_kind = [], ""

    for line in _body_lines(text.splitlines()):
        stripped = line.strip()
        marker = _LIST_MARKER.match(line)
        if not stripped:
            flush()
        elif _TABLE_HEADER.match(line) and not marker:
            flush()
            blocks.append(("table_header", stripped))
        elif marker:
            flush()
            current, current_kind = [line[marker.end() :].strip()], "list_item"
        elif (
            current_kind == "list_item"
            and re.search(r"[.!?]$", current[-1])
            and stripped[:1].isupper()
        ):
            # A capitalised line after a finished item starts a new paragraph.
            flush()
            current, current_kind = [stripped], "paragraph"
        else:
            if not current:
                current_kind = "paragraph"
            current.append(stripped)
            # A line ending in ":" introduces what follows; it ends its paragraph.
            if stripped.endswith(":"):
                flush()
    flush()

    segments: list[Segment] = []
    for kind, block in blocks:
        if kind == "paragraph":
            pieces = _sentences(block)
        else:
            pieces = [re.sub(r"\s+", " ", block).strip()]
        for piece in pieces:
            segment_kind = "sentence" if kind == "paragraph" else kind
            if segment_kind == "table_header":
                used, reason = False, "table column headings"
            else:
                used, reason = _decide(piece, segment_kind)
            segments.append(Segment(len(segments), piece, segment_kind, used, reason))
    return segments


_ALL_DOCUMENTS = re.compile(r"\b(?:all|every|each|the)\s+(?:the\s+)?documents?\b", re.I)


def general_conditions(segments: list[Segment]) -> list[dict]:
    """Letter-wide conditions from skipped lines, e.g. "All documents should be attested"."""
    found = []
    for segment in segments:
        if segment.used or not _ALL_DOCUMENTS.search(segment.text):
            continue
        codes = conditions_in(segment.text)
        if codes:
            found.append({"text": segment.text, "conditions": codes})
    return found


def _decide(text: str, kind: str) -> tuple[bool, str]:
    names = names_a_requirement(text)
    if text.rstrip().endswith(":") and not names:
        return False, "introduces a list"
    if _GENERIC_INSTRUCTION.search(text) and not names:
        return False, "general instruction, not a specific requirement"
    if kind == "list_item":
        return True, "list item"
    if names:
        return True, "names a document or asks a question"
    if _REQUEST_CUE.search(text):
        return True, "contains a request"
    return False, "no request"
