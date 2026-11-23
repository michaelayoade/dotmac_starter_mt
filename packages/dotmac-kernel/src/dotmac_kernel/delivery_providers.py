"""The provider seam, and the one send path that asks consent (ADR-0006 § 5c).

**Owner of:** *this message is ready — may it go, over what, and what happened?*

The kernel ships a `Protocol` and no client. SMTP, Twilio, Africa's Talking and
Meta Cloud API are product dependencies, exactly as ADR-0009 ships a
`SecretSource` seam and no secret-store client. A product that sends by SMTP
brings its own SMTP.

## Why `send` is here rather than in each caller

Because the consent check must not be optional. If every sender wired its own
provider call, "ask consent first" would be a convention, and Sub's own history
is what a convention gets you: marketing eligibility lived inside the campaign
segment filter, so the answer depended on who was asking. Routing every send
through one function makes the check structural.

`send` does exactly five things, in order:

1. **Replay a completed dispatch.** A stable `dispatch_id` lets an outbox retry
   return the committed receipt without calling the provider again.
2. **Ask consent.** A suppressed address returns a `Suppressed` outcome and the
   provider is never called — no network, no cost, no receipt.
3. **Call the provider.** The adapter passes `message.idempotency_key` to a
   provider that supports idempotency, covering an unknown external outcome.
4. **Record the receipt**, which is what closes the bounce→consent loop
   (`dotmac_kernel.delivery.record_receipt`).
5. **Return the outcome**, so the caller can retry or give up on its own terms.

## What it deliberately does NOT do

- **It does not queue or retry.** `dotmac_kernel.messaging` owns that, and Sub's
  notification queue turned out to be that same engine built twice. A caller that
  wants durability enqueues an outbox event whose handler calls this.
- **It does not render.** `dotmac-template-studio` owns what the message says.
- **It does not choose a channel.** `dotmac_kernel.channel_policy` does.

## K-8 resolver contract (attachments)

`AttachmentRef.ref` is an OPAQUE KEY, not bytes, a path or a URL. The resolver
registry that turns refs into bytes at delivery time (a later work package) must:

- look the ref up by tenant + `kind` inside the tenant-RLS session, never open a
  path or fetch a URL taken from the ref;
- fail closed on an unknown `kind`;
- resolve refs that are immutable or versioned, because the fingerprint covers
  refs, not bytes: a ref whose content changes under a stable dispatch would
  send something the fingerprint never saw;
- bound the resolved size and check the bytes against the declared
  `content_type` before attaching;
- treat a construction `ValueError` and a capability `DeliveryError` as
  terminal, not retried;
- never rebuild a message from a redacted snapshot.

Four owners, one call site each. That separation is the § 5c capability map, and
this module is where the map is honoured rather than described.
"""

from __future__ import annotations

import ipaddress
import re
import unicodedata
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_kernel import consent, delivery
from dotmac_kernel.delivery_models import (
    DELIVERY_ACCEPTED,
    DELIVERY_FAILED,
    DELIVERY_STATUSES,
    CommunicationDelivery,
)
from dotmac_kernel.idempotency import fingerprint_of

#: Headers a caller may set. Everything else is refused, not dropped.
#:
#: - `Reply-To`, `In-Reply-To`, `References`: conversational routing that a
#:   rendered message legitimately needs (support replies, threading).
#: - `List-Unsubscribe`, `List-Unsubscribe-Post`: RFC 2369 / RFC 8058 one-click
#:   unsubscribe, which mailbox providers require for bulk mail.
#:
#: Deliberately absent: `From`/`Sender`/`To`/`Cc`/`Bcc` (addressing and consent
#: are the kernel's, not the caller's), `Subject` (a field), `Message-ID` (the
#: provider derives it from `dispatch_id`), `Date`, `MIME-Version`, `Content-*`
#: (the provider builds MIME) and any `X-` header (an open-ended escape hatch).
ALLOWED_HEADERS: tuple[str, ...] = (
    "In-Reply-To",
    "List-Unsubscribe",
    "List-Unsubscribe-Post",
    "References",
    "Reply-To",
)
_ALLOWED_HEADERS_BY_LOWER = {name.lower(): name for name in ALLOWED_HEADERS}

MAX_HEADER_VALUE_LENGTH = 998  # RFC 5322 § 2.1.1 line limit
MAX_HEADERS_TOTAL_LENGTH = 3072
MAX_ATTACHMENTS = 10
MAX_ATTACHMENT_KIND_LENGTH = 64
MAX_ATTACHMENT_REF_LENGTH = 512
MAX_ATTACHMENT_FILENAME_LENGTH = 255
MAX_ATTACHMENT_EXTENSION_LENGTH = 16
#: Per-part cap (UTF-8 bytes) on `text` / `html` / `body`.
MAX_PART_BYTES = 1024 * 1024

#: Attachment content types suitable for transactional mail. Everything else is
#: refused (notably text/html, image/svg+xml and executables), so adding a type
#: is a reviewed change to this constant.
ALLOWED_ATTACHMENT_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/gif",
        "text/plain",
        "text/csv",
        "text/calendar",
    }
)

#: The extensions each allowed content type may carry, so a filename cannot
#: contradict the declared type (`invoice.pdf.exe` as application/pdf).
_EXTENSIONS_BY_CONTENT_TYPE: dict[str, tuple[str, ...]] = {
    "application/pdf": ("pdf",),
    "image/png": ("png",),
    "image/jpeg": ("jpg", "jpeg"),
    "image/gif": ("gif",),
    "text/plain": ("txt",),
    "text/csv": ("csv",),
    "text/calendar": ("ics",),
}

_WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)
_FILENAME_FORBIDDEN = frozenset("\"';*?<>|:/\\")

_ATOM = r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
_LOCAL_RE = re.compile(rf"{_ATOM}(\.{_ATOM})*")
_DOMAIN_RE = re.compile(
    r"[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+"
)
_MSG_ID = rf"<{_ATOM}(\.{_ATOM})*@[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?>"
_MSG_ID_RE = re.compile(_MSG_ID)
_MSG_IDS_RE = re.compile(rf"{_MSG_ID}( +{_MSG_ID})*")
_UNSUB_LIST_RE = re.compile(r"<[^<>\s]+>( *, *<[^<>\s]+>)*")
_UNSUB_ITEM_RE = re.compile(r"<([^<>\s]+)>")

#: RFC 8058: the only value `List-Unsubscribe-Post` may carry.
_ONE_CLICK = "List-Unsubscribe=One-Click"

_KIND_RE = re.compile(r"[a-z][a-z0-9_.-]*")
_MIME_NAME = r"[a-z0-9][a-z0-9!#$&^_.+-]{0,126}"
_CONTENT_TYPE_RE = re.compile(rf"{_MIME_NAME}/{_MIME_NAME}")

#: Features a provider must declare in `message_features` before it is handed a
#: message that uses them. A legacy single-body adapter declares nothing, so it
#: is refused a message it would silently truncate to its text part. Headers are
#: declared by NAME in `message_headers` instead, so an adapter that honours only
#: Reply-To is never handed a List-Unsubscribe it would drop.
FEATURE_HTML = "html"
FEATURE_ATTACHMENTS = "attachments"


def _has_control(value: str) -> bool:
    """Any C0/C1 control, format, surrogate, private-use or line/paragraph
    separator. CR, LF and NUL are the injection characters; the rest are never
    legitimate in a header value, filename or reference."""
    return any(
        unicodedata.category(ch).startswith("C")
        or unicodedata.category(ch) in ("Zl", "Zp")
        for ch in value
    )


def _bad_subject_char(ch: str) -> bool:
    """Control, line/paragraph separator, surrogate or bidi embedding/override/
    isolate. Format characters such as ZWJ, ZWNJ, soft hyphen and RLM, and
    unassigned code points (newer emoji), are legitimate subject text."""
    return unicodedata.category(ch) in ("Cc", "Zl", "Zp", "Cs") or (
        "\u202a" <= ch <= "\u202e" or "\u2066" <= ch <= "\u2069"
    )


def _is_printable_ascii(value: str) -> bool:
    return all(0x20 <= ord(ch) <= 0x7E for ch in value)


def _valid_addr_spec(addr: str) -> bool:
    local, sep, domain = addr.rpartition("@")
    return bool(
        sep
        and len(local) <= 64
        and len(domain) <= 255
        and _LOCAL_RE.fullmatch(local)
        and _DOMAIN_RE.fullmatch(domain)
    )


_PHRASE = (
    rf'(?:{_ATOM}(?: +{_ATOM})*|"(?:[\x20\x21\x23-\x5b\x5d-\x7e]|\\[\x20-\x7e])*")'
)
_MAILBOX_RE = re.compile(rf"(?:(?P<phrase>{_PHRASE}) +)?<(?P<angle>[^<>\s]+)>")


def _check_reply_to(value: str) -> None:
    """One mailbox, validated as the exact string that will be sent.

    Either a bare addr-spec, or `phrase <addr-spec>` where the phrase is plain
    atoms or one quoted string with no `@`. No comments, encoded words, routes,
    groups, lists or trailing text: the whole value must match.
    """
    message = "Reply-To must be one plain mailbox: addr-spec or phrase <addr-spec>"
    if "=?" in value or "(" in value or ")" in value:
        raise ValueError(message)
    if _valid_addr_spec(value):
        return
    match = _MAILBOX_RE.fullmatch(value)
    if (
        match is None
        or "@" in (match.group("phrase") or "")
        or not _valid_addr_spec(match.group("angle"))
    ):
        raise ValueError(message)


def _is_dns_hostname(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return False
    # A numeric last label is never a real TLD (and `127.1` style forms are IPs).
    return bool(_DOMAIN_RE.fullmatch(host)) and not host.rsplit(".", 1)[1].isdigit()


_MAILTO_UNSAFE_ESCAPE_RE = re.compile(r"%(?:[01][0-9a-f]|7f)", re.IGNORECASE)
_MAILTO_SUBJECT_RE = re.compile(r"subject=[A-Za-z0-9%._~!*'+,/:@-]*")


def _check_mailto(rest: str) -> None:
    """`mailto:` item: one valid address, optionally `?subject=` and nothing else.

    Any percent-escaped control (CR, LF, ...) is refused anywhere in the item, and
    `cc`, `bcc`, `to`, `body` and every other query field are refused.
    """
    if _MAILTO_UNSAFE_ESCAPE_RE.search(rest):
        raise ValueError("List-Unsubscribe mailto has an escaped control character")
    addr, _, query = rest.partition("?")
    if "%" in addr or not _valid_addr_spec(addr):
        raise ValueError("List-Unsubscribe mailto needs a valid address")
    if query and not _MAILTO_SUBJECT_RE.fullmatch(query):
        raise ValueError("List-Unsubscribe mailto allows only a subject= field")


def _check_list_unsubscribe(value: str) -> bool:
    """Validate the list; return True when it holds an https item."""
    if not _UNSUB_LIST_RE.fullmatch(value):
        raise ValueError(
            "List-Unsubscribe must be a comma list of <https://...> or <mailto:...>"
        )
    https = False
    for uri in _UNSUB_ITEM_RE.findall(value):
        if _MAILTO_UNSAFE_ESCAPE_RE.search(uri):
            raise ValueError("List-Unsubscribe item has an escaped control character")
        lowered = uri.lower()
        if lowered.startswith("https:"):
            try:
                parts = urlsplit(uri)
                host = parts.hostname
                parts.port  # noqa: B018 - raises ValueError on a bad port
            except ValueError as exc:
                raise ValueError("List-Unsubscribe has an unparseable URL") from exc
            if not host or "@" in parts.netloc:
                raise ValueError("List-Unsubscribe https URL needs a host, no userinfo")
            if not _is_dns_hostname(host):
                raise ValueError(
                    "List-Unsubscribe https host must be a DNS name, not an IP literal"
                )
            https = True
        elif lowered.startswith("mailto:"):
            _check_mailto(uri[len("mailto:") :])
        else:
            raise ValueError("List-Unsubscribe items must be https: or mailto:")
    return https


def _check_header_syntax(canonical: str, value: str) -> None:
    if canonical == "Reply-To":
        _check_reply_to(value)
    elif canonical == "In-Reply-To":
        if not _MSG_ID_RE.fullmatch(value):
            raise ValueError("In-Reply-To must be exactly one <msg-id>")
    elif canonical == "References":
        if not _MSG_IDS_RE.fullmatch(value):
            raise ValueError("References must be space-separated <msg-id>s")
    elif canonical == "List-Unsubscribe":
        _check_list_unsubscribe(value)


def _checked_part(label: str, part: str | None) -> None:
    if part is None:
        return
    if not isinstance(part, str):
        raise ValueError(f"{label} must be a string")
    try:
        size = len(part.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} is not encodable as UTF-8") from exc
    if size > MAX_PART_BYTES:
        raise ValueError(f"{label} exceeds {MAX_PART_BYTES} bytes")


class _FrozenHeaders(Mapping[str, str]):
    """Immutable, hashable, canonically ordered header mapping."""

    __slots__ = ("_items",)

    def __init__(self, items: tuple[tuple[str, str], ...]) -> None:
        self._items = items

    def __getitem__(self, key: str) -> str:
        for name, value in self._items:
            if name == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (name for name, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __hash__(self) -> int:
        return hash(self._items)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Mapping):
            return dict(self._items) == dict(other.items())
        return NotImplemented

    def __repr__(self) -> str:
        return f"headers({dict(self._items)!r})"


def _normalise_headers(raw: Mapping[str, str] | None) -> _FrozenHeaders:
    if raw is None:
        return _FrozenHeaders(())
    if not isinstance(raw, Mapping):
        raise ValueError("headers must be a mapping of name to value")
    found: dict[str, str] = {}
    total = 0
    for name, value in raw.items():
        if not isinstance(name, str) or not isinstance(value, str):
            raise ValueError("header names and values must be strings")
        canonical = _ALLOWED_HEADERS_BY_LOWER.get(name.lower())
        # The name is checked against the allowlist by exact lowercase match, so
        # a name carrying CR/LF/NUL/whitespace/colon can never match.
        if canonical is None or name != name.strip():
            raise ValueError(
                f"header {name[:40]!r} is not allowed — expected one of "
                f"{', '.join(ALLOWED_HEADERS)}"
            )
        if canonical in found:
            raise ValueError(f"header {canonical} is given more than once")
        if not value.strip() or value != value.strip():
            raise ValueError(f"header {canonical} has an empty or padded value")
        if _has_control(value) or not _is_printable_ascii(value):
            raise ValueError(
                f"header {canonical} must be printable ASCII with no control "
                "characters (CR, LF and NUL are header injection)"
            )
        if len(value) > MAX_HEADER_VALUE_LENGTH:
            raise ValueError(
                f"header {canonical} exceeds {MAX_HEADER_VALUE_LENGTH} characters"
            )
        try:
            _check_header_syntax(canonical, value)
        except ValueError as exc:
            raise ValueError(f"header {canonical}: {exc}") from None
        total += len(canonical) + len(value)
        found[canonical] = value
    if total > MAX_HEADERS_TOTAL_LENGTH:
        raise ValueError(f"headers exceed {MAX_HEADERS_TOTAL_LENGTH} characters")
    if "List-Unsubscribe-Post" in found:
        if found["List-Unsubscribe-Post"] != _ONE_CLICK:
            raise ValueError(f"List-Unsubscribe-Post must be {_ONE_CLICK!r}")
        if "List-Unsubscribe" not in found:
            raise ValueError("List-Unsubscribe-Post requires List-Unsubscribe")
        if not _check_list_unsubscribe(found["List-Unsubscribe"]):
            raise ValueError(
                "One-Click List-Unsubscribe-Post requires an https item (RFC 8058)"
            )
    return _FrozenHeaders(tuple(sorted(found.items())))


@dataclass(frozen=True, slots=True)
class AttachmentRef:
    """A REFERENCE to an attachment — never its bytes.

    Bytes are resolved at delivery time, by whatever resolver the product
    registers for `kind`, so a message stays small, a retry re-reads the same
    stored object, and the fingerprint covers identity rather than content.
    `kind` names the resolver (`document`, `storage`, …); `ref` is its key.
    """

    kind: str
    ref: str
    filename: str
    content_type: str

    def __post_init__(self) -> None:
        for label, value in (
            ("kind", self.kind),
            ("ref", self.ref),
            ("filename", self.filename),
            ("content_type", self.content_type),
        ):
            if not isinstance(value, str):
                raise ValueError(f"attachment {label} must be a string")
        if (
            not _KIND_RE.fullmatch(self.kind)
            or len(self.kind) > MAX_ATTACHMENT_KIND_LENGTH
        ):
            raise ValueError("attachment kind must be a lowercase identifier")
        if (
            not self.ref
            or self.ref != self.ref.strip()
            or len(self.ref) > MAX_ATTACHMENT_REF_LENGTH
            or _has_control(self.ref)
        ):
            raise ValueError(
                "attachment ref is empty, padded, too long or has controls"
            )
        name = unicodedata.normalize("NFC", self.filename)
        object.__setattr__(self, "filename", name)
        # Lookalikes (superscript digits, fullwidth letters and slash) are
        # judged on the NFKC form too.
        compat = unicodedata.normalize("NFKC", name)
        stem = compat.split(".", 1)[0].rstrip(" ").upper()
        extension = compat.rsplit(".", 1)[1] if "." in compat.strip(".") else ""
        if (
            not name
            or name != name.strip()
            or name in (".", "..")
            or len(name) > MAX_ATTACHMENT_FILENAME_LENGTH
            or name.endswith((".", " "))
            or any(ch in _FILENAME_FORBIDDEN for ch in name + compat)
            or stem in _WINDOWS_RESERVED
            or len(extension) > MAX_ATTACHMENT_EXTENSION_LENGTH
            or _has_control(name)
        ):
            raise ValueError(
                "attachment filename must be a bare, portable name: no path "
                "separators, quotes, ; * ? < > | :, control characters, padding, "
                "trailing dots, reserved Windows names or long extensions"
            )
        # Normalised lowercase, no parameters: a `;charset=` or a boundary
        # smuggled in here would be provider-side MIME injection.
        if (
            self.content_type != self.content_type.lower()
            or not _CONTENT_TYPE_RE.fullmatch(self.content_type)
            or self.content_type not in ALLOWED_ATTACHMENT_CONTENT_TYPES
        ):
            raise ValueError(
                "attachment content_type must be one of "
                f"{', '.join(sorted(ALLOWED_ATTACHMENT_CONTENT_TYPES))}"
            )
        allowed = _EXTENSIONS_BY_CONTENT_TYPE[self.content_type]
        if extension.lower() not in allowed:
            raise ValueError(
                f"attachment filename extension must be one of "
                f".{', .'.join(allowed)} for {self.content_type}"
            )


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    """What a provider is asked to send. Already rendered, already addressed.

    **Parts.** `text` and `html` are the two bodies; at least one is required
    (an empty string counts as given, which keeps the legacy `body=""` valid).
    `body` is the legacy name for the text part: `body="x"` means `text="x"`,
    and construction normalises both to the same value so an adapter that reads
    only `message.body` keeps working. Giving `body` and `text` with different
    values is refused. For an html-only message `body` and `text` are `None`.
    `body` has no default (it sits before the required `category`), so a caller
    using only `text`/`html` passes `body=None`.

    **Headers** are limited to `ALLOWED_HEADERS`; anything else is refused.
    **Attachments** are `AttachmentRef`s. `html`, `headers` are email-only.
    """

    #: Product/outbox identity created before the first provider call and reused
    #: on every relay attempt. Provider adapters use `idempotency_key` where the
    #: external API supports idempotent requests.
    dispatch_id: UUID
    channel: str
    address: str
    body: str | None
    #: Why this is being sent — `billing`, `marketing`, … The consent decision
    #: turns on it, so it is required rather than defaulted: a caller that has to
    #: name the category cannot accidentally get the marketing rule applied to an
    #: invoice, or the reverse.
    category: str
    subject: str | None = None
    text: str | None = None
    html: str | None = None
    headers: Mapping[str, str] | None = None
    attachments: Sequence[AttachmentRef] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.dispatch_id, UUID):
            raise ValueError("dispatch_id must be a UUID")
        if not consent.normalize_channel(self.channel):
            raise ValueError("channel is required")
        if not consent.normalize_address(self.channel, self.address):
            raise ValueError("address is required")
        if not (self.category or "").strip():
            raise ValueError("category is required")
        if self.subject is not None and (
            not isinstance(self.subject, str)
            or any(_bad_subject_char(ch) for ch in self.subject)
        ):
            raise ValueError(
                "subject must be a string with no control characters, line "
                "separators, lone surrogates or bidi override/embedding controls"
            )
        for label, part in (
            ("body", self.body),
            ("text", self.text),
            ("html", self.html),
        ):
            _checked_part(label, part)
        if self.body is not None and self.text is not None and self.body != self.text:
            raise ValueError(
                "body is the legacy name for text; they were given different values"
            )
        text = self.text if self.text is not None else self.body
        if text is None and self.html is None:
            raise ValueError("at least one of text/body or html is required")
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "body", text)

        channel = consent.normalize_channel(self.channel)
        headers = _normalise_headers(self.headers)
        if isinstance(self.attachments, str | bytes) or not isinstance(
            self.attachments, Sequence
        ):
            raise ValueError("attachments must be a sequence of AttachmentRef")
        attachments = tuple(self.attachments)
        if len(attachments) > MAX_ATTACHMENTS:
            raise ValueError(f"at most {MAX_ATTACHMENTS} attachments")
        if not all(isinstance(a, AttachmentRef) for a in attachments):
            raise ValueError("attachments must be AttachmentRef instances")
        if channel != "email" and (self.html is not None or headers):
            raise ValueError("html and headers are only valid on the email channel")
        object.__setattr__(self, "headers", headers)
        object.__setattr__(self, "attachments", attachments)

    @property
    def idempotency_key(self) -> str:
        """Stable external-provider key for this outbox dispatch."""
        return str(self.dispatch_id)

    @property
    def required_features(self) -> frozenset[str]:
        """Provider features this message needs beyond a single text body."""
        needed = set()
        if self.html is not None:
            needed.add(FEATURE_HTML)
        if self.attachments:
            needed.add(FEATURE_ATTACHMENTS)
        return frozenset(needed)

    @property
    def request_fingerprint(self) -> str:
        """Bind the stable dispatch id to exactly one rendered request.

        Receipts store only the digest, no version, so there is no migration
        and the two shapes are told apart by the payload itself:

        - **v1** — a message with only legacy fields (a text part, no html, no
          headers, no attachments) hashes the exact payload the kernel always
          did, so every existing receipt still matches on replay.
        - **v2** — anything richer hashes a canonical-JSON payload carrying
          `"v": 2`. Parts, headers and attachments are separate JSON keys and
          strings are JSON-quoted, so bytes moved between parts, header values
          or attachment fields cannot collide; `None` and `""` differ; header
          names are canonical-cased and sorted; attachment order is kept. The
          `v` key cannot appear in a v1 payload, so v1 and v2 never collide.
        """
        channel = consent.normalize_channel(self.channel)
        payload: dict[str, object] = {
            "channel": channel,
            "address": consent.normalize_address(channel, self.address),
            "category": self.category.strip().lower(),
            "subject": self.subject,
        }
        if self.html is None and not self.headers and not self.attachments:
            payload["body"] = self.text
        else:
            payload.update(
                {
                    "v": 2,
                    "text": self.text,
                    "html": self.html,
                    "headers": [[k, v] for k, v in (self.headers or {}).items()],
                    "attachments": [
                        [a.kind, a.ref, a.filename, a.content_type]
                        for a in self.attachments
                    ],
                }
            )
        return fingerprint_of(payload)


@dataclass(frozen=True, slots=True)
class ProviderResult:
    """What the provider said, in the kernel's vocabulary.

    The adapter CLASSIFIES: a permanent failure is `bounced`, a transient one is
    `failed`. See `dotmac_kernel.delivery` for why that judgement cannot live in
    the kernel and why getting it wrong stops a customer's invoices.
    """

    status: str = DELIVERY_ACCEPTED
    provider_message_id: str | None = None
    response_code: str | None = None
    response_body: str | None = None

    def __post_init__(self) -> None:
        if self.status not in DELIVERY_STATUSES:
            raise ValueError(
                f"unknown delivery status {self.status!r} — expected one of "
                f"{', '.join(DELIVERY_STATUSES)}"
            )


@runtime_checkable
class DeliveryProvider(Protocol):
    """A transport a product supplies. One method, plus a name for the receipt."""

    @property
    def name(self) -> str:
        """Stable provider id, recorded on every receipt (`ses`, `twilio`, …)."""

    def send(self, message: OutboundMessage) -> ProviderResult: ...

    # Optional attributes, both `frozenset[str]`:
    # - `message_features`: `FEATURE_HTML` / `FEATURE_ATTACHMENTS` the adapter
    #   honours;
    # - `message_headers`: the header names (from `ALLOWED_HEADERS`) it honours.
    # `send` refuses to hand a message needing something the provider did not
    # declare, rather than let it be silently dropped.


@dataclass(frozen=True, slots=True)
class Suppressed:
    """The send did not happen because consent said no."""

    reason: str

    sent = False


@dataclass(frozen=True, slots=True)
class Sent:
    """The provider was called, and this is its receipt."""

    receipt: CommunicationDelivery

    sent = True

    @property
    def status(self) -> str:
        return self.receipt.status


def _receipts_for_dispatch(
    db: Session, tenant_id: UUID, dispatch_id: UUID
) -> list[CommunicationDelivery]:
    """Every receipt for one dispatch, newest first."""
    return list(
        db.execute(
            select(CommunicationDelivery)
            .where(
                CommunicationDelivery.tenant_id == tenant_id,
                CommunicationDelivery.dispatch_id == dispatch_id,
            )
            .order_by(
                CommunicationDelivery.occurred_at.desc(),
                CommunicationDelivery.created_at.desc(),
            )
        ).scalars()
    )


def _declared(provider: DeliveryProvider, attribute: str) -> frozenset[str]:
    """A provider's declared `message_features` / `message_headers`.

    Absent means "nothing declared". Anything that is not a collection of
    strings is a misconfigured adapter and fails closed with `DeliveryError`.
    """
    value: Any = getattr(provider, attribute, frozenset())
    if value is None or isinstance(value, str | bytes):
        raise delivery.DeliveryError(
            f"provider {getattr(provider, 'name', '?')!r}: {attribute} must be a "
            "collection of names"
        )
    try:
        names = frozenset(value)
    except TypeError:
        raise delivery.DeliveryError(
            f"provider {getattr(provider, 'name', '?')!r}: {attribute} must be a "
            "collection of names"
        ) from None
    if not all(isinstance(n, str) for n in names):
        raise delivery.DeliveryError(
            f"provider {getattr(provider, 'name', '?')!r}: {attribute} must hold "
            "strings"
        )
    return names


def send(
    db: Session,
    tenant_id: UUID,
    *,
    provider: DeliveryProvider,
    message: OutboundMessage,
) -> Sent | Suppressed:
    """Send one message, asking consent first and recording what came back.

    Returns `Suppressed` without calling the provider when the address is
    suppressed for this category — no network call, no receipt, nothing to
    retry. Returns `Sent` otherwise, carrying the receipt; a `bounced` or
    `complaint` receipt will already have suppressed the address by the time
    this returns.

    Replay: an existing receipt for the dispatch is returned without calling the
    provider. The one exception is a dispatch whose receipts are ALL `failed`
    (the adapter's transient, retryable verdict): that allows a fresh attempt,
    with consent re-checked and the outcome appended as a new receipt. If ANY
    receipt is not `failed` (`accepted`, `delivered`, `rejected`, `bounced`,
    `complaint`), the newest such receipt replays, so a late asynchronous
    `failed` webhook cannot reopen a dispatch the provider already took.

    Concurrency: delivery is at-least-once, serialised by the relay lease
    (FOR UPDATE SKIP LOCKED), bounded by `stale_lease_seconds`. This function
    takes no row lock of its own.

    Adapter contract: a provider adapter that cannot tell whether the provider
    accepted the message (for example a lost response) must raise, or return
    `failed` only when the provider honours the `dispatch_id` idempotency key
    (`OutboundMessage.idempotency_key`). SMTP and most HTTP APIs do not honour
    it, so returning `failed` for an unknown outcome can double-send.

    Flush-only: the receipt and any suppression it triggers belong to the
    caller's transaction.
    """
    fingerprint = message.request_fingerprint
    receipts = _receipts_for_dispatch(db, tenant_id, message.dispatch_id)
    if receipts:
        if receipts[0].request_fingerprint != fingerprint:
            raise delivery.DeliveryError(
                "dispatch id was already used for a different message"
            )
        # Receipts are newest first. Any non-failed receipt is final for this
        # purpose; only a dispatch that has never got past `failed` may retry.
        final = next((r for r in receipts if r.status != DELIVERY_FAILED), None)
        if final is not None:
            return Sent(receipt=final)

    features = _declared(provider, "message_features")
    missing = message.required_features - features
    header_names = {name.lower() for name in _declared(provider, "message_headers")}
    missing_headers = {
        name for name in (message.headers or {}) if name.lower() not in header_names
    }
    if missing or missing_headers:
        raise delivery.DeliveryError(
            f"provider {provider.name!r} does not declare support for "
            f"{', '.join(sorted(missing | {f'header {h}' for h in missing_headers}))}"
            "; the message was not sent"
        )

    reason = consent.suppression_reason(
        db,
        tenant_id,
        channel=message.channel,
        address=message.address,
        category=message.category,
    )
    if reason is not None:
        return Suppressed(reason=reason)

    result = provider.send(message)
    receipt = delivery.record_receipt(
        db,
        tenant_id,
        channel=message.channel,
        address=message.address,
        provider=provider.name,
        status=result.status,
        dispatch_id=message.dispatch_id,
        request_fingerprint=fingerprint,
        provider_message_id=result.provider_message_id,
        response_code=result.response_code,
        response_body=result.response_body,
    )
    return Sent(receipt=receipt)


#: The capability id of the email send contract a delivery connector serves
#: (ADR-0024 10: the owning domain publishes the payload contract; a connector
#: only claims its digest). `.v1` is part of the identity: a schema change is a
#: new id, never an edit of this one.
EMAIL_SEND_CAPABILITY_ID = "email.send.v1"

#: The command payload of `email.send.v1`: one already-rendered, already
#: addressed message. It mirrors `OutboundMessage` for the email channel, with
#: the dispatch id as a string and attachments by reference only (never bytes).
#: A DELIVERY capability publishes a command schema and no result schema: the
#: effect is the outcome.
#:
#: The digest of this schema depends on the kernel constants it embeds
#: (`MAX_PART_BYTES`, `ALLOWED_HEADERS`, the attachment limits and content
#: types). Changing ANY of them changes the published contract: that is
#: `email.send.v2`, never an edit of v1 (the digest pin in
#: `tests/unit/test_email_send_contract.py` is the guard).
EMAIL_SEND_COMMAND_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["dispatch_id", "address"],
    "description": (
        "At least one of text/html must be a string. text and html are limited "
        "in UTF-8 BYTES (MAX_PART_BYTES), which JSON Schema cannot count: "
        "maxLength bounds characters only, and the producer and the connector "
        "enforce the byte limit. Each header value is limited in CHARACTERS "
        "(MAX_HEADER_VALUE_LENGTH) and may not contain CR or LF."
    ),
    "anyOf": [
        {"required": ["text"], "properties": {"text": {"type": "string"}}},
        {"required": ["html"], "properties": {"html": {"type": "string"}}},
    ],
    "properties": {
        "dispatch_id": {
            "type": "string",
            "pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
            # Python's `$` also matches before a final newline.
            "not": {"type": "string", "pattern": "[\\r\\n]"},
        },
        "address": {
            "type": "string",
            "pattern": (
                '^[^\\s<>"@,;:()\\[\\]\\\\]{1,64}@' '[^\\s<>"@,;:()\\[\\]\\\\]{1,255}$'
            ),
            "not": {"type": "string", "pattern": "[\\r\\n]"},
        },
        "subject": {
            "type": ["string", "null"],
            "maxLength": 998,
            "not": {"type": "string", "pattern": "[\\r\\n]"},
        },
        "text": {"type": ["string", "null"], "maxLength": MAX_PART_BYTES},
        "html": {"type": ["string", "null"], "maxLength": MAX_PART_BYTES},
        "headers": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                name: {
                    "type": "string",
                    "maxLength": MAX_HEADER_VALUE_LENGTH,
                    "not": {"pattern": "[\\r\\n]"},
                }
                for name in ALLOWED_HEADERS
            },
        },
        "attachments": {
            "type": "array",
            "maxItems": MAX_ATTACHMENTS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "ref", "filename", "content_type"],
                "properties": {
                    "kind": {
                        "type": "string",
                        "pattern": "^[a-z][a-z0-9_.-]*$",
                        "maxLength": MAX_ATTACHMENT_KIND_LENGTH,
                    },
                    "ref": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_ATTACHMENT_REF_LENGTH,
                    },
                    "filename": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_ATTACHMENT_FILENAME_LENGTH,
                    },
                    "content_type": {
                        "type": "string",
                        "enum": sorted(ALLOWED_ATTACHMENT_CONTENT_TYPES),
                    },
                },
            },
        },
    },
}


__all__ = [
    "EMAIL_SEND_CAPABILITY_ID",
    "EMAIL_SEND_COMMAND_SCHEMA",
    "ALLOWED_ATTACHMENT_CONTENT_TYPES",
    "ALLOWED_HEADERS",
    "MAX_PART_BYTES",
    "FEATURE_ATTACHMENTS",
    "FEATURE_HTML",
    "AttachmentRef",
    "DeliveryProvider",
    "OutboundMessage",
    "ProviderResult",
    "Sent",
    "Suppressed",
    "send",
]
