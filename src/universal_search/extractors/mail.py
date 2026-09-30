"""Mail extraction with the standard library only (phase 033).

Mail is the first source where the interesting text is not the whole file: the
part a user searches for is usually the subject line or the sender, and the body
is frequently HTML with the content buried in markup. So this extractor
composes text deliberately — participants, date, subject, then body — instead
of dumping whatever the parser happened to produce.

Three decisions worth stating, because each of them is a place where a mail
extractor could quietly over-reach:

* **Attachments are not read.** Their bytes are never materialized and their
  content never enters the index. Their names are counted and reported as a
  warning, so "this message had 3 attachments and none of them are searchable"
  is visible rather than implied.
* **Headers are untrusted input.** They are sanitized and length-bounded with
  the same helpers that bound document structure, because a subject line can
  contain anything a sender typed.
* **One unreadable part costs a warning, not the message.** A body declaring a
  charset that does not exist is a real thing that happens; the headers and the
  remaining parts are still indexed and the result is PARTIAL.

``.msg`` is deliberately out of scope: it is a Microsoft OLE compound file, not
an RFC 5322 message, and reading it would require a proprietary parser. It is
not registered, so it is treated as an unknown binary rather than opened.
"""

from __future__ import annotations

import time
from email import policy
from email.message import Message
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path

from universal_search.domain.extraction import (
    DEFAULT_LIMITS,
    DocumentStructure,
    ExtractionLimits,
    ExtractionResult,
    ExtractionStatus,
    ResourceUsage,
)
from universal_search.extractors.base import (
    CancelCheck,
    CharBudget,
    check_cancel,
    exceeds_input_limit,
    finalize,
    input_size,
    sanitize_text_entry,
    structure_entries,
    time_limit_warning,
    timed_out,
)
from universal_search.extractors.text import normalize_text

MAIL_EXTENSIONS: tuple[str, ...] = (".eml", ".mbox", ".mbx", ".email")

# Mailbox containers hold many messages, so the message count needs its own
# bound. ``max_pages`` is the natural existing budget for "units of work".
DEFAULT_MAX_MESSAGES = 2_000

# Header values are indexed, and indexed text is stored forever, so they are
# held to the structure bounds rather than a mail-specific larger one.
MAX_HEADER_CHARS = 200

# Header lines are emitted in a fixed order so two runs over the same mailbox
# produce byte-identical text.
PARTICIPANT_HEADERS = ("From", "To", "Cc", "Reply-To", "Date")

# Block-level HTML tags force a line break in the extracted text, otherwise
# "celda1celda2" becomes one word the user can never search for.
_HTML_BREAK = frozenset(
    {
        "p", "br", "div", "li", "tr", "table", "td", "th", "thead", "tbody",
        "tfoot", "caption", "blockquote", "pre", "dd", "dt", "figure",
        "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "hr",
    }
)
_HTML_SKIP = frozenset({"script", "style", "head", "title"})


class _HtmlText(HTMLParser):
    """Collect the readable text of an HTML body, dropping script and style."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _HTML_SKIP:
            self._skip_depth += 1
        elif tag in _HTML_BREAK:
            self.chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _HTML_SKIP and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in _HTML_BREAK:
            self.chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data.strip():
            self.chunks.append(data)

    def text(self) -> str:
        return "".join(self.chunks)


def html_to_text(data: str) -> str:
    """Readable text of an HTML fragment, or ``""`` if it cannot be parsed."""
    parser = _HtmlText()
    try:
        parser.feed(data)
        parser.close()
    except Exception:
        # Malformed markup still yields whatever was read before the error.
        pass
    return parser.text()


def split_mbox(raw: bytes) -> list[bytes]:
    """Split an mbox into individual messages.

    mbox delimits messages with a line starting ``From ``, which is why the
    format escapes occurrences of that inside a body as ``>From ``. Not
    unescaping would split forwarded messages into phantom ones, so it is
    unescaped here — the same trade-off every mbox reader makes.
    """
    messages: list[bytes] = []
    current: list[bytes] = []
    for line in raw.splitlines(keepends=True):
        if line.startswith(b"From ") and current:
            messages.append(b"".join(current))
            current = []
        if line.startswith(b">From "):
            line = b"From " + line[6:]
        current.append(line)
    if current:
        messages.append(b"".join(current))
    return [message for message in messages if message.strip()]


def _envelope_sender(raw: bytes) -> str | None:
    """The sender address from an mbox envelope line, when present.

    The envelope line is ``From <address> <date>``, so the address is the
    *first* field after the keyword — reading the second one returns the day
    of the week, which is what this function did the first time it ran.
    """
    first, _, _rest = raw.partition(b"\n")
    if not first.startswith(b"From "):
        return None
    fields = first[5:].split()
    return fields[0].decode("ascii", "replace") if fields else None


def _strip_envelope_line(raw: bytes) -> bytes:
    """Drop the mbox envelope line, which is not an RFC 5322 header."""
    first, separator, rest = raw.partition(b"\n")
    if first.startswith(b"From ") and separator:
        return rest
    return raw


def _header(message: Message, name: str) -> str | None:
    try:
        value = message[name]
    except Exception:
        return None
    if value is None:
        return None
    cleaned = sanitize_text_entry(str(value))
    return cleaned or None


def _body_text(
    message: Message, budget: CharBudget
) -> tuple[list[str], int, int]:
    """Collect body text, skipping attachments.

    Returns the warnings raised, the attachment count and the count of parts
    that could not be read.
    """
    warnings: list[str] = []
    attachments = 0
    unreadable = 0
    try:
        parts = list(message.walk())
    except Exception:
        parts = [message]
    for part in parts:
        if part.get_content_maintype() == "multipart":
            continue
        if budget.exhausted:
            break
        try:
            disposition = part.get_content_disposition()
        except Exception:
            disposition = None
        if disposition == "attachment":
            attachments += 1
            continue
        content_type = part.get_content_type()
        if content_type not in ("text/plain", "text/html"):
            if part.get_content_maintype() != "text":
                attachments += 1
            continue
        try:
            content = part.get_content()
        except Exception as exc:
            # A declared charset that does not exist, or a broken transfer
            # encoding: this part is lost, the message is not.
            unreadable += 1
            warnings.append(
                f"part {content_type} unreadable ({type(exc).__name__})"
            )
            continue
        if not isinstance(content, str):
            unreadable += 1
            continue
        if content_type == "text/html":
            content = html_to_text(content)
        if content.strip():
            budget.add(content)
    if attachments:
        warnings.append(f"{attachments} attachment(s) not extracted")
    if unreadable:
        warnings.append(f"{unreadable} part(s) unreadable")
    return warnings, attachments, unreadable


def _compose(
    message: Message, budget: CharBudget, fallback_from: str | None
) -> tuple[list[str], int]:
    """Add the header block and the body of one message to the budget.

    Returns the warnings raised and how many body parts could not be read, so
    the caller can report the message as PARTIAL instead of quietly shipping
    a subject line and calling it whole.
    """
    warnings: list[str] = []
    values: dict[str, str] = {}
    for name in PARTICIPANT_HEADERS:
        value = _header(message, name)
        if value:
            values[name] = value[:MAX_HEADER_CHARS]
    if "From" not in values and fallback_from:
        values["From"] = fallback_from
    if values:
        block = "\n".join(f"{name}: {value}" for name, value in values.items())
        budget.add(block + "\n\n")
    body_warnings, _attachments, unreadable = _body_text(message, budget)
    warnings.extend(body_warnings)
    return warnings, unreadable


def read_mail(
    path: Path,
    *,
    limits: ExtractionLimits | None = None,
    cancel: CancelCheck | None = None,
) -> ExtractionResult:
    limits = limits or DEFAULT_LIMITS
    started = time.perf_counter()
    size = input_size(path) or 0
    over_limit = exceeds_input_limit(path, limits)
    if over_limit is not None:
        return finalize(
            started=started,
            input_bytes=size,
            text=None,
            status=ExtractionStatus.ERROR,
            error=over_limit,
        )

    warnings: list[str] = []
    truncated = False
    mailbox = path.suffix.lower() in (".mbox", ".mbx")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return finalize(
            started=started,
            input_bytes=size,
            text=None,
            status=ExtractionStatus.ERROR,
            error=f"{type(exc).__name__}: {exc}",
        )

    if mailbox:
        blocks = split_mbox(raw)
        max_messages = min(limits.max_pages, DEFAULT_MAX_MESSAGES)
        if len(blocks) > max_messages:
            # Dropping messages silently would be the worst outcome here: the
            # index would claim the mailbox is searchable and half of it is
            # simply not there.
            warnings.append(f"stopped at {max_messages} of {len(blocks)} messages")
            blocks = blocks[:max_messages]
            truncated = True
    else:
        blocks = [raw]

    parser = BytesParser(policy=policy.default)
    budget = CharBudget(limits.max_chars)
    subjects: list[str] = []
    parsed = 0
    failed = 0
    unreadable_total = 0
    cancelled = False
    for index, block in enumerate(blocks):
        if check_cancel(cancel):
            warnings.append("extraction cancelled")
            cancelled = True
            break
        if timed_out(started, limits):
            warnings.append(time_limit_warning(limits, "message", index + 1))
            truncated = True
            break
        try:
            message = parser.parsebytes(
                _strip_envelope_line(block) if mailbox else block
            )
        except Exception:
            failed += 1
            continue
        parsed += 1
        subject = _header(message, "Subject")
        if subject:
            subjects.append(subject)
        budget.add(f"Subject: {subject}\n\n" if subject else "")
        message_warnings, unreadable = _compose(
            message,
            budget,
            _envelope_sender(block) if mailbox else None,
        )
        warnings.extend(message_warnings)
        unreadable_total += unreadable
        if budget.cut:
            truncated = True
            break

    text = normalize_text(budget.text())
    if not text or not text.strip():
        text = None
    structure = None
    if subjects:
        structure = DocumentStructure(
            title=subjects[0] if len(subjects) == 1 else None,
            headings=structure_entries(subjects),
        )
    if cancelled:
        status = ExtractionStatus.CANCELLED
        text = None
    elif truncated:
        status = ExtractionStatus.TRUNCATED
    elif not text:
        status = ExtractionStatus.NO_CONTENT if not failed else ExtractionStatus.PARTIAL
        warnings.append("no extractable text")
    elif failed or unreadable_total:
        # A message that lost a body part is not the same document as one that
        # never had it, and saying "ok" would hide the difference.
        status = ExtractionStatus.PARTIAL
    else:
        status = ExtractionStatus.OK
    usage = ResourceUsage(
        input_bytes=size,
        output_chars=len(text or ""),
        pages=parsed,
        elapsed_ms=(time.perf_counter() - started) * 1_000.0,
    )
    return finalize(
        started=started,
        input_bytes=size,
        text=text,
        status=status,
        warnings=warnings,
        truncated=truncated or cancelled,
        structure=structure,
        usage=usage,
    )


__all__ = [
    "DEFAULT_MAX_MESSAGES",
    "MAIL_EXTENSIONS",
    "html_to_text",
    "read_mail",
    "split_mbox",
]
