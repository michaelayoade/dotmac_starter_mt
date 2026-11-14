"""Outbox event types emitted by money flows (payloads carry references only)."""

from __future__ import annotations

SETTLE_SETTLED_EVENT_TYPE = "money_flows.settle.settled.v1"
SETTLE_ATTENTION_EVENT_TYPE = "money_flows.settle.attention.v1"

EVENT_TYPES = (SETTLE_SETTLED_EVENT_TYPE, SETTLE_ATTENTION_EVENT_TYPE)

__all__ = [
    "EVENT_TYPES",
    "SETTLE_ATTENTION_EVENT_TYPE",
    "SETTLE_SETTLED_EVENT_TYPE",
]
