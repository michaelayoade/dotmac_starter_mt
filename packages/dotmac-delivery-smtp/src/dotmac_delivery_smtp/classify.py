"""SMTP reply classification into kernel receipt statuses.

Only `bounced` (and `complaint`) suppress an address in the kernel, so a
permanent verdict is `bounced` ONLY when the server says the recipient address
itself is bad (enhanced status 5.1.x at the RCPT stage). Everything permanent
that is not provably about the address is `rejected`.

| stage            | reply                                   | status   |
|------------------|-----------------------------------------|----------|
| rcpt             | 5.1.x (bad destination address)         | bounced  |
| rcpt             | 5.7.x (policy / security)               | rejected |
| rcpt             | 5.2.x mailbox-state, 5.4.x routing      | failed   |
| rcpt             | any other 5xx, or 5xx without 5.x.x     | rejected |
| mail / data      | any 5xx (incl. 5.1.x, 5.7.x)            | rejected |
| connect/ehlo/tls | any 5xx                                 | failed   |
| any              | 4xx or unexpected non-2xx/5xx           | failed   |

`failed` is the kernel's retryable verdict. 5.2.x and 5.4.x at RCPT are soft by
the kernel's own rule (mailbox full is never `bounced`). A 5xx on a session
stage (greeting, EHLO, STARTTLS) is an operator-side fault, so it is retryable
rather than a verdict on the message. Authentication failure is NOT classified:
the adapter raises, because a wrong password is not a delivery outcome.

Bounce coverage is synchronous only: asynchronous DSNs are not captured.
"""

from __future__ import annotations

import re

from dotmac_kernel.delivery_models import (
    DELIVERY_BOUNCED,
    DELIVERY_FAILED,
    DELIVERY_REJECTED,
)

STAGE_SESSION = "session"
STAGE_MAIL = "mail"
STAGE_RCPT = "rcpt"
STAGE_DATA = "data"

_ENHANCED_RE = re.compile(r"^([245])\.(\d{1,3})\.(\d{1,3})(?:\s|$)")


def enhanced_status(code: int, text: str) -> tuple[int, int, int] | None:
    """The RFC 3463 status at the start of a reply text, if it agrees with the
    reply code's class digit; otherwise None (a mismatch is not trusted)."""
    match = _ENHANCED_RE.match(text.lstrip())
    if match is None or int(match.group(1)) != code // 100:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def classify_reply(stage: str, code: int, text: str) -> str:
    """Kernel status for a non-success reply at `stage`."""
    if not 500 <= code < 600:
        return DELIVERY_FAILED
    if stage == STAGE_SESSION:
        return DELIVERY_FAILED
    enhanced = enhanced_status(code, text)
    subject = enhanced[1] if enhanced else None
    if stage == STAGE_RCPT:
        if subject == 1:
            return DELIVERY_BOUNCED
        if subject in (2, 4):
            return DELIVERY_FAILED
    return DELIVERY_REJECTED


__all__ = [
    "STAGE_DATA",
    "STAGE_MAIL",
    "STAGE_RCPT",
    "STAGE_SESSION",
    "classify_reply",
    "enhanced_status",
]
