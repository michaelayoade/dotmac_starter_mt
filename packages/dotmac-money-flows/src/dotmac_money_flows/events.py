"""Outbox event types emitted by money flows (payloads carry references only)."""

from __future__ import annotations

SETTLE_SETTLED_EVENT_TYPE = "money_flows.settle.settled.v1"
SETTLE_ATTENTION_EVENT_TYPE = "money_flows.settle.attention.v1"
FUNDED_CHARGE_DISCHARGED_EVENT_TYPE = "money_flows.funded_charge.discharged.v1"
FUNDED_CHARGE_ATTENTION_EVENT_TYPE = "money_flows.funded_charge.attention.v1"
#: The refs-only observation the assembly turns into a Service Access Policy
#: ``RecordAccessInputV2`` (signal PREPAID_DEPLETED).
FUNDED_CHARGE_ACCESS_EVENT_TYPE = "money_flows.funded_charge.access.v1"
#: Continuation of a capped re-evaluation; the assembly routes it back to
#: ``on_settlement_settled``.
FUNDED_CHARGE_REEVALUATE_EVENT_TYPE = "money_flows.funded_charge.reevaluate.v1"
#: Continuation of a capped per-account terms re-check; the assembly routes it
#: back to ``reevaluate_account_terms``.
FUNDED_CHARGE_TERMS_RECHECK_EVENT_TYPE = "money_flows.funded_charge.terms_recheck.v1"

EVENT_TYPES = (
    SETTLE_SETTLED_EVENT_TYPE,
    SETTLE_ATTENTION_EVENT_TYPE,
    FUNDED_CHARGE_DISCHARGED_EVENT_TYPE,
    FUNDED_CHARGE_ATTENTION_EVENT_TYPE,
    FUNDED_CHARGE_ACCESS_EVENT_TYPE,
    FUNDED_CHARGE_REEVALUATE_EVENT_TYPE,
    FUNDED_CHARGE_TERMS_RECHECK_EVENT_TYPE,
)

__all__ = [
    "EVENT_TYPES",
    "FUNDED_CHARGE_ACCESS_EVENT_TYPE",
    "FUNDED_CHARGE_ATTENTION_EVENT_TYPE",
    "FUNDED_CHARGE_DISCHARGED_EVENT_TYPE",
    "FUNDED_CHARGE_REEVALUATE_EVENT_TYPE",
    "FUNDED_CHARGE_TERMS_RECHECK_EVENT_TYPE",
    "SETTLE_ATTENTION_EVENT_TYPE",
    "SETTLE_SETTLED_EVENT_TYPE",
]
