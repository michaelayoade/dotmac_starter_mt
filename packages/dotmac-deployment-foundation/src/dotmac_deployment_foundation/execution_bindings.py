"""Provider-neutral discovery of an assembly's execution bindings.

## The gap this closes — a4's first inadmissibility finding

The executor's injection points all existed: `_require_grant` read
``args.authorization_verifier``, `Executor` took an evidence policy, an
evidence verifier and a recovery verifier. Not one of them was REACHABLE from
the installed console script — the verifier was an argparse-namespace attribute
that no flag, no entry point and no discovery mechanism ever set, and
`_build_effects` was a closed switch over the one in-package provider. So the
installed `dotmac-deploy` could refuse honestly and could never ADMIT: the
seams were real for an embedder and decorative for the CLI, which is the only
thing an operator actually runs.

## What discovery is, and is not

An assembly ships its bindings as a DISTRIBUTION — reviewed, versioned,
installed by the supply chain into the same environment as this facility — and
declares ONE entry point in the group :data:`ENTRY_POINT_GROUP`. Python
plugins are trusted in-process code (the repository's standing rule): install
and verify them at build/deploy time. Discovery here therefore authenticates
nothing and decides nothing; it LOCATES the one set of bindings the
environment was built to carry, and refuses every ambiguous shape:

* **two declarations refuse, naming both.** A mechanism that makes the ADMIT
  representable also makes an unintended admit representable — a second
  distribution declaring the same group must be a loud stop, never a pick.
* **a declaration that fails to import, or resolves to the wrong type,
  refuses.** Skipping it would turn a broken deployment environment into a
  quieter one.
* **zero declarations is a valid answer**, returned as None: the CLI's
  refusals then stand exactly as before, and they now say what was looked for.

## The entry point's NAME is the provider name

Deliberate, and load-bearing: enumerating provider names for `--provider`'s
choices must not import assembly code on a `validate` or a dry run. A name is
metadata; a load is an import. :func:`declared_provider_names` reads names
only; :func:`discover_bindings` is the single place a declaration is loaded,
and only the execute path calls it.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterable, Sequence
from typing import Any, Final

from .errors import PreconditionFailed, SpecError
from .evidence import SignatureVerifier, TrustPolicy
from .provenance import AuthorizationVerifier

__all__ = [
    "ENTRY_POINT_GROUP",
    "ExecutionBindings",
    "declared_provider_names",
    "discover_bindings",
]

ENTRY_POINT_GROUP: Final = "dotmac_deployment_foundation.execution_bindings"

#: The in-package provider's reserved name. A binding may not claim it: the
#: compose-host provider is selected by this facility's own code, and a
#: distribution shadowing the built-in name would swap effects under an
#: unchanged command line.
RESERVED_PROVIDER_NAMES: Final[frozenset[str]] = frozenset({"compose-host"})


@dataclasses.dataclass(frozen=True, slots=True)
class ExecutionBindings:
    """Everything an assembly injects into the installed CLI, typed.

    ``provider`` names the effects implementation for ``--provider``; the
    callables and verifiers are the assembly's own, carrying its trust roots.
    At least one injectable must be present — an empty bindings object is a
    declaration that discovers nothing and can only mislead.
    """

    #: The provider name `--provider` selects. Must equal the entry point's
    #: name; checked at discovery so the two cannot drift.
    provider: str
    #: ``(spec, deploy_dir) -> Effects``. None when the assembly injects
    #: verifiers only and keeps the in-package provider.
    build_effects: Callable[..., Any] | None = None
    authorization_verifier: AuthorizationVerifier | None = None
    evidence_policy: TrustPolicy | None = None
    evidence_verifier: SignatureVerifier | None = None
    recovery_verifier: SignatureVerifier | None = None

    def __post_init__(self) -> None:
        name = str(self.provider).strip()
        if not name:
            raise SpecError(
                "ExecutionBindings.provider is empty. The provider name is what "
                "`--provider` selects; bindings that cannot be named cannot be "
                "chosen"
            )
        if name in RESERVED_PROVIDER_NAMES:
            raise SpecError(
                f"ExecutionBindings.provider {name!r} is reserved by the "
                "facility's own in-package provider. A distribution shadowing "
                "the built-in name would swap effects under an unchanged "
                "command line"
            )
        if self.build_effects is not None and not callable(self.build_effects):
            raise SpecError(
                "ExecutionBindings.build_effects must be callable "
                f"((spec, deploy_dir) -> Effects), got "
                f"{type(self.build_effects).__name__}"
            )
        if self.authorization_verifier is not None and not isinstance(
            self.authorization_verifier, AuthorizationVerifier
        ):
            raise SpecError(
                "ExecutionBindings.authorization_verifier does not implement "
                "AuthorizationVerifier (an `attest(material)` method); got "
                f"{type(self.authorization_verifier).__name__}"
            )
        if self.evidence_policy is not None and not isinstance(
            self.evidence_policy, TrustPolicy
        ):
            raise SpecError(
                "ExecutionBindings.evidence_policy must be a TrustPolicy, got "
                f"{type(self.evidence_policy).__name__}"
            )
        for field in ("evidence_verifier", "recovery_verifier"):
            value = getattr(self, field)
            if value is not None and not isinstance(value, SignatureVerifier):
                raise SpecError(
                    f"ExecutionBindings.{field} does not implement "
                    "SignatureVerifier (a `verify(key_id=, message=, "
                    f"signature=)` method); got {type(value).__name__}"
                )
        if (
            self.build_effects is None
            and self.authorization_verifier is None
            and self.evidence_policy is None
            and self.evidence_verifier is None
            and self.recovery_verifier is None
        ):
            raise SpecError(
                "ExecutionBindings carries no injectable at all. An empty "
                "declaration cannot help the CLI admit anything and can only "
                "mislead a reader into believing an assembly is wired in"
            )


def _distribution_of(entry: Any) -> str:
    dist = getattr(entry, "dist", None)
    name = getattr(dist, "name", None)
    return str(name) if name else "<unknown distribution>"


def _declared(entries: Iterable[Any] | None) -> Sequence[Any]:
    if entries is not None:
        return list(entries)
    import importlib.metadata

    return list(importlib.metadata.entry_points(group=ENTRY_POINT_GROUP))


def declared_provider_names(*, entries: Iterable[Any] | None = None) -> tuple[str, ...]:
    """Every declared provider name, WITHOUT importing any assembly code.

    Metadata only — safe on `validate` and on a dry run. Duplicates are
    reported by :func:`discover_bindings`; here they simply collapse, because
    a choices list is a menu rather than a gate.
    """
    return tuple(sorted({str(entry.name) for entry in _declared(entries)}))


def discover_bindings(
    *, entries: Iterable[Any] | None = None
) -> ExecutionBindings | None:
    """Locate THE declared bindings, or None, or refuse the ambiguous shapes.

    The single place a declaration is LOADED. Zero declarations is a valid
    environment (the CLI's refusals stand, and name this group); one is loaded
    and validated; two or more refuse naming every declarer, because which one
    wins must never be an iteration-order accident.
    """
    declared = _declared(entries)
    if not declared:
        return None
    if len(declared) > 1:
        listed = ", ".join(
            sorted(f"{_distribution_of(entry)}:{entry.name}" for entry in declared)
        )
        raise PreconditionFailed(
            f"{len(declared)} distributions declare "
            f"{ENTRY_POINT_GROUP!r} entry points ({listed}). One environment "
            "carries one set of execution bindings; a second declaration is "
            "either a stale install or an attempt to swap effects under an "
            "unchanged command line, and neither is a thing to pick a winner "
            "from. Remove all but one and redeploy"
        )

    entry = declared[0]
    try:
        factory = entry.load()
    except Exception as exc:
        raise PreconditionFailed(
            f"the execution bindings declared by {_distribution_of(entry)} "
            f"({ENTRY_POINT_GROUP}:{entry.name}) failed to import: {exc}. A "
            "broken bindings distribution is a broken deployment environment, "
            "not a thing to skip"
        ) from exc
    try:
        bindings = factory()
    except Exception as exc:
        raise PreconditionFailed(
            f"the execution bindings factory from {_distribution_of(entry)} "
            f"({ENTRY_POINT_GROUP}:{entry.name}) raised: {exc}"
        ) from exc
    if not isinstance(bindings, ExecutionBindings):
        raise PreconditionFailed(
            f"the entry point {ENTRY_POINT_GROUP}:{entry.name} from "
            f"{_distribution_of(entry)} returned "
            f"{type(bindings).__name__}, not ExecutionBindings. The typed "
            "object is the contract; a look-alike is exactly what the type "
            "exists to refuse"
        )
    if str(bindings.provider) != str(entry.name):
        raise PreconditionFailed(
            f"the entry point is named {str(entry.name)!r} but its bindings "
            f"declare provider {str(bindings.provider)!r}. The entry point "
            "name is what `--provider` offered before anything was imported; "
            "bindings answering to a different name were selected by nobody"
        )
    return bindings
