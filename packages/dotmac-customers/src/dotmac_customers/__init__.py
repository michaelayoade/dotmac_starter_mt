"""Tenant customer-account owner."""

from dotmac_customers.contracts import (
    AccountStatus,
    Conflict,
    CreateCustomerAccount,
    CustomerError,
    LinkPartyReference,
    NotFound,
    PartyReferenceRole,
    SetCustomerProfile,
)
from dotmac_customers.manifest import module
from dotmac_customers.migrations import versions_dir
from dotmac_customers.models import (
    CustomerAccount,
    CustomerPartyReference,
    CustomerProfile,
)
from dotmac_customers.service import (
    create_account,
    get_account,
    link_party_reference,
    set_profile,
    transition_account,
)

__version__ = "0.1.0a1"

__all__ = [
    "AccountStatus",
    "Conflict",
    "CreateCustomerAccount",
    "CustomerAccount",
    "CustomerError",
    "CustomerPartyReference",
    "CustomerProfile",
    "LinkPartyReference",
    "NotFound",
    "PartyReferenceRole",
    "SetCustomerProfile",
    "__version__",
    "create_account",
    "get_account",
    "link_party_reference",
    "module",
    "set_profile",
    "transition_account",
    "versions_dir",
]
