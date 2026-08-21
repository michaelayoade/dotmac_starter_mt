"""Typed remote-access commands and provider-neutral intents."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class RemoteAccessRequestInput:
    request_key: str
    target_ref: str
    purpose: str
    scopes: tuple[str, ...]
    requester_ref: str


@dataclass(frozen=True, slots=True)
class RemoteAccessIntent:
    intent_key: str
    action: str
    grant_id: UUID
    target_ref: str
    scopes: tuple[str, ...]

