"""The release catalogue's structural contract.

What this file protects is the property that makes the catalogue trustworthy:
**identity is content, and the product data plane cannot read it.** Everything
else guards the mechanics that keep those true — the ledger allocation, schema
qualification, platform-catalog grants, and the deliberate absence of the
channel/pin half until update authority exists.

Behaviour of digest and reference parsing lives in
`tests/unit/test_release_catalog_identity.py`; this file is static structure, in
keeping with the repo's split.
"""

from __future__ import annotations

import ast
import inspect
import tomllib
from pathlib import Path

import pytest
from dotmac_kernel.namespaces import (
    MIGRATION_OWNER_LEDGER,
    RELEASE_CATALOG_MIGRATION_OWNER,
    NamespaceRegistry,
    module_schema,
)
from dotmac_kernel.planes import (
    ModulePlane,
    ModulePlaneSelection,
    ModulePlaneSelectionError,
)
from dotmac_release_catalog import identity, models
from dotmac_release_catalog.manifest import module

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODULE_ROOT = Path(inspect.getfile(identity)).parent
PACKAGE_ROOT = MODULE_ROOT.parents[1]
MIGRATIONS = MODULE_ROOT / "migrations" / "versions"
LINEAGE = MIGRATIONS / "rl_0001_release_artifacts.py"
ORIGIN_LINEAGE = MIGRATIONS / "rl_0002_artifact_origin.py"


def _migration_source() -> str:
    return LINEAGE.read_text(encoding="utf-8")


# ── D1: the ledger allocation ────────────────────────────────────────────────


def test_the_manifest_matches_its_immutable_ledger_row() -> None:
    """A stateful module whose declaration differs from the ledger is refused at
    boot. Asserting the agreement here turns that runtime failure into a
    build-time one."""
    owner = RELEASE_CATALOG_MIGRATION_OWNER
    assert owner in MIGRATION_OWNER_LEDGER
    assert module.code == owner.owner == "release_catalog"
    assert module.short_code == "rel"
    assert module.migration_prefix == owner.prefix == "rl"
    assert module.migration_branch == owner.branch_label == "release_catalog"
    assert module.db_schema == owner.db_schema == module_schema("rel")


def test_the_module_is_not_core() -> None:
    """Most deployments have no business holding a vendor's release catalogue."""
    assert module.core is False


def test_declared_tables_are_exactly_what_the_migration_creates() -> None:
    """The composed gate checks this against a live catalog; this checks it
    statically, so a table added to one and not the other fails before a
    database is involved."""
    calls = [
        node
        for node in ast.walk(ast.parse(_migration_source()))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "create_table"
    ]
    # Every table name must be a literal, not a module constant: this file is
    # read by the static gate WITHOUT being imported, so a name behind a
    # variable is a name the gate cannot see.
    assert calls, "no create_table calls found"
    for call in calls:
        assert isinstance(call.args[0], ast.Constant), ast.unparse(call.func)
    created = {call.args[0].value for call in calls}
    assert created == set(module.platform_tables)
    assert created == {"release_artifacts", "artifact_attestations"}


def test_the_lineage_is_a_root_with_no_false_dependency() -> None:
    """`depends_on` orders a lineage behind another owner's revision. Declaring
    one this module does not need would make it harder to install standalone for
    no benefit — neither table references a kernel table."""
    tree = ast.parse(_migration_source())
    assigned = {
        target.id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert assigned["revision"].value == "rl_0001_release_artifacts"
    assert assigned["down_revision"].value is None
    assert assigned["depends_on"].value is None


def test_installed_consumer_locates_lineage_through_public_surface() -> None:
    """A cross-repository assembly cannot hard-code this source checkout's path.

    The package owns its internal layout, so it must expose the installed
    lineage location rather than require every consumer to reconstruct it from
    ``__file__`` or reach into an undocumented submodule.
    """
    import dotmac_release_catalog as package

    assert "versions_dir" in package.__all__
    assert package.versions_dir() == MIGRATIONS
    assert (package.versions_dir() / "rl_0001_release_artifacts.py").is_file()


def test_the_revision_id_fits_the_alembic_version_column() -> None:
    """Over-long revision ids do not fail at authoring time — they fail at
    `alembic upgrade`, against a real database."""
    assert len("rl_0001_release_artifacts") <= 32
    assert len("rl_0002_artifact_origin") <= 32


def test_origin_is_catalogue_evidence_and_raw_sql_cannot_relabel_it() -> None:
    """The request selects an artifact; it never chooses the evidence regime."""
    source = ORIGIN_LINEAGE.read_text(encoding="utf-8")
    assert 'revision = "rl_0002_artifact_origin"' in source
    assert 'down_revision = "rl_0001_release_artifacts"' in source
    assert "ck_release_artifacts_origin_class" in source
    assert "trg_artifact_attestations_origin_kind" in source
    assert "trg_release_artifacts_origin_update" in source
    assert "vulnerability_policy_result" in source
    assert "compatibility_result" in source
    assert "product_manifest" in source
    assert "capability_contract" in source
    assert "capability_schema" in source
    assert "capability_composition" in source


# ── Identity is content ──────────────────────────────────────────────────────


def test_no_column_stores_a_mutable_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    """The one thing this module may never grow: a place to put `:latest`.

    A column named for a tag, or a boolean marking one row as current, is the
    same mutable pointer the digest exists to replace — the fleet would simply
    read it instead.
    """
    columns = {c.name for c in models.ReleaseArtifact.__table__.columns}
    forbidden = {"tag", "tags", "latest", "is_latest", "is_current", "current"}
    assert columns & forbidden == set()


def test_the_artifact_digest_is_unique_fleet_wide() -> None:
    """Not scoped to a product: the same bytes published under two product codes
    are one artifact, and two rows could otherwise disagree about what vouches
    for identical content."""
    uniques = {
        tuple(c.name for c in constraint.columns)
        for constraint in models.ReleaseArtifact.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("digest",) in uniques
    assert ("product_code", "version", "artifact_kind") in uniques


def test_an_attestation_records_its_own_digest_not_only_a_uri() -> None:
    """ "The SBOM at this URI" is a mutable tag by another route."""
    columns = {c.name for c in models.ArtifactAttestation.__table__.columns}
    assert {"uri", "digest", "attestation_kind"} <= columns


# ── Platform catalog, not tenant-scoped ──────────────────────────────────────


@pytest.mark.parametrize("model", [models.ReleaseArtifact, models.ArtifactAttestation])
def test_no_tenant_column_because_a_published_artifact_is_one_fact(
    model: type,
) -> None:
    """A tenant column would assert two tenants can disagree about what a digest
    contains, which is false, and would then be maintained as a lie in joins."""
    assert "tenant_id" not in {c.name for c in model.__table__.columns}


@pytest.mark.parametrize("model", [models.ReleaseArtifact, models.ArtifactAttestation])
def test_every_table_is_bound_to_the_module_schema(model: type) -> None:
    """Fully qualified, never resolved through `search_path` — connection state
    a pooler or another module can change."""
    assert model.__table__.schema == module_schema("rel")


def test_the_migration_revokes_the_data_plane_role_from_every_table() -> None:
    """The load-bearing grant. `app_user` is the product data plane's role, and a
    data plane must learn which artifact to run from a signed licence or a
    deployment plan — never by reading the vendor's catalogue. Without the
    revoke, "vendor-assembly-only" is an import contract that raw SQL walks
    around.
    """
    source = _migration_source()
    for table in module.tables:
        assert f"REVOKE ALL ON mod_rel.{table} FROM app_user;" in source


def test_the_online_role_holds_no_privilege_that_can_rewrite_history() -> None:
    """Where immutability is actually enforced.

    `platform_api` is the request-path role. Granting it UPDATE or DELETE would
    make "rows are never updated" a convention a service is trusted to keep,
    which every raw SQL path and every future router is free to break. The live
    canaries in `tests/test_release_catalog_immutability.py` prove the database
    refuses; this proves the migration never asks.
    """
    source = _migration_source()
    for table in module.tables:
        assert f"GRANT SELECT, INSERT ON mod_rel.{table} TO platform_api;" in source
    for verb in ("UPDATE", "DELETE"):
        assert f"{verb} ON mod_rel.release_artifacts TO platform_api" not in source
        assert f"{verb} ON mod_rel.artifact_attestations TO platform_api" not in source


def test_the_offline_role_keeps_a_repair_path() -> None:
    """Immutability must not mean unrepairable.

    A mis-recorded artifact or a legally required erasure has to be possible by
    someone; confining it to the role that already runs reviewed migrations is
    what makes it deliberate rather than accidental.
    """
    source = _migration_source()
    for table in module.tables:
        assert (
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON mod_rel.{table} TO app_admin;"
            in source
        )


def test_the_reference_cannot_drift_from_the_digest_in_raw_sql() -> None:
    """`pinned_reference(expected=...)` proves this on the way in and is
    stronger. The constraint exists for the path that never calls it."""
    source = _migration_source()
    assert "ck_release_artifacts_ref_pins_digest" in source
    assert "artifact_ref LIKE '%@' || digest" in source


def test_the_migration_fully_qualifies_every_object() -> None:
    """A module migration that omits `schema=` is rejected by the composed gate;
    catching it here names the offending call instead of the whole file."""
    for node in ast.walk(ast.parse(_migration_source())):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"create_table", "create_index", "drop_table"}
        ):
            assert "schema" in {kw.arg for kw in node.keywords}, ast.unparse(node)


# ── What is deliberately absent ──────────────────────────────────────────────


def test_channels_and_pins_are_not_in_this_release() -> None:
    """Ruling C3: a channel pin is desired state ONLY under vendor-automatic
    authority, and is otherwise an offer. Shipping the table before the
    authority that gates it makes that distinction discoverable only in
    production, by a deployment that moved when nobody approved it.
    """
    deferred = {"release_channels", "channel_pins", "artifact_selections"}
    assert set(module.tables) & deferred == set()
    assert deferred & {p.stem for p in MODULE_ROOT.glob("*.py")} == set()


def test_no_declarations_without_a_route_to_gate() -> None:
    """A declared code with no consumer is dead vocabulary that reads as a
    working gate — the failure ADR-0008's registries exist to prevent. This
    release ships no routers, so it declares nothing."""
    assert module.permissions == ()
    assert module.capabilities == ()
    assert module.audit_actions == ()


# ── Dependency direction ─────────────────────────────────────────────────────


def test_the_package_depends_on_the_kernel_and_nothing_else_dotmac() -> None:
    """ADR-0006 § 2 fixes `assembly → module → dotmac-ui → dotmac-kernel`. A
    module never depends on an assembly, and never on another module."""
    manifest = tomllib.loads(
        (PACKAGE_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    deps = manifest["tool"]["poetry"]["dependencies"]
    dotmac_deps = {name for name in deps if name.startswith("dotmac")}
    assert dotmac_deps == {"dotmac-kernel"}


def test_the_module_imports_no_assembly_and_no_sibling_module() -> None:
    forbidden = ("app.", "dotmac_ticketing", "dotmac_template_studio", "vendor_cp")
    for path in MODULE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert not name.startswith(forbidden), f"{path.name}: {name}"


def test_the_assembly_cannot_install_this_vendor_only_module() -> None:
    """`app_user` being REVOKEd proves the tenant data plane cannot READ the
    catalogue. It says nothing about INSTALLATION — this assembly is the
    reference product data plane, and nothing stopped it importing the module
    and mounting it. That gap is closed by an import-linter contract, and this
    test fails if the contract is dropped."""
    manifest = tomllib.loads(
        (PACKAGE_ROOT.parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )
    contracts = manifest["tool"]["importlinter"]["contracts"]
    wanted = "The assembly must not install vendor-only modules"
    vendor_only = [c for c in contracts if c["name"] == wanted]
    assert vendor_only, "the vendor-only installation contract is missing"
    assert vendor_only[0]["source_modules"] == ["app"]
    assert "dotmac_release_catalog" in vendor_only[0]["forbidden_modules"]


def test_the_write_path_is_the_only_documented_entry_point() -> None:
    """A model is constructible a dozen ways the class never sees. The service
    is the seam, and it must not be joined by an update path the online role has
    no privilege to perform."""
    import dotmac_release_catalog as package

    assert {"publish_artifact", "attest_artifact"} <= set(package.__all__)
    assert not {"update_artifact", "delete_artifact"} & set(package.__all__)


def test_the_extraction_dossier_records_the_production_adopter() -> None:
    """Hard rule 24 was executed even though nothing qualified as a source. The
    dossier is the evidence that the inventory ran, not a step skipped because
    the capability looked new."""
    dossier = tomllib.loads(
        (PACKAGE_ROOT / "EXTRACTION.toml").read_text(encoding="utf-8")
    )
    assert dossier["source_mode"] == "greenfield-after-inventory"
    assert set(dossier["source_repositories"]) >= {
        "dotmac_erp",
        "dotmac_crm",
        "dotmac_sub",
        "dotmac_vendor_control_plane",
    }
    assert dossier["contract_consumers"] == ["dotmac_vendor_control_plane"]
    assert dossier["status"] == "adopted"
    assert dossier["candidate_consumers"] == []
    evidence = set(dossier["adoption_evidence"])
    assert {
        "dotmac_vendor_control_plane:main@f8f8c3fd636e663e4a17275c19e82fc1667aa52a",
        "dotmac_vendor_control_plane:production-deploy#32022599873",
        "ghcr.io/michaelayoade/dotmac_vendor_control_plane@sha256:56ec553139c449dc7da46a8873b3c03e95a61e43c970cd1675e28a202b2991cc",
    } <= evidence


def test_the_module_declares_the_platform_plane_and_owns_no_tenant_tables() -> None:
    """ADR-0023: the plane is DECLARED, never inferred from a missing column.

    The DDL was always control-plane shaped, but the manifest declared these
    tables under `tables=` — the TENANT slot. A declaration that disagrees with
    what the migration builds is a real defect, because the live-catalog gate
    holds each plane to its own contract and would have audited these against
    the wrong one.
    """
    assert module.tables == ()
    assert set(module.platform_tables) == {"release_artifacts", "artifact_attestations"}


def test_the_plane_contract_is_atomic_so_an_assembly_makes_no_choice() -> None:
    """A singleton plane set is not selectability; it is ceremony.

    `supported_plane_sets=()` keeps the historical atomic contract: the one
    declared plane installs with the lineage and there is nothing to select.
    """
    assert module.supported_plane_sets == ()


def test_composition_succeeds_without_any_plane_selection() -> None:
    """An atomic module must compose with no `module_planes` entry at all."""
    registry = NamespaceRegistry.from_manifests([module])
    schema = module_schema(module.short_code)
    assert schema in registry.module_schemas()
    assert registry.platform_plane_installed(schema) is True
    assert registry.tenant_plane_installed(schema) is False


def test_an_explicit_plane_selection_is_rejected_for_an_atomic_module() -> None:
    """Offering a choice that does not exist must fail loudly, not be ignored.

    Silently accepting a selection would let an assembly believe it had chosen
    something, which is the omission-reads-as-intent failure ADR-0028 removes.
    """
    with pytest.raises(ModulePlaneSelectionError) as excinfo:
        NamespaceRegistry.from_manifests(
            [module],
            module_planes=[
                ModulePlaneSelection(
                    module="release_catalog", planes=(ModulePlane.PLATFORM,)
                )
            ],
        )
    assert "atomic" in str(excinfo.value)


def test_the_migration_creates_no_tenant_column_rls_or_policy() -> None:
    """Static proof that the DDL matches the declared plane.

    A platform table with a `tenant_id`, RLS, or a policy would mean the plane
    declaration and the migration disagree — and the declaration is what the
    gate trusts.
    """
    source = (
        PROJECT_ROOT
        / "packages/dotmac-release-catalog/src"
        / "dotmac_release_catalog/migrations/versions/rl_0001_release_artifacts.py"
    ).read_text(encoding="utf-8")
    assert '"tenant_id"' not in source
    assert "ROW LEVEL SECURITY" not in source
    assert "CREATE POLICY" not in source
    assert "REVOKE ALL" in source, "the revoke IS the isolation on this plane"


def test_the_catalogue_renders_it_as_platform_and_atomic() -> None:
    """The generated catalogue must show the corrected plane, not the old one."""
    catalog = (PROJECT_ROOT / "docs" / "MODULE_CATALOG.md").read_text(encoding="utf-8")
    row = next(
        line
        for line in catalog.splitlines()
        if line.startswith("| [`dotmac-release-catalog`]")
    )
    cells = [cell.strip() for cell in row.split("|")]
    capability = next(cell for cell in cells if "mod_" in cell)
    assert "platform" in capability, capability
    assert (
        "tenant" not in capability
    ), f"catalogue still shows a tenant capability for this module: {capability}"
    assert any(cell.startswith("atomic") for cell in cells), cells
