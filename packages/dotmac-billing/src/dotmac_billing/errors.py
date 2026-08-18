"""Stable fail-closed Billing errors."""


class BillingError(Exception):
    """Base for Billing refusals with a stable machine code."""

    def __init__(self, code: str, message: str, **details: object) -> None:
        super().__init__(message)
        self.code = f"billing.{code}"
        self.message = message
        self.details = dict(details)


class BillingRuleViolation(BillingError, ValueError):
    """A proposed state violates the Billing contract."""


class BillingConflict(BillingError):
    """A stable source identity was reused with different evidence."""


__all__ = ["BillingConflict", "BillingError", "BillingRuleViolation"]
