"""Public typed surface for ``dotmac-reseller-management``."""

from dotmac_reseller_management.contracts import (
    BindCustomerAccount,
    BindMember,
    ChangeStatus,
    ContractError,
    CreateResellerAccount,
    PublishAuthority,
    SetParent,
)
from dotmac_reseller_management.manifest import module
from dotmac_reseller_management.migrations import versions_dir
from dotmac_reseller_management.service import (
    Conflict,
    InvalidTransition,
    NotFound,
    ResellerManagementError,
    bind_customer_account,
    bind_member,
    create_account,
    publish_authority,
    set_parent,
    transition_account,
)

__version__ = "0.1.0a1"

__all__ = [
    "BindCustomerAccount",
    "BindMember",
    "ChangeStatus",
    "Conflict",
    "ContractError",
    "CreateResellerAccount",
    "InvalidTransition",
    "NotFound",
    "PublishAuthority",
    "ResellerManagementError",
    "SetParent",
    "__version__",
    "bind_customer_account",
    "bind_member",
    "create_account",
    "module",
    "publish_authority",
    "set_parent",
    "transition_account",
    "versions_dir",
]
