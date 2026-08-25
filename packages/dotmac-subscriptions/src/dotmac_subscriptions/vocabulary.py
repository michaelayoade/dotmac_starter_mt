"""Open declared charge-model, obligation-source and treatment-reason registries."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from dotmac_kernel.modules import AnyManifest

from dotmac_subscriptions.errors import SubscriptionDataError

#: The seven product-neutral non-standard billing-treatment reasons ported from
#: Sub's `BillingTreatmentReason`.  They are DECLARATIONS owned by this module,
#: never an enum: ADR-0008 makes every module-owned vocabulary an open registry
#: because a closed one re-imposes a module release on every product that needs
#: an eighth reason, and a CHECK constraint would re-close it in the database
#: too.  The backing column is a plain string for exactly that reason.
PORTED_BILLING_TREATMENT_REASONS: Final[tuple[str, ...]] = (
    "internal_service",
    "staff_benefit",
    "partner_service",
    "community_support",
    "commercial_concession",
    "sponsored_service",
    "other_approved",
)

#: The module code that owns the seven ported reasons above.
PORTED_REASON_OWNER: Final = "subscriptions"


@dataclass(frozen=True, slots=True)
class BillingTreatmentReasonDeclaration:
    """One module's claim to a set of non-standard treatment reason codes.

    A product adds its own reasons by passing a declaration naming ITS module
    code; a code already claimed by another declaration is refused, so the
    vocabulary stays open while every member keeps exactly one owner.
    """

    module: str
    codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.module:
            raise SubscriptionDataError(
                "vocabulary.missing_declaration_owner",
                "A billing treatment reason declaration must name its module.",
            )


def _code(value: str, *, vocabulary: str) -> str:
    if not value or len(value) > 120:
        raise SubscriptionDataError(
            "vocabulary.invalid_code",
            f"{vocabulary} codes are required and at most 120 characters.",
        )
    return value


@dataclass(frozen=True, slots=True)
class SubscriptionVocabularyRegistry:
    """One owner per declared code, derived from installed manifests."""

    charge_models: dict[str, str]
    obligation_sources: dict[str, str]
    billing_treatment_reasons: dict[str, str]

    @classmethod
    def from_manifests(
        cls,
        manifests: Iterable[AnyManifest],
        *,
        reason_declarations: Iterable[BillingTreatmentReasonDeclaration] = (),
    ) -> SubscriptionVocabularyRegistry:
        charge_models: dict[str, str] = {}
        obligation_sources: dict[str, str] = {}
        billing_treatment_reasons: dict[str, str] = {}
        cls._add(
            billing_treatment_reasons,
            PORTED_BILLING_TREATMENT_REASONS,
            owner=PORTED_REASON_OWNER,
            vocabulary="billing treatment reason",
        )
        for declaration in reason_declarations:
            cls._add(
                billing_treatment_reasons,
                declaration.codes,
                owner=declaration.module,
                vocabulary="billing treatment reason",
            )
        for manifest in manifests:
            cls._add(
                charge_models,
                getattr(manifest, "charge_models", ()),
                owner=manifest.name,
                vocabulary="charge model",
            )
            cls._add(
                obligation_sources,
                getattr(manifest, "obligation_sources", ()),
                owner=manifest.name,
                vocabulary="obligation source",
            )
        return cls(charge_models, obligation_sources, billing_treatment_reasons)

    @staticmethod
    def _add(
        target: dict[str, str],
        declarations: Iterable[str],
        *,
        owner: str,
        vocabulary: str,
    ) -> None:
        for declaration in declarations:
            code = _code(declaration, vocabulary=vocabulary)
            previous = target.get(code)
            if previous is not None:
                raise SubscriptionDataError(
                    "vocabulary.duplicate_owner",
                    f"{vocabulary} {code!r} is declared by more than one module: "
                    f"{previous!r} and {owner!r}.",
                )
            target[code] = owner

    def require_charge_model(self, code: str) -> str:
        try:
            return self.charge_models[code]
        except KeyError as exc:
            raise SubscriptionDataError(
                "vocabulary.undeclared_charge_model",
                f"charge model {code!r} is undeclared",
            ) from exc

    def require_obligation_source(self, code: str) -> str:
        try:
            return self.obligation_sources[code]
        except KeyError as exc:
            raise SubscriptionDataError(
                "vocabulary.undeclared_obligation_source",
                f"obligation source {code!r} is undeclared",
            ) from exc

    def require_billing_treatment_reason(self, code: str) -> str:
        try:
            return self.billing_treatment_reasons[code]
        except KeyError as exc:
            raise SubscriptionDataError(
                "vocabulary.undeclared_billing_treatment_reason",
                f"billing treatment reason {code!r} is undeclared",
            ) from exc


__all__ = [
    "PORTED_BILLING_TREATMENT_REASONS",
    "PORTED_REASON_OWNER",
    "BillingTreatmentReasonDeclaration",
    "SubscriptionVocabularyRegistry",
]
