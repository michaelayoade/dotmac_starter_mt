"""Public surface of ``dotmac-money-flows``.

Money flows are composition over the published owners: instance state only. The
F1 Settle flow turns one confirmed payment into accepted cash and its
application; Billing and Payments stay the only sources of money facts.
"""

from dotmac_money_flows.contracts import (
    DEFAULT_ACCEPTED_SOURCES,
    DepositApplication,
    SettleConfirmationV1,
    SettleInstanceV1,
    SettlementTargetResolver,
    SettlementTargetV1,
    SettleOutcomeV1,
    SettlePolicyReader,
    SettlePolicyV1,
    SettlePorts,
    SettleRefused,
    SettleState,
    SettleWithholdingV1,
)
from dotmac_money_flows.events import (
    SETTLE_ATTENTION_EVENT_TYPE,
    SETTLE_SETTLED_EVENT_TYPE,
)
from dotmac_money_flows.manifest import module
from dotmac_money_flows.migrations import versions_dir
from dotmac_money_flows.settle import (
    get_settle_instance,
    is_settle_replay_conflict,
    list_settle_attention,
    record_settle_refusal,
    retry_settle,
    settle_confirmation,
)

__version__ = "0.1.0a1"

__all__ = [
    "DEFAULT_ACCEPTED_SOURCES",
    "SETTLE_ATTENTION_EVENT_TYPE",
    "SETTLE_SETTLED_EVENT_TYPE",
    "DepositApplication",
    "SettleConfirmationV1",
    "SettleInstanceV1",
    "SettleOutcomeV1",
    "SettlePolicyReader",
    "SettlePolicyV1",
    "SettlePorts",
    "SettleRefused",
    "SettleState",
    "SettleWithholdingV1",
    "SettlementTargetResolver",
    "SettlementTargetV1",
    "__version__",
    "get_settle_instance",
    "is_settle_replay_conflict",
    "list_settle_attention",
    "module",
    "record_settle_refusal",
    "retry_settle",
    "settle_confirmation",
    "versions_dir",
]
