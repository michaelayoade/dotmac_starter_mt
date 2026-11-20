"""The ``expenses.claim_input_vat`` tenant setting, as a typed declaration.

Whether recoverable input VAT on a staff claim is booked at acceptance is a
tenant POLICY: ``none`` (the safe default) refuses any tax amount, ``immediate``
books it to the immediate input-VAT account. It is never the deferral account:
the employee paid the merchant at purchase, so the VAT was never deferred.

This module only DECLARES the setting. It is deliberately not registered at
import time: the Starter assembly does not compose Expenses, and a registered
spec naming a domain no assembly manifest declares would fail the Starter's own
declaration gate. The adopting assembly declares the ``expenses`` setting domain
on its manifest, registers ``claim_input_vat_spec()``, shows it in its settings
UI, reads it at acceptance and passes the value as
``AcceptClaimForReimbursement.input_vat_mode``. The owner then freezes it on the
liability, so a later change never alters an accepted claim. Admissions and the
acceptance of never-posted legacy claims carry no VAT whatever the setting says.
"""

from __future__ import annotations

from dotmac_kernel.setting_value_types import SettingValueType
from dotmac_kernel.settings_models import SettingDomain
from dotmac_kernel.settings_resolver import SettingSpec

from dotmac_expenses.contracts import (
    CLAIM_INPUT_VAT_DEFAULT,
    CLAIM_INPUT_VAT_DOMAIN,
    CLAIM_INPUT_VAT_KEY,
    ClaimInputVat,
    InvalidCommand,
)


def claim_input_vat_spec() -> SettingSpec[str]:
    """The typed spec for ``expenses.claim_input_vat`` (default ``none``)."""
    return SettingSpec(
        domain=SettingDomain(CLAIM_INPUT_VAT_DOMAIN),
        key=CLAIM_INPUT_VAT_KEY,
        value_type=SettingValueType("string"),
        default=CLAIM_INPUT_VAT_DEFAULT.value,
        label="Input VAT on staff expense claims",
        description=(
            "none: staff claims carry no recoverable input VAT and a tax amount "
            "is refused. immediate: input VAT is booked at acceptance to the "
            "immediate input-VAT account. The value in force at acceptance is "
            "frozen on the claim; changing it never alters an accepted claim."
        ),
        allowed={mode.value for mode in ClaimInputVat},
    )


def parse_claim_input_vat(value: object) -> ClaimInputVat:
    """Read a resolved setting value fail-closed: anything unknown is refused."""
    try:
        return ClaimInputVat(value)  # type: ignore[arg-type]
    except ValueError as exc:
        raise InvalidCommand(
            "expenses.claim_input_vat must be 'none' or 'immediate'"
        ) from exc


__all__ = ["claim_input_vat_spec", "parse_claim_input_vat"]
