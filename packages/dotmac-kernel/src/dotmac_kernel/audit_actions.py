"""Audit-action registry (module control-plane directive, step 3).

An *audit action* (e.g. ``"role.grant"``) is a module's declaration, on its
manifest's ``audit_actions``, that a named auditable event EXISTS and that this
module owns it. This module builds the registry of those declarations and is the
ONE place that answers "is this action real?".

Same shape and same fail-closed posture as
``dotmac_kernel.capabilities.CapabilityCatalogue`` and
``dotmac_kernel.permissions.PermissionCatalogue``. An action is a bare code, not
a spec: unlike a permission there is no binding to carry — the audit trail
records what happened, it decides nothing.

**Why this is validated at all.** `audit_events.action` is a free-text column,
so before this registry any typo (`"role.grant"` vs `"role.granted"`) silently
produced a second, near-identical action nobody would ever query for — and an
audit trail that is missing events you believe you are reading is worse than one
that is obviously empty. `dotmac_kernel.audit.write_audit_event` now calls
`active_audit_actions().require(action)` before it writes, so an undeclared
action fails loudly at the write instead of quietly polluting the trail.

Process-active registry, installed by `create_app` — see
`dotmac_kernel.permissions`' module docstring for why a process-wide install
rather than a threaded-through argument.

**Not installed is not the same as installed-and-empty.** Unlike the permission
catalogue, this registry has no empty default: `active_audit_actions()` raises
`AuditActionsNotInstalledError` until something installs one. The asymmetry is
deliberate and is about what each default DOES — an uninstalled permission
catalogue denies, which is the safe answer for an authorization check, whereas
an uninstalled audit registry would reject every write inside the caller's
transaction and turn a wiring mistake into a failed business operation. See the
comment above the sentinel below.

Import-safe: pure data over the manifests; no engine, no I/O.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoids a runtime cycle: `features` imports this module
    from dotmac_kernel.modules import AnyManifest


class DuplicateAuditActionError(ValueError):
    """Two modules declared the same audit action — there is no single owner."""


class UndeclaredAuditActionError(KeyError):
    """An audit action was written that no installed module declares."""


class AuditActionRegistry:
    """The immutable set of audit actions declared across a set of modules.

    Build it once from the installed manifests (`from_manifests`); then ask it
    whether an action being written is real (`is_declared`/`require`) and who
    owns it (`owner`). Construction fails closed on a duplicate declaration.
    """

    __slots__ = ("_owner_by_action",)

    def __init__(self, declarations: Iterable[tuple[str, str]]) -> None:
        """`declarations` is (owning module code, action) pairs. Construction IS
        validation: an action declared by two different modules raises here."""
        owner_by_action: dict[str, str] = {}
        for owner, action in declarations:
            existing = owner_by_action.get(action)
            if existing is not None and existing != owner:
                raise DuplicateAuditActionError(
                    f"audit action {action!r} declared by both {existing!r} and "
                    f"{owner!r} — an audit action has one owning module"
                )
            owner_by_action[action] = owner
        self._owner_by_action = owner_by_action

    @classmethod
    def from_manifests(cls, manifests: Iterable[AnyManifest]) -> AuditActionRegistry:
        return cls(
            (manifest.name, action)
            for manifest in manifests
            for action in manifest.audit_actions
        )

    def is_declared(self, action: str) -> bool:
        """True iff some installed module declares `action`."""
        return action in self._owner_by_action

    def require(self, action: str) -> None:
        """Raise `UndeclaredAuditActionError` unless `action` is declared."""
        if action not in self._owner_by_action:
            raise UndeclaredAuditActionError(
                f"audit action {action!r} is not declared by any installed "
                "module — declare it on the writing module's manifest "
                "(`audit_actions=(...)`) rather than inventing it at the "
                "write site"
            )

    def owner(self, action: str) -> str | None:
        """The module that declares `action`, or None if undeclared."""
        return self._owner_by_action.get(action)

    def actions(self) -> frozenset[str]:
        """Every declared audit action."""
        return frozenset(self._owner_by_action)


# NOT INSTALLED is a distinct state from INSTALLED-AND-EMPTY, and conflating the
# two is a real trap. A process that never installs a registry — a worker, a
# Celery task, a CLI, a migration helper, anything that does not build an app —
# would otherwise be told that its perfectly well-declared action "is not
# declared by any installed module", sending the reader to the wrong file
# entirely. The sentinel below keeps the two answerable separately.
_active_registry: AuditActionRegistry | None = None


class AuditActionsNotInstalledError(RuntimeError):
    """No audit-action registry was installed in this process.

    A CONFIGURATION error, not a declaration error: the vocabulary was never
    loaded, so nothing can be validated against it.
    """


def install_audit_actions(registry: AuditActionRegistry) -> None:
    """Install the process-active audit-action registry.

    Called by `create_app` with the registry built from the INSTALLED module set
    (not the enabled subset — a disabled module's actions stay declared, so a
    still-running writer never trips on a deployment toggle). A consumer that
    builds an app by hand must call this itself, exactly as it must call
    `install_surface_globals` / `install_permissions`.

    Installing an EMPTY registry is legitimate and deliberately distinct from
    not installing one: it means "this deployment declares no audit actions",
    and every write is then correctly rejected as undeclared.
    """
    global _active_registry
    _active_registry = registry


def active_audit_actions() -> AuditActionRegistry:
    """The process-active registry.

    Raises `AuditActionsNotInstalledError` when none was installed — see the
    comment above the sentinel for why that is not the same as an empty one.
    """
    if _active_registry is None:
        raise AuditActionsNotInstalledError(
            "no audit-action registry is installed in this process, so no "
            "action can be validated. `create_app` installs one; a worker, "
            "task, CLI or test that builds no app must call "
            "`install_audit_actions(AuditActionRegistry.from_manifests(...))` "
            "itself. This is a wiring problem, not an undeclared action."
        )
    return _active_registry


__all__ = [
    "AuditActionRegistry",
    "AuditActionsNotInstalledError",
    "DuplicateAuditActionError",
    "UndeclaredAuditActionError",
    "install_audit_actions",
    "active_audit_actions",
]
