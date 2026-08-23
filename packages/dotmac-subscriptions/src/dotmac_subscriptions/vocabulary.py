"""Open manifest-declared charge-model and obligation-source registries."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from dotmac_kernel.modules import AnyManifest

from dotmac_subscriptions.errors import SubscriptionDataError


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

    @classmethod
    def from_manifests(
        cls, manifests: Iterable[AnyManifest]
    ) -> SubscriptionVocabularyRegistry:
        charge_models: dict[str, str] = {}
        obligation_sources: dict[str, str] = {}
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
        return cls(charge_models, obligation_sources)

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


__all__ = ["SubscriptionVocabularyRegistry"]
