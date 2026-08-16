"""`dotmac-integration`'s declaration must match what its code actually calls.

The defect this file exists to make unrepeatable: `run_effect_once` delegates
at-most-once execution to `dotmac_kernel.idempotency.execute_once_platform`
(hard rule 21, ADR-0014), which writes `public.platform_idempotency_records` at
REQUEST time — and no migration in this module creates it. Through `0.1.0a1`,
`0.1.0a2` and `0.1.0a3`, all PUBLISHED, that dependency was declared nowhere.
An adopter
running its own lineage passed every gate here, migrated cleanly, and would
raise `UndefinedTable` on the first guarded delivery.

The declaration is asserted FROM THE CALL, not from a second copy of itself. A
test that read `module.requires` twice would pass with the requirement deleted
and the call still there, which is the state being forbidden.

Structural only. Behaviour of the at-most-once path belongs to the kernel that
owns it; the PostgreSQL proof that the prerequisite is real, and refuses a
half-supplied ledger, is in `tests/test_integration_isolation.py`.
"""

from __future__ import annotations

import ast
import json
import tomllib
from pathlib import Path

import pytest
from dotmac_integration.manifest import module
from dotmac_kernel.planes import (
    ModulePlane,
    ModulePlaneSelection,
    ModulePlaneSelectionError,
    declared_planes,
    supported_plane_sets,
    validate_module_plane_selections,
)
from dotmac_kernel.prerequisites import IDEMPOTENCY_LEDGER_V1, PLATFORM_AUDIT_LOG_V1

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "packages/dotmac-integration"
SOURCE = PACKAGE_ROOT / "src/dotmac_integration"
MIGRATIONS = (
    SOURCE / "migrations/versions/ig_0007_idempotency_ledger.py",
    SOURCE / "migrations/versions/ig_0008_platform_audit_log.py",
)


# ── The declaration is derived from the call ────────────────────────────────


def test_the_module_still_delegates_at_most_once_to_the_kernel() -> None:
    """The premise every assertion below rests on.

    If `run_effect_once` ever stops calling the kernel — because the module grew
    its own ledger, which ADR-0014 forbids, or because the delegation moved —
    the prerequisite reasoning here becomes a statement about code that is gone.
    Fail loudly at that moment rather than keep asserting it.
    """
    source = (SOURCE / "idempotency.py").read_text(encoding="utf-8")
    assert "from dotmac_kernel.idempotency import execute_once_platform" in source
    assert "return execute_once_platform(" in source


def test_the_ledger_call_is_platform_only() -> None:
    """`execute_once` (the tenant entry point) must NOT appear.

    This is what makes the COMMON-versus-plane-specific reasoning below a real
    decision rather than a copy of numbering's. Numbering calls both halves of
    the pair; this module owns no tenant plane and calls only the platform one.
    """
    calls = [
        line
        for path in sorted(SOURCE.rglob("*.py"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if "execute_once(" in line
    ]
    assert not calls, (
        "a tenant-scoped at-most-once call appeared in a platform-only module: "
        f"{calls}"
    )


def test_the_kernel_facility_the_module_calls_is_a_declared_prerequisite() -> None:
    """Asserted from the CALL. The two tests above establish that the call is
    real and which half it uses; this one requires the manifest to say so."""
    assert IDEMPOTENCY_LEDGER_V1.name in module.requires, (
        "`run_effect_once` writes the kernel at-most-once ledger but the "
        f"manifest does not declare {IDEMPOTENCY_LEDGER_V1.name!r} — an adopter "
        "without those tables would migrate cleanly and fail at the first "
        "guarded effect"
    )


def test_the_platform_audit_writer_is_a_declared_prerequisite() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in SOURCE.rglob("*.py")
    )
    assert "write_platform_audit_event(" in source
    assert PLATFORM_AUDIT_LOG_V1.name in module.requires


# ── COMMON, and why ─────────────────────────────────────────────────────────


def test_the_ledger_prerequisite_is_common_not_plane_specific() -> None:
    assert IDEMPOTENCY_LEDGER_V1.name not in module.tenant_requires
    assert IDEMPOTENCY_LEDGER_V1.name not in module.platform_requires


def test_this_module_owns_exactly_one_plane_installed_atomically() -> None:
    """The semantic half of the reason.

    A plane-specific list names what a plane needs WHEN THAT PLANE IS SELECTED.
    This module declares one plane and no `supported_plane_sets`, so the plane
    is installed atomically and there is no selection under which the ledger
    stops being needed. Conditioning on it would describe a variable that
    cannot vary.
    """
    assert module.tables == ()
    assert declared_planes(module) == (ModulePlane.PLATFORM,)
    assert module.supported_plane_sets == ()
    assert supported_plane_sets(module) == ((ModulePlane.PLATFORM,),)


def test_an_atomic_module_cannot_carry_a_plane_specific_prerequisite() -> None:
    """The mechanical half, and the reason this is not merely a style choice.

    `resolve_depends_on` reads a plane list only via `module=`, `module=` reads
    `selected_module_planes`, and an atomic module may never have a selection
    installed — so `platform_requires` here would be a declaration nothing could
    ever resolve. Proved against the kernel's own refusal rather than asserted
    in prose, and by its SPECIFIC message: a selection rejected for some other
    reason would satisfy `pytest.raises` just as well.
    """
    with pytest.raises(ModulePlaneSelectionError, match="atomic plane contract"):
        validate_module_plane_selections(
            [module],
            [
                ModulePlaneSelection(
                    module="integration", planes=(ModulePlane.PLATFORM,)
                )
            ],
        )


# ── Declared is not enough; it must be verified ─────────────────────────────


def _migration_literals(path: Path) -> dict[str, tuple[str, ...]]:
    """The three prerequisite tuples, read statically.

    `ast` rather than an import: importing the revision evaluates
    `resolve_depends_on` at module scope, whose answer depends on which bindings
    happen to be installed in this process. The literals do not.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    literals: dict[str, tuple[str, ...]] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
        else:
            continue
        if node.value is None:
            continue
        for name in targets:
            if name in {"COMMON_REQUIRES", "TENANT_REQUIRES", "PLATFORM_REQUIRES"}:
                literals[name] = ast.literal_eval(node.value)
    return literals


def test_the_migration_declares_the_same_prerequisites_as_the_manifest() -> None:
    """Manifest and lineage are two audiences for one fact — composition reads
    the manifest, `alembic upgrade` reads the migration — and only the second
    is checked against a real database."""
    literals = [_migration_literals(path) for path in MIGRATIONS]
    verified = tuple(
        requirement
        for migration in literals
        for requirement in migration["COMMON_REQUIRES"]
    )
    assert verified == tuple(module.requires)
    assert all(migration["TENANT_REQUIRES"] == () for migration in literals)
    assert all(migration["PLATFORM_REQUIRES"] == () for migration in literals)
    assert tuple(module.tenant_requires) == tuple(module.platform_requires) == ()


def test_the_prerequisite_is_actually_verified_against_the_database() -> None:
    """A declaration orders migrations; it does not prove anything ran. Without
    `require_prerequisites` the whole change would be paperwork, and an adopter
    with a stamped or half-supplied ledger would still reach production."""
    for migration in MIGRATIONS:
        source = migration.read_text(encoding="utf-8")
        assert "require_prerequisites(op.get_bind(), REQUIRES)" in source
        assert "resolve_depends_on(COMMON_REQUIRES)" in source


def test_the_verification_did_not_land_in_a_released_revision() -> None:
    """The rule `tests/architecture/test_released_migrations.py` enforces by
    digest, stated here as intent: no file that shipped in a tag learned to
    verify a prerequisite after the fact."""
    versions = SOURCE / "migrations/versions"
    verifying = sorted(
        path.name
        for path in versions.glob("ig_*.py")
        if "require_prerequisites" in path.read_text(encoding="utf-8")
    )
    assert verifying == [path.name for path in MIGRATIONS]


# ── Release surfaces ────────────────────────────────────────────────────────


def test_the_floor_is_the_highest_prerequisite_release() -> None:
    """a68 publishes the audit prerequisite after the a66 ledger name."""
    manifest = tomllib.loads(
        (PACKAGE_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert manifest["tool"]["poetry"]["dependencies"]["dotmac-kernel"] == ">=0.1.0a68"


def test_the_release_entry_requires_every_migration_in_the_wheel() -> None:
    """Dropping either DDL-free verification would strand its declaration."""
    entry = json.loads(
        (REPO_ROOT / ".github/release-modules.json").read_text(encoding="utf-8")
    )["modules"]["dotmac-integration"]
    assert entry["kernel_floor"] == "0.1.0a68"
    required = set(entry["wheel_contents"]["required"])
    on_disk = {
        f"dotmac_integration/migrations/versions/{path.name}"
        for path in (SOURCE / "migrations/versions").glob("ig_*.py")
    }
    assert on_disk <= required, sorted(on_disk - required)
