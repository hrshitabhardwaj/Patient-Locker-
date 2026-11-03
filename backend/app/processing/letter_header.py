"""Facts about a query letter itself: who sent it and the deadline it sets."""

import re

_SENDER_WORDS = re.compile(
    r"\b(?:insurance|assurance|tpa|third\s+party\s+administrator|health\s*care|general)\b", re.I
)
_COMPANY_WORDS = re.compile(
    r"\b(?:ltd|limited|pvt|private|co|company|corporation|services)\b", re.I
)
_DEADLINE = re.compile(r"\bwithin\s+(\d{1,3})\s+(?:working\s+)?days?\b", re.I)
# Short all-capital words that stay capitals: TPA, LLP, ...
_ACRONYM = re.compile(r"^[A-Z]{2,3}$")


def _readable(name: str) -> str:
    """'KESTRELLINE HEALTH INSURANCE LTD.' -> 'Kestrelline Health Insurance Ltd.'"""
    if name != name.upper():
        return name
    words = []
    for word in name.split():
        words.append(word if _ACRONYM.match(word) else word.capitalize())
    return " ".join(words)


def sender_name(first_page_text: str) -> str | None:
    """The insurer or TPA named on the letterhead (first lines), or None."""
    lines = [line.strip() for line in first_page_text.splitlines() if line.strip()][:8]
    for line in lines:
        if "|" in line or "@" in line or len(line) > 90:
            continue
        if _SENDER_WORDS.search(line) and _COMPANY_WORDS.search(line):
            name = re.sub(r"\s*\((?:fictional|demo|sample)[^)]*\)\s*", " ", line, flags=re.I)
            return _readable(name.strip())[:200]
    return None


def response_days(text: str) -> int | None:
    """Days the letter gives to reply ("within 15 days"), or None if it sets no deadline."""
    found = _DEADLINE.search(text)
    return int(found.group(1)) if found else None
