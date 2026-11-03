"""Fill the templates. Values are inserted literally; nothing else in the wording changes."""

import hashlib
import html
import re
from dataclasses import dataclass, field

from app.messaging import templates
from app.models import MessageKind

_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


@dataclass
class RenderedMessage:
    kind: MessageKind
    whatsapp_text: str | None = None
    email_subject: str | None = None
    email_text: str | None = None
    email_html: str | None = None
    missing: list[str] = field(default_factory=list)

    @property
    def content_hash(self) -> str:
        parts = [self.kind.value, self.whatsapp_text, self.email_subject, self.email_text]
        return hashlib.sha256("\x1f".join(p or "" for p in parts).encode()).hexdigest()


def fill(template: str, values: dict[str, str]) -> str:
    """Replace {placeholders} in one pass, so braces inside values are never re-read."""
    return _PLACEHOLDER.sub(lambda m: values[m.group(1)], template)


def _email_html(text: str) -> str:
    """The email body as simple HTML: same words, paragraphs kept, values escaped."""
    paragraphs = [html.escape(p).replace("\n", "<br>\n") for p in text.split("\n\n")]
    body = "\n".join(f"<p>{p}</p>" for p in paragraphs)
    return (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.5;'
        f'color:#1f2937">\n{body}\n</div>'
    )


def render(kind: MessageKind, values: dict[str, str | None]) -> RenderedMessage:
    required = (
        templates.DOCUMENTS_NEEDED_FIELDS
        if kind == MessageKind.DOCUMENTS_NEEDED
        else templates.NOTHING_NEEDED_FIELDS
    )
    missing = [name for name in required if not (values.get(name) or "").strip()]
    if missing:
        return RenderedMessage(kind, missing=missing)

    filled = {name: str(values[name]).strip() for name in required}
    if kind == MessageKind.DOCUMENTS_NEEDED:
        email_text = fill(templates.EMAIL_DOCUMENTS_NEEDED, filled)
        return RenderedMessage(
            kind,
            whatsapp_text=fill(templates.WHATSAPP_DOCUMENTS_NEEDED, filled),
            email_subject=fill(templates.EMAIL_DOCUMENTS_NEEDED_SUBJECT, filled),
            email_text=email_text,
            email_html=_email_html(email_text),
        )
    text = fill(templates.NOTHING_NEEDED, filled)
    return RenderedMessage(
        kind,
        whatsapp_text=text,
        email_subject=fill(templates.NOTHING_NEEDED_SUBJECT, filled),
        email_text=text,
        email_html=_email_html(text),
    )
