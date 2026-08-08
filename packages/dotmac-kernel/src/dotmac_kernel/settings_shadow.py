"""Shadow-compare a product's own settings resolver against the kernel's.

ADR-0003 requires that an existing product adopt through "adapters,
contract/shadow tests, expand/contract migrations, reconciliation, and
one-writer cutovers — never a big-bang rewrite". For settings the kernel
supplied none of that, so every product would have invented its own way of
answering the only question that matters before a cutover:

    does the kernel resolve every setting to exactly what we resolve today?

This module answers it. The product supplies a callable that reads a setting
its own way; the kernel resolves the same setting its way; the two are compared
and the disagreement is RECORDED. Nothing here changes what a request is served
until a deployment says so.

## The phases, and why the order is not negotiable

`ShadowPhase` is set per deployment and moves in one direction:

1. `LEGACY_AUTHORITATIVE` — the product's value is served. The kernel's is
   computed and compared. This is where you live until divergence is zero.
2. `KERNEL_AUTHORITATIVE` — the kernel's value is served. The product's is
   still computed and compared, so a regression is visible immediately and the
   phase can be stepped back without a deploy.
3. `KERNEL_ONLY` — the legacy reader is no longer called. Now, and not before,
   the product's resolver and columns can be deleted.

Serving before verifying is the failure this ordering exists to prevent, and it
is why there is no "compare and serve whichever is non-null" mode: that is a
silent third answer belonging to neither system.

## What is deliberately NOT reported

**Never the values.** A divergence report names the domain, key and scope, and
says whether the two agreed — never what either produced. A settings table
holds credentials, and a comparison report is exactly the kind of artefact that
gets pasted into an issue. Where a type differs the TYPE NAMES are reported,
because `int` vs `str` is the common real cause and neither name is a secret.

**Nothing raises.** A shadow phase that can crash a request is worse than the
drift it is looking for; a comparison failure is itself recorded as a
divergence. The one exception is a legacy callable that raises during
`KERNEL_ONLY`, which is a programming error — it should not be called at all.

## Cost

Shadow doubles settings reads for as long as it is on. That is finite, bounded
by the phase, and the alternative is finding the disagreement in production
after the old path is gone.
"""

from __future__ import annotations

import enum
import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from dotmac_kernel.setting_scopes import SettingScope
from dotmac_kernel.settings_resolver import (
    SettingSpec,
    all_specs,
    resolve_with_source,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from dotmac_kernel.settings_models import SettingDomain

logger = logging.getLogger(__name__)


class ShadowPhase(enum.StrEnum):
    """Which system's answer is SERVED. See the module docstring."""

    LEGACY_AUTHORITATIVE = "legacy_authoritative"
    KERNEL_AUTHORITATIVE = "kernel_authoritative"
    KERNEL_ONLY = "kernel_only"


# A product's own read of one setting. Takes nothing: the product closes over
# whatever it needs (its session, its own key names, its own scope notion),
# which is what keeps this interface free of any assumption about how the
# product's settings are shaped.
LegacyRead = Callable[[], object]


@dataclass(frozen=True, slots=True)
class Divergence:
    """One setting the two resolvers disagreed about.

    Carries no values — see the module docstring on why.
    """

    domain: str
    key: str
    scope_kind: str
    kernel_type: str
    legacy_type: str
    # Set when the comparison itself failed (a legacy reader raised, a value
    # was not comparable). Distinguished from a plain disagreement because the
    # fix is different: one is drift, the other is a broken adapter.
    error: str | None = None

    def describe(self) -> str:
        where = f"{self.domain}/{self.key} @{self.scope_kind}"
        if self.error:
            return f"{where}: comparison failed ({self.error})"
        return f"{where}: kernel={self.kernel_type} legacy={self.legacy_type}"


@dataclass
class ShadowReport:
    """The outcome of a sweep. `clean` is the only thing a cutover gate reads."""

    compared: int = 0
    divergences: list[Divergence] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.divergences

    def describe(self) -> str:
        if self.clean:
            return f"{self.compared} setting(s) compared, no divergence"
        lines = "\n".join(f"  - {d.describe()}" for d in self.divergences)
        return (
            f"{self.compared} setting(s) compared, "
            f"{len(self.divergences)} diverged:\n{lines}"
        )


def _values_agree(kernel_value: object, legacy_value: object) -> bool:
    """Equality, with the one normalisation that is not drift.

    A legacy resolver that returns `Decimal("5")` where the kernel returns `5`
    is not a disagreement about the SETTING, and failing a cutover on it would
    train people to ignore the report. Anything else compares by `==`, and an
    unorderable/uncomparable pair is a divergence rather than an exception.
    """
    if kernel_value is None or legacy_value is None:
        return kernel_value is legacy_value
    if isinstance(kernel_value, bool) != isinstance(legacy_value, bool):
        # `True == 1` in Python. A bool setting read as an int IS drift.
        return False
    try:
        return bool(kernel_value == legacy_value)
    except Exception:
        return False


def compare_one(
    db: Session,
    domain: SettingDomain,
    key: str,
    legacy: LegacyRead,
    *,
    scope: SettingScope | None = None,
) -> Divergence | None:
    """Compare one setting. Returns None when the two agree.

    Never raises: a legacy reader that blows up is reported as a divergence
    with `error` set, because during a shadow phase an adapter bug and a value
    disagreement are both things you want to see and neither is worth a 500.
    """
    target = scope or SettingScope.platform()
    try:
        # `scope=` only: passing tenant_id alongside it is a caller error the
        # resolver rejects outright, since there is no rule for which wins.
        kernel_value, _source = resolve_with_source(db, domain, key, scope=target)
    except Exception as exc:
        return Divergence(
            domain=str(domain),
            key=key,
            scope_kind=target.kind,
            kernel_type="<error>",
            legacy_type="<not read>",
            error=f"kernel resolve raised {type(exc).__name__}",
        )
    try:
        legacy_value = legacy()
    except Exception as exc:
        return Divergence(
            domain=str(domain),
            key=key,
            scope_kind=target.kind,
            kernel_type=type(kernel_value).__name__,
            legacy_type="<error>",
            error=f"legacy read raised {type(exc).__name__}",
        )
    if _values_agree(kernel_value, legacy_value):
        return None
    return Divergence(
        domain=str(domain),
        key=key,
        scope_kind=target.kind,
        kernel_type=type(kernel_value).__name__,
        legacy_type=type(legacy_value).__name__,
    )


def resolve_shadowed(
    db: Session,
    spec: SettingSpec[Any],
    legacy: LegacyRead,
    *,
    phase: ShadowPhase,
    scope: SettingScope | None = None,
) -> object:
    """Resolve `spec`, serving whichever side `phase` says is authoritative.

    This is the call a product puts at its read sites during a migration. The
    returned value is `object` for the same reason `resolve_value` is: during a
    shadow phase it may come from the legacy reader, whose type the kernel
    cannot vouch for.

    A divergence is logged at WARNING and returned to nobody — the point is
    that behaviour does not change until the phase does.
    """
    if phase is ShadowPhase.KERNEL_ONLY:
        value, _ = resolve_with_source(
            db, spec.domain, spec.key, scope=scope or SettingScope.platform()
        )
        return value

    divergence = compare_one(db, spec.domain, spec.key, legacy, scope=scope)
    if divergence is not None:
        logger.warning("Settings shadow divergence: %s", divergence.describe())

    target = scope or SettingScope.platform()
    if phase is ShadowPhase.KERNEL_AUTHORITATIVE:
        value, _ = resolve_with_source(db, spec.domain, spec.key, scope=target)
        return value
    return legacy()


def sweep(
    db: Session,
    legacy_reader: Callable[[SettingDomain, str], object],
    *,
    scope: SettingScope | None = None,
    specs: Sequence[SettingSpec[Any]] | None = None,
) -> ShadowReport:
    """Compare EVERY registered spec at one scope. The cutover gate.

    `legacy_reader` takes `(domain, key)` and returns the product's value —
    the adapter over whatever the product's own resolver looks like.

    A cutover proceeds when this reports `clean` against real data at every
    scope that matters, not when it reports clean once against fixtures. That
    distinction is the whole reason this returns a report rather than a bool.
    """
    report = ShadowReport()
    for spec in specs if specs is not None else all_specs():
        report.compared += 1
        divergence = compare_one(
            db,
            spec.domain,
            spec.key,
            lambda s=spec: legacy_reader(s.domain, s.key),  # type: ignore[misc]
            scope=scope,
        )
        if divergence is not None:
            report.divergences.append(divergence)
    if report.clean:
        logger.info("Settings shadow sweep clean: %d compared", report.compared)
    else:
        logger.warning("Settings shadow sweep: %s", report.describe())
    return report


def sweep_scopes(
    db: Session,
    legacy_reader: Callable[[SettingDomain, str], object],
    scopes: Iterable[SettingScope],
) -> ShadowReport:
    """`sweep` across several scopes, merged into one report.

    Platform-only agreement proves nothing about tenant overrides, which are
    where a precedence difference between two resolvers actually shows up.
    """
    merged = ShadowReport()
    for scope in scopes:
        one = sweep(db, legacy_reader, scope=scope)
        merged.compared += one.compared
        merged.divergences.extend(one.divergences)
    return merged


__all__ = [
    "Divergence",
    "LegacyRead",
    "ShadowPhase",
    "ShadowReport",
    "compare_one",
    "resolve_shadowed",
    "sweep",
    "sweep_scopes",
]
