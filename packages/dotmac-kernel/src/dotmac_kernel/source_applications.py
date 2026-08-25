"""Which APPLICATION issued this — the attribution vocabulary.

A *source application* names the fleet peer that issued a command, presented a
machine credential, or caused an audit row: `"dotmac_sub"`, `"dotmac_erp"`,
`"dotmac_starter_mt"`. It answers a question neither the actor pair nor the
scope set can: `actor_type="api_key"` with a key id says *a machine*, and the
scope says *what it was allowed to do*, but neither says *whose machine*.

Open registered strings, never an enum (ADR-0008, hard rule 12). A deployment
names the peers it accepts without a kernel change, and the registry is what
makes "this deployment does not talk to that application" an answerable question
instead of a free-text field nobody validates.

## Why this registry is INSTALLED, not derived from manifests

Every other declaration registry here (`audit_actions`, `capabilities`,
`permissions`, `setting_domains`) is built from installed module manifests,
because those vocabularies are things a module OWNS. A source application is
not: it names a peer application, which is a fact about the DEPLOYMENT's
topology, not about any module composed into it. Deriving it from manifests
would mean a module declaring the existence of its callers, which inverts the
dependency — and it would make "who may call us" un-configurable, in a
repository whose standing rule is that every environment-specific value is an
overridable knob.

So it is installed the way `install_secret_source` and `install_key_provider`
are installed: once, at startup, by the product, from configuration.

## Two separate facts, and conflating them is the trap

**The registry** (`install_source_applications`) — every application this
deployment ACCEPTS attribution from. A gate.

**The host identity** (`install_host_application`) — which application this
process IS. Exactly one value, and it is the attribution for anything this
process originates itself: an operator clicking a button in this app's own
admin portal is issued BY this app, and recording that is the truth. It is not
a fallback for a missing attribution and never stands in for a caller's — a
request that arrived over the wire carries the caller's identity or is refused.

## Not installed is not installed-and-empty

Same asymmetry, and the same reason, as
`dotmac_kernel.audit_actions.active_audit_actions`: a process that installed
nothing has a WIRING problem, and telling it "that application is not declared"
sends the reader to the wrong file. The two states stay separately answerable.

Import-safe: pure data, no engine, no I/O.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Final

#: What a source-application code may look like. Lowercase, snake, bounded —
#: the same shape the fleet already uses for repository/distribution names, so
#: an operator writes `dotmac_sub` and not `Dotmac Sub (prod)`.
#:
#: Bounded at 64 to match the storage columns exactly. A code that fits the
#: pattern but not the column would be accepted here and truncated there, and a
#: truncated attribution is a WRONG attribution rather than a missing one.
_CODE_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

#: The storage width every attribution column uses. Named once so the pattern
#: above, the ORM columns and the migration cannot drift apart.
SOURCE_APPLICATION_MAX_LENGTH: Final = 64


class InvalidSourceApplicationError(ValueError):
    """A code that is not a well-formed source-application name."""


class UndeclaredSourceApplicationError(KeyError):
    """A well-formed code that this deployment does not accept attribution from."""


class SourceApplicationsNotInstalledError(RuntimeError):
    """No registry was installed in this process.

    A CONFIGURATION error, not a declaration error: the vocabulary was never
    loaded, so nothing can be validated against it.
    """


class HostApplicationNotInstalledError(RuntimeError):
    """This process never said which application it is.

    Deliberately fatal rather than resolving to `"unknown"`, `"system"` or the
    empty string. Every one of those is an ANONYMOUS principal wearing a name,
    and an audit trail whose attribution column is mostly `"system"` has the
    same value as one with no column at all.
    """


def validate_source_application(code: str) -> str:
    """Return `code` if it is a well-formed source-application name, else raise.

    Format only — say NOTHING about whether the deployment accepts it. The two
    checks are separate on purpose: a malformed code is a caller bug, an
    undeclared one is a deployment fact, and the fixes live in different files.
    """
    if not isinstance(code, str) or not _CODE_PATTERN.match(code):
        raise InvalidSourceApplicationError(
            f"source application {code!r} is not a well-formed code: lowercase "
            "letters, digits and underscores, starting with a letter, 2-64 "
            f"characters (e.g. 'dotmac_sub'). Max length is "
            f"{SOURCE_APPLICATION_MAX_LENGTH} because that is the storage width; "
            "a code that fit here and truncated there would be a WRONG "
            "attribution, not a missing one."
        )
    return code


class SourceApplicationRegistry:
    """The set of source applications this deployment accepts attribution from.

    Construction IS validation: every code is checked for shape, so a typo in a
    deployment's configuration fails at boot rather than at the first refused
    request. Membership is EXACT — see `require`.
    """

    __slots__ = ("_codes",)

    def __init__(self, codes: Iterable[str]) -> None:
        self._codes = frozenset(validate_source_application(code) for code in codes)

    def is_declared(self, code: str) -> bool:
        """True iff `code` is EXACTLY one of the declared codes.

        Set membership, deliberately. Not `startswith`, not `in`, not a
        `fnmatch` — see `require` for what each of those would have let through.
        """
        return code in self._codes

    def require(self, code: str) -> None:
        """Raise unless `code` is exactly declared.

        The exactness is the whole control. A prefix test would let
        `dotmac_sub_staging` authenticate as `dotmac_sub`; a substring test
        would let `not_dotmac_sub` do it; a glob would let one wildcard entry
        quietly re-open everything the registry exists to close. There is no
        wildcard code and no "all" code, for the same reason there is no
        wildcard scope.
        """
        validate_source_application(code)
        if code not in self._codes:
            raise UndeclaredSourceApplicationError(
                f"source application {code!r} is not declared by this "
                "deployment. Declare it in the deployment's accepted-peer "
                "configuration; there is deliberately no wildcard entry."
            )

    def codes(self) -> frozenset[str]:
        """Every declared code."""
        return self._codes


# See the module docstring: NOT INSTALLED is a distinct, separately answerable
# state from INSTALLED-AND-EMPTY. An empty registry is a legitimate deployment
# saying "no peer may attribute anything to itself here", and every cross-app
# command is then correctly refused.
_active_registry: SourceApplicationRegistry | None = None
_host_application: str | None = None


def install_source_applications(registry: SourceApplicationRegistry) -> None:
    """Install the process-active registry of accepted source applications.

    Called by `create_app`. A worker, task, CLI or test that builds no app must
    call this itself, exactly as it must call `install_audit_actions`.
    """
    global _active_registry
    _active_registry = registry


def active_source_applications() -> SourceApplicationRegistry:
    """The process-active registry, or raise if none was installed."""
    if _active_registry is None:
        raise SourceApplicationsNotInstalledError(
            "no source-application registry is installed in this process, so "
            "no attribution can be validated. `create_app` installs one; a "
            "worker, task, CLI or test that builds no app must call "
            "`install_source_applications(SourceApplicationRegistry(...))` "
            "itself. This is a wiring problem, not an undeclared application."
        )
    return _active_registry


def install_host_application(code: str) -> None:
    """Declare which application THIS process is.

    Validated for shape here and checked against the installed registry when
    there is one — a host that is not in its own accepted set is a
    configuration mistake worth catching at boot, since every locally
    originated audit row would otherwise carry an attribution the deployment
    says it does not accept.
    """
    global _host_application
    validate_source_application(code)
    if _active_registry is not None:
        _active_registry.require(code)
    _host_application = code


def clear_host_application() -> None:
    """Forget the host identity.

    Exists for the tests that PROVE an unattributed write is refused: a guard
    that can only be observed passing is not observed at all. Not a deployment
    operation — nothing in `create_app` calls it.
    """
    global _host_application
    _host_application = None


def active_host_application() -> str:
    """Which application this process is, or raise.

    Raises rather than returning None so a caller cannot accidentally treat
    "we never said" as a value. See `HostApplicationNotInstalledError`.
    """
    if _host_application is None:
        raise HostApplicationNotInstalledError(
            "this process never declared which application it is, so nothing "
            "it originates can be attributed. Call "
            "`install_host_application(<code>)` at startup — `create_app` does "
            "it from configuration. There is deliberately no default: "
            "'system' and 'unknown' are anonymous principals with a name on."
        )
    return _host_application


def host_application_or_none() -> str | None:
    """The host identity if one was installed, else None — no raise.

    For the ONE caller that legitimately distinguishes the two states before
    deciding how to fail: `write_audit_event`, which reports a missing host
    identity as an unattributed-event error naming the audit call site, rather
    than as a bare wiring error that sends the reader hunting through startup.
    """
    return _host_application


__all__ = [
    "SOURCE_APPLICATION_MAX_LENGTH",
    "HostApplicationNotInstalledError",
    "InvalidSourceApplicationError",
    "SourceApplicationRegistry",
    "SourceApplicationsNotInstalledError",
    "UndeclaredSourceApplicationError",
    "active_host_application",
    "active_source_applications",
    "clear_host_application",
    "host_application_or_none",
    "install_host_application",
    "install_source_applications",
    "validate_source_application",
]
