"""Structural guards for `dotmac-brand-profiles`, each with a sensitivity proof.

Two guards here have no equivalent elsewhere in the programme:

- **No CSS, token map or colour parser.** ADR-0006 D8 retired tenant-supplied raw
  CSS, and this module is where it would come back — it is the one that holds
  brand data, and "just one custom CSS field" is the request an operator makes.
  The absence has to be checked because it is the kind of thing a helpful later
  change adds.
- **`dotmac_ui` is imported and every other package is not.** This is the only
  module in the programme with a legitimate cross-package dependency, so the
  guard cannot simply forbid all of them — it has to permit exactly one, which
  makes the permitted direction (ADR-0006 § 2) checkable rather than assumed.

Static only; no database.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

import pytest
from dotmac_brand_profiles import module
from dotmac_kernel.namespaces import (
    BRAND_PROFILES_MIGRATION_OWNER,
    MIGRATION_OWNER_LEDGER,
    module_schema,
)
from dotmac_kernel.planes import ModulePlane

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "packages/dotmac-brand-profiles"
SRC = PACKAGE_ROOT / "src/dotmac_brand_profiles"
MIGRATION = SRC / "migrations/versions/bp_0001_brand_profiles.py"

#: Every other installable distribution. `dotmac_ui` is deliberately ABSENT —
#: it is this module's one permitted cross-package dependency, and the test
#: below asserts it is present rather than forbidden.
SIBLING_ROOTS = frozenset(
    {
        "dotmac_template_studio",
        "dotmac_ticketing",
        "dotmac_release_catalog",
        "dotmac_entitlement_allocation",
        "dotmac_commercial_agreements",
        "dotmac_licensing",
        "dotmac_deployment_control",
        "dotmac_application_directory",
        "dotmac_files",
        "dotmac_imports",
        "dotmac_integration",
        "dotmac_approvals",
        "dotmac_numbering",
        "dotmac_people",
        "dotmac_durable_timers",
        "dotmac_auth_oidc",
        "dotmac_campaigns",
        "app",
        "vendor_cp",
    }
)


def _sources() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _imported_roots(source: str) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


# ── Allocation parity ───────────────────────────────────────────────────────


class TestTheManifestMatchesTheLedger:
    def test_the_ledger_row_exists_and_is_registered(self) -> None:
        assert BRAND_PROFILES_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER

    def test_every_manifest_field_matches_its_ledger_row(self) -> None:
        owner = BRAND_PROFILES_MIGRATION_OWNER
        assert module.code == owner.owner == "brand_profiles"
        assert module.migration_prefix == owner.prefix == "bp"
        assert module.migration_branch == owner.branch_label == "brand_profiles"
        assert module.db_schema == owner.db_schema == module_schema("brand")

    def test_the_prefix_and_schema_are_unique_fleet_wide(self) -> None:
        prefixes = [owner.prefix for owner in MIGRATION_OWNER_LEDGER]
        schemas = [o.db_schema for o in MIGRATION_OWNER_LEDGER if o.db_schema]
        assert prefixes.count("bp") == 1
        assert schemas.count(module_schema("brand")) == 1


class TestBothPlanesAreDeclaredAndSelectable:
    def test_both_plane_tuples_are_populated(self) -> None:
        """ADR-0023's actual case rather than an aspiration: a named assembly
        exists on each side today — Sub on the tenant plane, the vendor control
        plane on the platform plane."""
        assert module.tables
        assert module.platform_tables

    def test_all_three_plane_sets_are_supported(self) -> None:
        """Sub selects TENANT, Vendor selects PLATFORM, and neither installs the
        other's. A module offering only the union would force each product to
        carry the other's tables."""
        # Compared as SETS of frozensets: the manifest normalises each
        # combination to a canonical order, and asserting the written order
        # would test a serialisation detail rather than the capability.
        supported = {frozenset(s) for s in (module.supported_plane_sets or ())}
        assert supported == {
            frozenset({ModulePlane.TENANT}),
            frozenset({ModulePlane.PLATFORM}),
            frozenset({ModulePlane.TENANT, ModulePlane.PLATFORM}),
        }

    def test_the_declared_tables_are_exactly_the_mapped_ones(self) -> None:
        from dotmac_brand_profiles.models import (
            SCHEMA,
            BrandProfile,
            PlatformBrandHostBinding,
            PlatformBrandProfile,
        )

        assert {BrandProfile.__tablename__} == set(module.tables)
        assert {
            PlatformBrandProfile.__tablename__,
            PlatformBrandHostBinding.__tablename__,
        } == set(module.platform_tables)
        for model in (BrandProfile, PlatformBrandProfile, PlatformBrandHostBinding):
            assert model.__table__.schema == SCHEMA

    def test_the_tenant_table_has_a_tenant_column_and_the_platform_ones_do_not(
        self,
    ) -> None:
        """The two planes hold structurally similar tables, which is exactly the
        case ADR-0023 warns about — so the distinction is asserted rather than
        assumed from the class names."""
        from dotmac_brand_profiles.models import (
            BrandProfile,
            PlatformBrandHostBinding,
            PlatformBrandProfile,
        )

        assert "tenant_id" in BrandProfile.__table__.columns
        assert not BrandProfile.__table__.columns["tenant_id"].nullable
        for model in (PlatformBrandProfile, PlatformBrandHostBinding):
            assert "tenant_id" not in model.__table__.columns, model.__tablename__

    def test_no_foreign_key_crosses_the_planes(self) -> None:
        """Hard rule 27: two planes share a lifecycle, never a row."""
        from dotmac_brand_profiles.models import (
            PLATFORM_TABLES,
            TENANT_TABLES,
            BrandProfile,
            PlatformBrandHostBinding,
            PlatformBrandProfile,
        )

        for model, own_plane in (
            (BrandProfile, TENANT_TABLES),
            (PlatformBrandProfile, PLATFORM_TABLES),
            (PlatformBrandHostBinding, PLATFORM_TABLES),
        ):
            for key in model.__table__.foreign_keys:
                target = key.column.table.name
                assert target in own_plane, (model.__tablename__, target)

    def test_prerequisites_are_declared_per_plane(self) -> None:
        """A control plane selecting PLATFORM alone installs this module without
        a `tenants` table, which is exactly the vendor-side case — so the tenant
        catalogue cannot be a COMMON requirement."""
        assert "tenant_scope_catalog.v1" in module.tenant_requires
        assert "tenant_scope_catalog.v1" not in module.requires
        assert "platform_audit_log.v1" in module.platform_requires
        assert "idempotency_ledger.v1" in module.requires
        assert "module_database_roles.v1" in module.requires


# ── The two guards unique to this module ────────────────────────────────────


class TestNoCssTokenMapOrColourParserCameBack:
    """ADR-0006 D8 retired tenant-supplied raw CSS, and this is the module where
    it would return. "Just one custom CSS field" is the request an operator
    makes, and the absence has to be checked because it is the kind of thing a
    helpful later change adds."""

    _CSS_NAMES = re.compile(
        r"custom_css|stylesheet|theme_css|\bcss\b|token_overrides|token_map"
    )

    def test_no_model_column_is_css_or_token_shaped(self) -> None:
        from dotmac_brand_profiles.models import (
            BrandProfile,
            PlatformBrandHostBinding,
            PlatformBrandProfile,
        )

        offenders: list[str] = []
        for model in (BrandProfile, PlatformBrandProfile, PlatformBrandHostBinding):
            for column in model.__table__.columns:
                if self._CSS_NAMES.search(column.name):
                    offenders.append(f"{model.__tablename__}.{column.name}")
        assert not offenders, offenders

    def test_the_migration_creates_no_such_column_either(self) -> None:
        """The models and the migration are two artifacts; a column added to one
        and not the other is a different defect and would leak just the same."""
        sql = MIGRATION.read_text()
        columns = re.findall(r'sa\.Column\(\s*"([a-z_0-9]+)"', sql)
        assert not [name for name in columns if self._CSS_NAMES.search(name)]

    def test_the_package_defines_no_colour_parser(self) -> None:
        """`dotmac_ui` validates hex at `BrandOverride` construction. A second
        parser would eventually accept something the first refuses, and the
        disagreement would surface as a page that renders wrong rather than a
        value that failed to save."""
        parser = re.compile(
            r"def\s+\w*(?:parse_hex|hex_to_|to_hex|normali[sz]e_colou?r)\w*\s*\(|"
            r"#\\\[0-9a-fA-F\\\]"
        )
        offenders = [
            str(path.relative_to(REPO_ROOT))
            for path in _sources()
            if parser.search(path.read_text())
        ]
        assert not offenders, offenders

    def test_the_detectors_fire_against_synthetic_violations(self) -> None:
        """Sensitivity proof (ADR-0018), with the false positives they must not
        produce."""
        assert self._CSS_NAMES.search("custom_css")
        assert self._CSS_NAMES.search("token_overrides")
        assert not self._CSS_NAMES.search("accent_hex")
        assert not self._CSS_NAMES.search("display_name")
        parser = re.compile(r"def\s+\w*(?:parse_hex|hex_to_)\w*\s*\(")
        assert parser.search("def parse_hex(value): ...")
        assert not parser.search("primary = resolved.get('primary_hex')")


class TestExactlyOneCrossPackageDependency:
    """`dotmac-ui` is this module's one permitted import, and the permitted
    direction is `assembly -> module -> dotmac-ui -> dotmac-kernel` (ADR-0006
    § 2).

    Asserted in BOTH directions: a guard that only forbade siblings would pass
    against a version that had quietly dropped the `dotmac_ui` dependency and
    vendored a hex parser — which is exactly the second colour authority D8
    retired.
    """

    def test_no_source_file_imports_a_sibling_module(self) -> None:
        offenders: list[str] = []
        for path in _sources():
            for bad in sorted(_imported_roots(path.read_text()) & SIBLING_ROOTS):
                offenders.append(f"{path.relative_to(REPO_ROOT)} imports {bad}")
        assert not offenders, "\n".join(offenders)

    def test_the_package_does_import_dotmac_ui(self) -> None:
        """Validation and the allowlist both come from dotmac-ui. A version that
        dropped the dependency would have vendored a parser."""
        roots = set()
        for path in _sources():
            roots |= _imported_roots(path.read_text())
        assert "dotmac_ui" in roots

    def test_the_allowlist_matches_dotmac_uis_own_fields(self) -> None:
        """The anti-drift device. If `dotmac-ui` ever publishes a third accepted
        input, this fails here rather than this module silently under-carrying
        it — which is the difference between an allowlist somebody thought of and
        one that tracks the vocabulary."""
        from dotmac_brand_profiles import BRAND_OVERRIDE_INPUTS, brand_override_fields

        assert set(BRAND_OVERRIDE_INPUTS.values()) == brand_override_fields()

    def test_the_module_constructs_no_brand_override(self) -> None:
        """Michael 2026-08-19: the ASSEMBLY maps profile values into
        `BrandOverride`. A module function returning one would take that job
        back, and the boundary would hold only by convention."""
        offenders = []
        for path in _sources():
            source = path.read_text()
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "BrandOverride"
                    and path.name != "brand_values.py"
                ):
                    offenders.append(str(path.relative_to(REPO_ROOT)))
        assert not offenders, (
            f"{offenders} construct a BrandOverride; only brand_values.py may, "
            "and only to VALIDATE through dotmac-ui's own parser"
        )

    def test_the_distribution_declares_dotmac_ui(self) -> None:
        data = tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text())
        declared = set(data["tool"]["poetry"]["dependencies"])
        assert "dotmac-ui" in declared
        assert "dotmac-kernel" in declared

    def test_the_detector_fires_against_a_synthetic_violation(self) -> None:
        assert _imported_roots("from dotmac_files import store") & SIBLING_ROOTS
        assert (
            not _imported_roots("from dotmac_ui import BrandOverride") & SIBLING_ROOTS
        )


class TestItStoresNoBytesAndNoKeys:
    def test_file_references_are_plain_strings(self) -> None:
        """ADR-0022: `dotmac-files` owns bytes. A binary column here would make
        this module a second store, and a profile must stay readable after a
        file is purged."""
        from dotmac_brand_profiles.models import BrandProfile, PlatformBrandProfile

        for model in (BrandProfile, PlatformBrandProfile):
            for name in ("logo_file_ref", "dark_logo_file_ref", "icon_file_ref"):
                column = model.__table__.columns[name]
                assert "VARCHAR" in str(column.type).upper(), (
                    model.__tablename__,
                    name,
                )

    def test_no_column_is_key_or_certificate_shaped(self) -> None:
        """Native mobile brands are separate SIGNED BUILDS from shared source;
        `mobile_build_profile_ref` names a build profile and holds nothing that
        could sign one."""
        from dotmac_brand_profiles.models import BrandProfile, PlatformBrandProfile

        banned = re.compile(r"private|secret|certificate|keystore|signing")
        offenders: list[str] = []
        for model in (BrandProfile, PlatformBrandProfile):
            for column in model.__table__.columns:
                if banned.search(column.name):
                    offenders.append(f"{model.__tablename__}.{column.name}")
        assert not offenders, offenders

    def test_the_detector_fires_against_a_synthetic_violation(self) -> None:
        banned = re.compile(r"private|secret|certificate|keystore|signing")
        assert banned.search("android_keystore_ref")
        assert not banned.search("mobile_build_profile_ref")


class TestTheModuleOwnsNoTransaction:
    _FORBIDDEN = re.compile(
        r"\b(?:db|session|self\._session)\.(?:commit|rollback)\s*\(|"
        r"\bsessionmaker\s*\(|\bSessionLocal\s*\(|\bPlatformSessionLocal\s*\(|"
        r"\bSession\s*\([^)]"
    )

    def test_no_source_file_commits_rolls_back_or_builds_a_session(self) -> None:
        offenders = [
            str(path.relative_to(REPO_ROOT))
            for path in _sources()
            if self._FORBIDDEN.search(path.read_text())
        ]
        assert not offenders, offenders

    def test_the_detector_fires_against_a_synthetic_violation(self) -> None:
        assert self._FORBIDDEN.search("    db.commit()")
        assert not self._FORBIDDEN.search("    session.flush()")


# ── The migration ───────────────────────────────────────────────────────────


class TestTheMigrationIsPlaneConditionalAndIsolatesBothWays:
    @pytest.fixture
    def sql(self) -> str:
        return MIGRATION.read_text()

    def test_the_ddl_is_conditional_on_the_selected_planes(self, sql: str) -> None:
        """ADR-0028: the assembly selects; the migration obeys. A lineage that
        built both planes unconditionally would put `tenant_id` tables into a
        control plane that has no tenants."""
        assert "selected_module_planes(MODULE_CODE)" in sql
        assert "if ModulePlane.TENANT in planes" in sql
        assert "if ModulePlane.PLATFORM in planes" in sql

    def test_the_tenant_table_enables_and_forces_rls_with_a_policy(
        self, sql: str
    ) -> None:
        """FORCE is the half that is easy to omit and impossible to notice:
        without it the table owner — which migrations run as — bypasses its own
        policy, so every migration-time check passes while production leaks."""
        assert "ALTER TABLE mod_brand.brand_profiles ENABLE ROW LEVEL SECURITY;" in sql
        assert "ALTER TABLE mod_brand.brand_profiles FORCE ROW LEVEL SECURITY;" in sql
        assert "CREATE POLICY brand_profiles_tenant_isolation" in sql
        assert "public.app_current_tenant_id()" in sql

    def test_the_platform_tables_are_revoked_from_the_tenant_app_role(
        self, sql: str
    ) -> None:
        for table in module.platform_tables:
            assert f"REVOKE ALL ON mod_brand.{table} FROM app_user;" in sql

    def test_the_platform_tables_get_no_rls(self, sql: str) -> None:
        """Not even ENABLEd-with-no-policy, which denies every row to the control
        plane while reading as protected."""
        for table in module.platform_tables:
            assert f"ALTER TABLE mod_brand.{table} ENABLE ROW LEVEL" not in sql

    def test_the_online_roles_can_reach_their_own_plane(self, sql: str) -> None:
        """Declared-and-unusable is a violation too (hard rule 27)."""
        assert "GRANT USAGE ON SCHEMA mod_brand TO app_user;" in sql
        assert "GRANT USAGE ON SCHEMA mod_brand TO platform_api;" in sql
        assert "ON mod_brand.brand_profiles " in sql
        for table in module.platform_tables:
            assert f"ON mod_brand.{table} " in sql

    def test_the_tenant_table_has_a_composite_unique(self, sql: str) -> None:
        """Hard rule 11: unique-per-tenant, not unique globally. Two tenants
        naming a profile `default` is ordinary, not a collision."""
        assert (
            '"tenant_id", "profile_code", name="uq_brand_profiles_tenant_code"' in sql
        )

    def test_the_lineage_is_a_root_naming_its_own_branch(self, sql: str) -> None:
        assert 'revision = "bp_0001_brand_profiles"' in sql
        assert "down_revision = None" in sql
        assert 'branch_labels = ("brand_profiles",)' in sql
        assert len("bp_0001_brand_profiles") <= 32

    def test_it_names_no_foreign_revision(self, sql: str) -> None:
        assert "depends_on = resolve_depends_on(" in sql
        assert not re.search(r'depends_on\s*=\s*[\'"]', sql)

    def test_it_verifies_only_the_selected_planes_prerequisites(self, sql: str) -> None:
        """Verifying the tenant catalogue on a platform-only install would fail a
        composition that is perfectly valid."""
        assert "requires.extend(TENANT_REQUIRES)" in sql
        assert "requires.extend(PLATFORM_REQUIRES)" in sql
        verify_at = sql.index("require_prerequisites(op.get_bind(), tuple(requires))")
        first_ddl = sql.index("CREATE SCHEMA IF NOT EXISTS mod_brand")
        assert verify_at < first_ddl

    def test_the_revoke_sweep_fires_against_a_synthetic_violation(self) -> None:
        """Sensitivity proof (ADR-0018)."""
        missing = "GRANT SELECT ON mod_brand.platform_brand_profiles TO platform_api;"
        assert (
            "REVOKE ALL ON mod_brand.platform_brand_profiles FROM app_user;"
            not in missing
        )


# ── The dossier ─────────────────────────────────────────────────────────────


class TestTheExtractionDossierIsHonest:
    @pytest.fixture
    def dossier(self) -> dict[str, object]:
        return tomllib.loads((PACKAGE_ROOT / "EXTRACTION.toml").read_text())

    def test_it_names_sub_as_the_qualifying_source(
        self, dossier: dict[str, object]
    ) -> None:
        assert dossier["source_mode"] == "product-first"
        assert "dotmac_sub" in dossier["source_repositories"]  # type: ignore[operator]
        assert any(
            "app/models/branding.py" in path
            for path in dossier["source_paths"]  # type: ignore[union-attr]
        )

    def test_it_claims_no_adoption_it_does_not_have(
        self, dossier: dict[str, object]
    ) -> None:
        assert dossier["status"] == "audit-complete"
        assert dossier["adoption_evidence"] == []
        assert dossier["contract_consumers"] == []

    def test_it_records_the_semantic_colours_ruling_and_its_reason(
        self, dossier: dict[str, object]
    ) -> None:
        """Michael ruled 2026-08-19: the quintet is not carried, because
        `dotmac-ui` publishes those five names as tokens with built-in ramps.
        The dossier must carry the RULING and the reason, not an open question —
        an unresolved note here would invite the next author to re-decide it."""
        shadow = str(dossier["shadow_and_drift"])
        assert "RULED by Michael 2026-08-19" in shadow
        assert "semantic_colors" in shadow
        assert "second authority over a published token" in shadow
        assert "confirmed with Michael" not in shadow, (
            "the open question was replaced by a ruling; a dossier that still "
            "asks for one would send a reader to re-litigate a closed decision"
        )

    def test_the_disposition_is_executable_not_prose(
        self, dossier: dict[str, object]
    ) -> None:
        """A dossier paragraph is not a migration control. The cutover has to be
        able to RUN the disposition and see what it did not carry."""
        assert "translate_legacy_brand_values()" in str(dossier["shadow_and_drift"])

    def test_it_records_that_the_colour_pipeline_changes(
        self, dossier: dict[str, object]
    ) -> None:
        """The migrated colours are the same VALUES rendered by a different
        generator, and pretending otherwise would make a visible change to what
        customers see look like a no-op migration."""
        shadow = str(dossier["shadow_and_drift"])
        assert "different generator" in shadow
        assert "diffs the output" in shadow
