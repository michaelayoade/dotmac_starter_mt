"""Deployment-profile registry (WS1) — declarative profiles + fail-closed validation.

A `DeploymentProfileSpec` is a **declarative bundle over independent axes** (which
modules must/mustn't be present, which provider implementation fills each seam,
and the locale/currency/legal/residency posture). It is emphatically NOT an enum
that feature code branches on — "profile names are conveniences over independent
axes" (ADR-0003). Feature code never reads a profile string; it reads explainable
local entitlement/capability decisions.

The `DeploymentProfileRegistry` holds the declared profiles (unique `code`),
answers "is this profile code valid?", and runs **startup validation that fails
closed**: a required module missing, a forbidden module present, or a named
provider that does not resolve is an error, reported in a human-readable
effective-profile report. Pure/in-memory — no database, no fleet tables (those
belong to the vendor control plane, not the kernel).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Set
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class DeploymentProfileSpec:
    """One deployment profile — a declaration over independent axes (ADR-0003).

    Every axis is a deliberate decision; a convenient profile name must not erase
    the underlying choices. `required_modules`/`forbidden_modules` reference module
    codes; the eight `*_provider` fields name provider implementations that must
    resolve against the running provider registry.
    """

    code: str
    # Versioned desired composition (WS1 public-contract rule): a profile's
    # effective module/provider set may only change via an explicit version bump,
    # never silently. `(code, version)` is the stable identifier consumers pin to.
    version: str
    required_modules: frozenset[str]
    # Provider selections — one implementation name per seam.
    commercial_provider: str
    provisioning_provider: str
    identity_provider: str
    telemetry_provider: str
    update_provider: str
    ingress_provider: str
    dns_verification_provider: str
    tls_provider: str
    # Locale / currency / legal / residency posture.
    default_locale: str
    supported_locales: frozenset[str]
    allowed_currencies: frozenset[str]
    legal_authority: str
    data_residency: str
    # Modules that must NOT be installed/enabled under this profile.
    forbidden_modules: frozenset[str] = field(default_factory=frozenset)

    def provider_selections(self) -> Mapping[str, str]:
        """{axis: provider-implementation-name} for the eight provider seams."""
        return {
            "commercial": self.commercial_provider,
            "provisioning": self.provisioning_provider,
            "identity": self.identity_provider,
            "telemetry": self.telemetry_provider,
            "update": self.update_provider,
            "ingress": self.ingress_provider,
            "dns_verification": self.dns_verification_provider,
            "tls": self.tls_provider,
        }


@dataclass(frozen=True, slots=True)
class ProfileValidationReport:
    """Outcome of validating a profile against a running deployment.

    `ok` is True only when `errors` is empty. `render()` is the human-readable
    effective-profile report (WS1 requires startup to emit one)."""

    profile_code: str
    profile_version: str
    ok: bool
    errors: tuple[str, ...]
    effective_modules: frozenset[str]
    effective_providers: Mapping[str, str]

    def render(self) -> str:
        modules = ", ".join(sorted(self.effective_modules)) or "(none)"
        lines = [
            f"Deployment profile: {self.profile_code} (v{self.profile_version})",
            f"  status: {'OK' if self.ok else 'INVALID'}",
            f"  required modules: {modules}",
            "  providers:",
        ]
        lines += [
            f"    {axis}: {name}"
            for axis, name in sorted(self.effective_providers.items())
        ]
        if self.errors:
            lines.append("  errors:")
            lines += [f"    - {e}" for e in self.errors]
        return "\n".join(lines)


class DuplicateProfileError(ValueError):
    """Two profiles declared the same code."""


class UnknownProfileError(KeyError):
    """A profile code was referenced that is not registered."""


class DeploymentProfileRegistry:
    """The registered deployment profiles, keyed by unique `code`."""

    __slots__ = ("_by_code",)

    def __init__(self, profiles: Iterable[DeploymentProfileSpec]) -> None:
        by_code: dict[str, DeploymentProfileSpec] = {}
        for profile in profiles:
            if profile.code in by_code:
                raise DuplicateProfileError(
                    f"deployment profile {profile.code!r} declared more than once"
                )
            by_code[profile.code] = profile
        self._by_code = by_code

    def is_valid_code(self, code: str) -> bool:
        """The 'profile code valid' guard (a plan-ready precondition)."""
        return code in self._by_code

    def get(self, code: str) -> DeploymentProfileSpec:
        try:
            return self._by_code[code]
        except KeyError as exc:
            raise UnknownProfileError(
                f"deployment profile {code!r} is not registered"
            ) from exc

    def codes(self) -> frozenset[str]:
        return frozenset(self._by_code)

    def validate(
        self,
        code: str,
        *,
        installed_modules: Set[str],
        enabled_modules: Set[str],
        available_providers: Set[str],
    ) -> ProfileValidationReport:
        """Validate a profile against the running deployment, fail-closed.

        Errors (each reported, not raised) for: a `required_modules` code not
        enabled; a `forbidden_modules` code installed; a named provider not in
        `available_providers`. `enabled_modules` must be a subset of
        `installed_modules`.
        """
        profile = self.get(code)
        errors: list[str] = []

        for module in sorted(profile.required_modules - set(enabled_modules)):
            errors.append(f"required module {module!r} is not enabled")
        for module in sorted(profile.forbidden_modules & set(installed_modules)):
            errors.append(f"forbidden module {module!r} is installed")
        for axis, provider in sorted(profile.provider_selections().items()):
            if provider not in available_providers:
                errors.append(f"provider for {axis!r} ({provider!r}) is not available")

        return ProfileValidationReport(
            profile_code=code,
            profile_version=profile.version,
            ok=not errors,
            # Total alphabetical order → the report is byte-for-byte deterministic
            # for a given input, regardless of which checks failed.
            errors=tuple(sorted(errors)),
            effective_modules=profile.required_modules,
            effective_providers=dict(profile.provider_selections()),
        )


__all__ = [
    "DeploymentProfileSpec",
    "DeploymentProfileRegistry",
    "ProfileValidationReport",
    "DuplicateProfileError",
    "UnknownProfileError",
]
