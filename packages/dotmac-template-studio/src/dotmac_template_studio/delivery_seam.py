"""The "send a test to me" delivery seam (ADR-0006 § 5f, § 5g).

Template Studio decides what a message SAYS; it does not deliver. The editor's
"Send test to me" button therefore calls a callable the PRODUCT supplies at
composition time, and the button is hidden while none is configured:

    from dotmac_template_studio import configure_test_send

    def send_test(db, message: OwnAddressTestMessage) -> None:
        ...  # product: hand `message` to the kernel delivery owner

    configure_test_send(send_test, channels=("email",))

Deliberately narrow. The message carries the ACTING USER's own address, taken
from the authenticated party by the web adapter; there is no recipient
parameter an operator can type, and the module never imports a delivery owner.
The product decides consent, category and provider when it implements the
callable (a test send to oneself is still a send).
"""

from __future__ import annotations

import threading
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class OwnAddressTestMessage:
    """One rendered test message, addressed to the acting party themselves."""

    tenant_id: UUID
    recipient_party_id: UUID
    recipient_email: str
    channel: str
    template_slug: str
    template_version: int
    subject: str | None
    text: str
    html: str | None = None


class OwnAddressSender(Protocol):
    """What the product supplies. May raise; the editor reports a generic failure."""

    def __call__(self, db: Any, message: OwnAddressTestMessage) -> None: ...


_lock = threading.Lock()
_sender: OwnAddressSender | None = None
_channels: frozenset[str] = frozenset()


def configure_test_send(
    sender: OwnAddressSender | None, *, channels: Collection[str] = ("email",)
) -> None:
    """Install (or, with `None`, remove) the product's test-send callable."""
    global _sender, _channels
    with _lock:
        _sender = sender
        _channels = frozenset(channels) if sender is not None else frozenset()


def can_test_send(channel: str) -> bool:
    return _sender is not None and channel in _channels


def get_test_sender() -> OwnAddressSender | None:
    return _sender


__all__ = [
    "OwnAddressSender",
    "OwnAddressTestMessage",
    "configure_test_send",
    "get_test_sender",
    "can_test_send",
]
