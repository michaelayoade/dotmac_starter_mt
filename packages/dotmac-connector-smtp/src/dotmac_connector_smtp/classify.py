"""SMTP reply classification into kernel receipt statuses.

Only `bounced` (and `complaint`) suppress an address in the kernel, so a
permanent verdict is `bounced` ONLY for the enhanced statuses that say the
RECIPIENT address is bad, at the RCPT stage: 5.1.1, 5.1.2, 5.1.3, 5.1.6, 5.1.10.
Sender-side 5.1.7 / 5.1.8 mean OUR from address is wrong; a bounce there would
suppress every recipient of a misconfigured from-domain, so they are an
operator fault (`OPERATOR_FAULT`, which the provider RAISES: no receipt).

| stage            | reply                                      | outcome        |
|------------------|--------------------------------------------|----------------|
| rcpt             | 5.1.1 5.1.2 5.1.3 5.1.6 5.1.10             | bounced        |
| rcpt             | 5.1.7 5.1.8 (sender address)               | OPERATOR_FAULT |
| rcpt             | other 5.1.x (5.1.0 .4 .5 .9), 5.7.x        | rejected       |
| rcpt             | 5.2.x mailbox-state, 5.4.x routing         | failed         |
| rcpt             | any other 5xx, or 5xx without 5.x.x        | rejected       |
| mail             | any 5xx (our sender/config is refused)     | OPERATOR_FAULT |
| data             | any 5xx                                    | rejected       |
| connect/ehlo/tls | any 5xx                                    | failed         |
| any              | 4xx or unexpected non-2xx/5xx              | failed         |

`failed` is the kernel's retryable verdict. 5.2.x and 5.4.x at RCPT are soft by
the kernel's own rule (mailbox full is never `bounced`). A 5xx on a session
stage is an operator-side fault but transient in practice, so it is retryable.
Authentication failure is not classified either: the provider raises.

Bounce coverage is synchronous only: asynchronous DSNs are not captured.
"""

from __future__ import annotations

import re

#: Outcome vocabulary of the moved table. The VALUES are the kernel delivery
#: receipt statuses the table was written against (`dotmac_kernel.delivery_models`
#: `DELIVERY_BOUNCED` / `DELIVERY_FAILED` / `DELIVERY_REJECTED`); a connector
#: distribution depends on `dotmac-integration` only, so the three literals are
#: restated here and pinned by the golden test rather than imported.
DELIVERY_BOUNCED = "bounced"
DELIVERY_FAILED = "failed"
DELIVERY_REJECTED = "rejected"

STAGE_SESSION = "session"
STAGE_MAIL = "mail"
STAGE_RCPT = "rcpt"
STAGE_DATA = "data"

#: Not a kernel status: the provider raises instead of writing a receipt.
OPERATOR_FAULT = "operator_fault"

#: Enhanced statuses (subject, detail) that prove the recipient address is bad.
_BAD_RECIPIENT = frozenset({(1, 1), (1, 2), (1, 3), (1, 6), (1, 10)})
#: Sender-address statuses at RCPT: our from address, not the recipient.
_SENDER_FAULT = frozenset({(1, 7), (1, 8)})

_ENHANCED_RE = re.compile(r"^([245])\.(\d{1,3})\.(\d{1,3})(?:\s|$)")


def enhanced_status(code: int, text: str) -> tuple[int, int, int] | None:
    """The RFC 3463 status at the start of a reply text, if it agrees with the
    reply code's class digit; otherwise None (a mismatch is not trusted)."""
    match = _ENHANCED_RE.match(text.lstrip())
    if match is None or int(match.group(1)) != code // 100:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def classify_reply(stage: str, code: int, text: str) -> str:
    """Kernel status for a non-success reply at `stage`, or `OPERATOR_FAULT`."""
    if not 500 <= code < 600:
        return DELIVERY_FAILED
    if stage == STAGE_SESSION:
        return DELIVERY_FAILED
    if stage == STAGE_MAIL:
        return OPERATOR_FAULT
    enhanced = enhanced_status(code, text)
    pair = (enhanced[1], enhanced[2]) if enhanced else None
    if stage == STAGE_RCPT and pair is not None:
        if pair in _BAD_RECIPIENT:
            return DELIVERY_BOUNCED
        if pair in _SENDER_FAULT:
            return OPERATOR_FAULT
        if pair[0] in (2, 4):
            return DELIVERY_FAILED
    return DELIVERY_REJECTED


__all__ = [
    "OPERATOR_FAULT",
    "STAGE_DATA",
    "STAGE_MAIL",
    "STAGE_RCPT",
    "STAGE_SESSION",
    "classify_reply",
    "enhanced_status",
]
