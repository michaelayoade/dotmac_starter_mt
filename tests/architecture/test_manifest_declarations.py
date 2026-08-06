"""Manifest declarations must be unique, referenced, and consumed.

The module control-plane directive's governance list requires CI to fail when
"a permission, flag, setting, audit action, or entity code is undeclared" and
when "a declaration has no consumer". This file covers the two declaration
kinds that exist today — `ModuleManifest`/`FeatureManifest.permissions` and
`.audit_actions` — from both ends:

- **Undeclared reference fails.** A route requiring a permission code no module
  declares fails the BOOT (`create_app`); an audit event written with an action
  no module declares fails the WRITE (`write_audit_event`).
- **Unconsumed declaration fails.** A declared code that nothing anywhere reads
  is a dead control, exactly like an orphan `SettingSpec` — the scan below is
  `test_no_orphan_settings.py`'s, applied to the two new catalogues. Read that
  file first; this one deliberately mirrors its shape, its exclusion reasoning,
  and its shrink-only allowlist rule.
- **Two owners fails.** A code declared by two modules has no single owner, so
  both catalogues refuse to build.

The allowlists start EMPTY and may only shrink: a newly-declared code with no
consumer must fail the build immediately rather than accumulate here.
"""

from __future__ import annotations

import pathlib
from uuid import uuid4

import pytest
from dotmac_kernel import (
    AuditActionRegistry,
    DuplicateAuditActionError,
    DuplicatePermissionError,
    FeatureManifest,
    PermissionCatalogue,
    PermissionSpec,
    ProductAssemblySpec,
    UndeclaredAuditActionError,
    UndeclaredPermissionError,
    create_app,
    install_audit_actions,
    install_permissions,
    load_manifests,
    write_audit_event,
)
from dotmac_kernel.deps import require_permission, require_tenant
from dotmac_kernel.models import Party
from dotmac_kernel.templating import install_surface_globals
from dotmac_kernel.testing import create_test_engine, isolated_session
from fastapi import APIRouter, Depends

from app.features import FEATURE_MODULES

# The declaration sites themselves are not "consumers" — excluding them is what
# makes the orphan scan meaningful at all, since every code trivially appears in
# the feature.py that declares it (same reasoning as `test_no_orphan_settings`'s
# `_EXCLUDED_DIR_PREFIX`).
_EXCLUDED_SUFFIX = "/feature.py"
# The two catalogue modules implement the MECHANISM and name real codes in their
# docstrings as examples; that is prose, not consumption (mirrors that test's
# `_EXCLUDED_FILE` for `settings_resolver.py`).
_EXCLUDED_FILES = {
    "dotmac_kernel/permissions.py",
    "dotmac_kernel/audit_actions.py",
}

# Burn-down allowlists — do NOT add without a task/plan reference in the
# comment; a new orphan should get a real consumer instead. Both EMPTY as of the
# step-3 (F2) change that introduced them.
_ALLOWED_ORPHAN_PERMISSIONS: set[str] = set()
_ALLOWED_ORPHAN_AUDIT_ACTIONS: set[str] = set()


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


def _consumer_corpus() -> str:
    """Every `.py` under the assembly and the kernel package, minus the
    declaration sites and the catalogue mechanics."""
    root = _repo_root()
    trees = (
        (root / "app", "app"),
        (root / "packages/dotmac-kernel/src/dotmac_kernel", "dotmac_kernel"),
    )
    chunks: list[str] = []
    for scan_dir, prefix in trees:
        for path in scan_dir.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            rel = f"{prefix}/" + str(path.relative_to(scan_dir)).replace("\\", "/")
            if rel.endswith(_EXCLUDED_SUFFIX) or rel in _EXCLUDED_FILES:
                continue
            try:
                chunks.append(path.read_text(encoding="utf-8"))
            except OSError:
                continue
    return "\n".join(chunks)


def _orphans(codes: set[str]) -> set[str]:
    corpus = _consumer_corpus()
    return {c for c in codes if f'"{c}"' not in corpus and f"'{c}'" not in corpus}


def _assembly_manifests() -> list[FeatureManifest]:
    return load_manifests(FEATURE_MODULES)


def _restore_process_declarations() -> None:
    """`create_app` installs the PROCESS-active catalogues and surface globals.
    A test that boots a synthetic assembly must put the real ones back, or it
    leaks a truncated catalogue into whatever runs next."""
    manifests = _assembly_manifests()
    install_permissions(PermissionCatalogue.from_manifests(manifests))
    install_audit_actions(AuditActionRegistry.from_manifests(manifests))
    install_surface_globals(manifests, disabled=set(), web_enabled=True)


# ── The real assembly is coherent (positive control) ────────────────────────


def test_assembly_declarations_build_and_are_owned() -> None:
    manifests = _assembly_manifests()
    catalogue = PermissionCatalogue.from_manifests(manifests)
    registry = AuditActionRegistry.from_manifests(manifests)
    assert catalogue.codes(), "the assembly declares no permissions at all"
    assert registry.actions(), "the assembly declares no audit actions at all"
    for code in catalogue.codes():
        assert catalogue.owner(code), code
    for action in registry.actions():
        assert registry.owner(action), action


# ── Two owners is a conflict, not a merge ───────────────────────────────────


def test_two_modules_declaring_the_same_permission_code_fails() -> None:
    with pytest.raises(DuplicatePermissionError):
        PermissionCatalogue.from_manifests(
            [
                FeatureManifest(name="a", permissions=[PermissionSpec(code="x.read")]),
                FeatureManifest(name="b", permissions=[PermissionSpec(code="x.read")]),
            ]
        )


def test_two_modules_declaring_the_same_audit_action_fails() -> None:
    with pytest.raises(DuplicateAuditActionError):
        AuditActionRegistry.from_manifests(
            [
                FeatureManifest(name="a", audit_actions=["x.happened"]),
                FeatureManifest(name="b", audit_actions=["x.happened"]),
            ]
        )


# ── An undeclared REFERENCE fails ───────────────────────────────────────────


def test_a_route_requiring_an_undeclared_permission_fails_the_boot() -> None:
    """The whole point of boot-time validation: a code nothing declares must
    stop `create_app`, not wait for the first request to that route."""
    router = APIRouter(prefix="/ghost", dependencies=[Depends(require_tenant)])

    @router.get("/thing")
    def _thing(_: Party = Depends(require_permission("ghost.read"))) -> dict:
        return {}

    module = FeatureManifest(name="ghost", routers=[router])
    try:
        with pytest.raises(UndeclaredPermissionError) as exc:
            create_app(ProductAssemblySpec(name="ghost-assembly", modules=[module]))
        assert "ghost.read" in str(exc.value)
    finally:
        _restore_process_declarations()


def test_the_same_route_boots_once_its_module_declares_the_code() -> None:
    """Companion to the test above — proves the failure is about the DECLARATION
    and not about the route shape."""
    router = APIRouter(prefix="/ghost", dependencies=[Depends(require_tenant)])

    @router.get("/thing")
    def _thing(_: Party = Depends(require_permission("ghost.read"))) -> dict:
        return {}

    module = FeatureManifest(
        name="ghost",
        routers=[router],
        permissions=[PermissionSpec(code="ghost.read")],
    )
    try:
        create_app(ProductAssemblySpec(name="ghost-assembly", modules=[module]))
    finally:
        _restore_process_declarations()


def test_an_audit_event_with_an_undeclared_action_fails() -> None:
    install_audit_actions(
        AuditActionRegistry.from_manifests(
            [FeatureManifest(name="probe", audit_actions=["probe.declared"])]
        )
    )
    try:
        engine = create_test_engine()
        with isolated_session(engine) as db:
            with pytest.raises(UndeclaredAuditActionError) as exc:
                write_audit_event(
                    db,
                    tenant_id=uuid4(),
                    actor_party_id=None,
                    action="probe.undeclared",
                    entity_type="probe",
                )
            assert "probe.undeclared" in str(exc.value)
        engine.dispose()
    finally:
        _restore_process_declarations()


# ── An unconsumed DECLARATION fails ─────────────────────────────────────────


def test_no_orphan_permission_declarations() -> None:
    codes = {
        spec.code for m in _assembly_manifests() for spec in m.permissions
    } - _ALLOWED_ORPHAN_PERMISSIONS
    orphans = _orphans(codes)
    assert not orphans, (
        "Declared permission code(s) with no consumer (dead declaration): "
        f"{sorted(orphans)}. Reference each from a real guard "
        '(`Depends(require_permission("<code>"))`) outside the declaring '
        "feature.py, or drop it from the manifest. Do not add to "
        "_ALLOWED_ORPHAN_PERMISSIONS."
    )


def test_no_orphan_audit_action_declarations() -> None:
    actions = {
        a for m in _assembly_manifests() for a in m.audit_actions
    } - _ALLOWED_ORPHAN_AUDIT_ACTIONS
    orphans = _orphans(actions)
    assert not orphans, (
        "Declared audit action(s) with no consumer (dead declaration): "
        f"{sorted(orphans)}. Write each one from a real "
        '`write_audit_event(..., action="<action>")` call outside the '
        "declaring feature.py, or drop it from the manifest. Do not add to "
        "_ALLOWED_ORPHAN_AUDIT_ACTIONS."
    )


def test_orphan_scan_detects_a_planted_orphan() -> None:
    """Sensitivity check for the two scans above — a code nothing mentions must
    be reported (mirrors `test_contract_comparison_detects_drift`)."""
    assert _orphans({"nobody.reads.this.code"}) == {"nobody.reads.this.code"}
    assert _orphans({"role.grant"}) == set()  # a genuinely consumed action


def test_orphan_allowlists_are_accurate() -> None:
    # Shrink-only companions: once an allowlisted code is wired (or removed from
    # a manifest), it must be deleted from the allowlist so the list trends
    # toward empty instead of accumulating stale exceptions.
    manifests = _assembly_manifests()
    permission_codes = {spec.code for m in manifests for spec in m.permissions}
    action_codes = {a for m in manifests for a in m.audit_actions}
    stale_permissions = _ALLOWED_ORPHAN_PERMISSIONS - _orphans(permission_codes)
    stale_actions = _ALLOWED_ORPHAN_AUDIT_ACTIONS - _orphans(action_codes)
    assert not stale_permissions, sorted(stale_permissions)
    assert not stale_actions, sorted(stale_actions)
