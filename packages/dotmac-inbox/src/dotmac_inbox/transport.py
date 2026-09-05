"""Typed, provider-neutral transport message correlation identity."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from dotmac_inbox.channels import TransportMessageIdScope, channel_spec

_RAW_REF_MAX = 255
_ACCOUNT_SCOPE_MAX = 160


def _frame(*parts: str) -> str:
    encoded = b"".join(
        len(part.encode("utf-8")).to_bytes(4, "big") + part.encode("utf-8")
        for part in parts
    )
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class TransportMessageIdentity:
    """Immutable coordinates for one provider message reference."""

    channel: str
    raw_ref: str
    scope: TransportMessageIdScope | None = None
    account_scope: str | None = None

    def __post_init__(self) -> None:
        spec = channel_spec(self.channel)
        object.__setattr__(self, "channel", spec.code)
        declared = spec.transport_message_id_scope
        scope = declared if self.scope is None else self.scope
        if scope is None:
            raise ValueError(f"channel {spec.code!r} has no transport id scope")
        if declared is not None and scope is not declared:
            raise ValueError(
                f"channel {spec.code!r} declares transport scope {declared.value!r}"
            )
        object.__setattr__(self, "scope", scope)
        if not self.raw_ref or not self.raw_ref.strip():
            raise ValueError("transport raw_ref must not be blank")
        if len(self.raw_ref.encode("utf-8")) > _RAW_REF_MAX:
            raise ValueError("transport raw_ref exceeds 255 UTF-8 bytes")
        if scope is TransportMessageIdScope.ACCOUNT:
            if self.account_scope is None or not self.account_scope.strip():
                raise ValueError(
                    "account-scoped transport identity requires account_scope"
                )
            if len(self.account_scope.encode("utf-8")) > _ACCOUNT_SCOPE_MAX:
                raise ValueError("account_scope exceeds 160 UTF-8 bytes")
        elif self.account_scope is not None:
            raise ValueError("account_scope is valid only for account-scoped identity")

    @property
    def key(self) -> str:
        scope = self.scope
        if scope is None or scope is TransportMessageIdScope.NONE:
            raise ValueError(
                "transport identity scope NONE cannot be bound or looked up"
            )
        parts = ["tm1", scope.value, self.channel]
        if scope is TransportMessageIdScope.ACCOUNT:
            parts.append(self.account_scope or "")
        parts.append(self.raw_ref)
        return "tm1:" + _frame(*parts)


__all__ = ["TransportMessageIdentity"]
