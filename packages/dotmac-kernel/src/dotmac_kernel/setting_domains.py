"""Setting-domain registry.

A *setting domain* (e.g. ``"branding"``) is a module's declaration, on its
manifest's ``setting_domains``, that a named group of settings EXISTS and that
this module owns it. This module builds the registry of those declarations and
is the ONE place that answers "is this domain real?".

Same shape and same fail-closed posture as `dotmac_kernel.audit_actions
.AuditActionRegistry`: a bare code, one owning module, validated at the
boundary that writes it.

**Why this is validated at all.** `domain_settings.domain` is a plain string
column, because a kernel cannot enumerate its consumers' domains — this repo
declares five, `dotmac_erp` twenty-one. Without a registry any typo
(`"brandng"`) would silently create a parallel domain whose settings no reader
ever resolves, so the value reverts to its default and the misconfiguration is
invisible. `dotmac_kernel.settings_resolver` calls
`active_setting_domains().require(domain)` before it registers a spec or writes
a row.

Process-active registry, installed by `create_app` — see
`dotmac_kernel.permissions`' module docstring for why a process-wide install
rather than a threaded-through argument.

**Not installed is not the same as installed-and-empty.** `active_setting_domains()`
raises `SettingDomainsNotInstalledError` until something installs one, for the
same reason `active_audit_actions()` does: rejecting every write inside the
caller's transaction would turn a wiring mistake into a failed business
operation, and would send the reader looking for a missing declaration rather
than a missing install.

Import-safe: pure data over the manifests; no engine, no I/O.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from dotmac_kernel.settings_models import SettingDomain

if TYPE_CHECKING:  # avoids a runtime cycle: `features` imports this module
    from dotmac_kernel.modules import AnyManifest


class DuplicateSettingDomainError(ValueError):
    """Two modules declared the same setting domain — there is no single owner."""


class UndeclaredSettingDomainError(KeyError):
    """A setting domain was used that no installed module declares."""


class SettingDomainRegistry:
    """The immutable set of setting domains declared across a set of modules.

    Build it once from the installed manifests (`from_manifests`); then ask it
    whether a domain is real (`is_declared`/`require`) and who owns it
    (`owner`). Construction fails closed on a duplicate declaration.
    """

    __slots__ = ("_owner_by_domain",)

    def __init__(self, declarations: Iterable[tuple[str, str]]) -> None:
        """`declarations` is (owning module code, domain) pairs. Construction IS
        validation: a domain declared by two different modules raises here."""
        owner_by_domain: dict[str, str] = {}
        for owner, domain in declarations:
            existing = owner_by_domain.get(domain)
            if existing is not None and existing != owner:
                raise DuplicateSettingDomainError(
                    f"setting domain {domain!r} declared by both {existing!r} "
                    f"and {owner!r} — a setting domain has one owning module"
                )
            owner_by_domain[str(domain)] = owner
        self._owner_by_domain = owner_by_domain

    @classmethod
    def from_manifests(cls, manifests: Iterable[AnyManifest]) -> SettingDomainRegistry:
        return cls(
            (manifest.name, str(domain))
            for manifest in manifests
            for domain in manifest.setting_domains
        )

    def is_declared(self, domain: str) -> bool:
        """True iff some installed module declares `domain`."""
        return str(domain) in self._owner_by_domain

    def require(self, domain: str) -> SettingDomain:
        """Return `domain` as a `SettingDomain`, or raise if undeclared."""
        if str(domain) not in self._owner_by_domain:
            raise UndeclaredSettingDomainError(
                f"setting domain {str(domain)!r} is not declared by any "
                "installed module — declare it on the owning module's manifest "
                "(`setting_domains=(...)`) rather than inventing it at the "
                "write site"
            )
        return SettingDomain(domain)

    def owner(self, domain: str) -> str | None:
        """The module that declares `domain`, or None if undeclared."""
        return self._owner_by_domain.get(str(domain))

    def domains(self) -> frozenset[SettingDomain]:
        """Every declared setting domain."""
        return frozenset(SettingDomain(d) for d in self._owner_by_domain)


_active_registry: SettingDomainRegistry | None = None


class SettingDomainsNotInstalledError(RuntimeError):
    """No setting-domain registry was installed in this process."""


def install_setting_domains(registry: SettingDomainRegistry) -> None:
    """Install the process-active setting-domain registry.

    Called by `create_app` with the registry built from the INSTALLED module set
    (not the enabled subset — a disabled module's domains stay declared, so a
    platform-default row it owns is still readable and its settings screen still
    explains itself). A consumer that builds an app by hand must call this
    itself, exactly as it must call `install_audit_actions`.

    Installing an EMPTY registry is legitimate and deliberately distinct from
    not installing one: it means "this deployment declares no setting domains",
    and every spec registration is then correctly rejected as undeclared.
    """
    global _active_registry
    _active_registry = registry


def active_setting_domains() -> SettingDomainRegistry:
    """The process-active registry.

    Raises `SettingDomainsNotInstalledError` when none was installed — see the
    module docstring for why that is not the same as an empty one.
    """
    if _active_registry is None:
        raise SettingDomainsNotInstalledError(
            "no setting-domain registry is installed in this process, so no "
            "domain can be validated. `create_app` installs one; a worker, "
            "task, CLI or test that builds no app must call "
            "`install_setting_domains(SettingDomainRegistry.from_manifests(...))` "
            "itself. This is a wiring problem, not an undeclared domain."
        )
    return _active_registry


__all__ = [
    "DuplicateSettingDomainError",
    "SettingDomainRegistry",
    "SettingDomainsNotInstalledError",
    "UndeclaredSettingDomainError",
    "active_setting_domains",
    "install_setting_domains",
]
