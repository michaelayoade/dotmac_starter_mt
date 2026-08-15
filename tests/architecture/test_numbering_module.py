"""Structural canaries for ``dotmac-numbering`` (ADR-0030 step 5).

These guard the boundary, not the behaviour — the behaviour is proven on real
PostgreSQL in ``tests/test_numbering_isolation.py``.

Several exist because the SOURCE has the defect and a comment alone would not
stop it coming back through a port. `numbering-source-variance.md` records each
one at file and line in ERP or Sub.
"""

from __future__ import annotations

import ast
import inspect
import json
import tomllib
from pathlib import Path

from dotmac_kernel.namespaces import MIGRATION_OWNER_LEDGER, NUMBERING_MIGRATION_OWNER
from dotmac_numbering import models, service
from dotmac_numbering.manifest import module

MODULE_ROOT = Path(inspect.getfile(service)).parent
MIGRATION = MODULE_ROOT / "migrations/versions/nu_0001_numbering.py"
REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "packages/dotmac-numbering"


def _module_source() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(MODULE_ROOT.rglob("*.py"))
    )


def test_manifest_matches_the_immutable_namespace_allocation() -> None:
    assert NUMBERING_MIGRATION_OWNER in MIGRATION_OWNER_LEDGER
    assert module.short_code == NUMBERING_MIGRATION_OWNER.owner
    assert module.migration_prefix == NUMBERING_MIGRATION_OWNER.prefix
    assert module.migration_branch == NUMBERING_MIGRATION_OWNER.branch_label
    assert models.SCHEMA == NUMBERING_MIGRATION_OWNER.db_schema == "mod_numbering"


def test_both_planes_are_declared_and_disjoint() -> None:
    assert module.tables == models.TENANT_TABLES
    assert module.platform_tables == models.PLATFORM_TABLES
    assert module.tables and module.platform_tables
    assert not set(module.tables) & set(module.platform_tables)


def test_platform_tables_carry_no_tenant_column() -> None:
    for model in (
        models.PlatformNumberSeries,
        models.PlatformSeriesCounter,
        models.PlatformAllocationReceipt,
        models.PlatformSeriesRepair,
    ):
        assert "tenant_id" not in model.__table__.c, (
            f"{model.__name__} has a tenant column; the platform plane is "
            "isolated by REVOKE, and a tenant column there invites RLS that "
            "does not exist"
        )


def test_every_tenant_table_has_a_not_null_tenant_and_composite_identity() -> None:
    for model in (
        models.NumberSeries,
        models.SeriesCounter,
        models.AllocationReceipt,
        models.SeriesRepair,
    ):
        column = model.__table__.c["tenant_id"]
        assert column.nullable is False
        constraints = {
            tuple(c.columns.keys())
            for c in model.__table__.constraints
            if hasattr(c, "columns")
        }
        assert any("tenant_id" in cols for cols in constraints)


def test_no_foreign_key_crosses_the_planes() -> None:
    for model in (
        models.NumberSeries,
        models.SeriesCounter,
        models.AllocationReceipt,
        models.SeriesRepair,
        models.PlatformNumberSeries,
        models.PlatformSeriesCounter,
        models.PlatformAllocationReceipt,
        models.PlatformSeriesRepair,
    ):
        for fk in model.__table__.foreign_keys:
            target = str(fk.target_fullname)
            # SQLAlchemy renders this unqualified: `tenants.id`, not
            # `public.tenants.id`.
            assert target.startswith("tenants."), (
                f"{model.__name__} points at {target}; the only permitted edge "
                "is the tenant catalogue, and no edge may cross the planes"
            )


def test_the_migration_declares_the_same_prerequisites_as_the_manifest() -> None:
    tree = ast.parse(MIGRATION.read_text(encoding="utf-8"))
    literals: dict[str, tuple[str, ...]] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in {"COMMON_REQUIRES", "TENANT_REQUIRES", "PLATFORM_REQUIRES"}:
                literals[name] = tuple(ast.literal_eval(node.value))
    assert literals["COMMON_REQUIRES"] == module.requires
    assert literals["TENANT_REQUIRES"] == module.tenant_requires


# ── The five refusals the sources make necessary ────────────────────────────


def test_the_module_reads_no_clock_for_a_business_decision() -> None:
    """`reference_date` is an input, never `date.today()`.

    ERP allocates against `date.today()` on sixteen caller sites, so a
    backdated invoice takes today's period. `allocated_at` is the only time the
    module takes from the system, and it is evidence rather than a decision.
    """
    source = _module_source()
    # ZERO, not a budget. An allowance of N is not an invariant — it is a
    # licence to add the N+1st read and lower the bar. Evidence instants are
    # PostgreSQL server defaults, so the module has no reason to read a clock
    # at all, and any reappearance is a real change in behaviour.
    for reader in ("date.today", "datetime.today", "datetime.now", "utcnow"):
        assert reader not in source, (
            f"{reader} in the numbering module. Transaction time records "
            "evidence and never influences a period, a format, an allocation "
            "or a repair — evidence instants are server defaults."
        )


def test_the_module_reads_no_settings_and_no_environment() -> None:
    """Configuration is a row supplied by the product (ADR-0009, ADR-0011)."""
    source = _module_source()
    for forbidden in ("os.environ", "getenv", "settings_resolver", "resolve_value"):
        assert forbidden not in source, f"{forbidden} on the allocation path"


def test_there_is_exactly_one_formatter() -> None:
    """ERP has three and two disagree — its preview hardcodes four digits, so
    for every other width the preview is a lie. Preview must call allocation's
    own formatter."""
    tree = ast.parse((MODULE_ROOT / "service.py").read_text(encoding="utf-8"))
    formatters = [
        n.name
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and "format" in n.name
    ]
    assert formatters == ["format_number"], formatters
    preview_src = inspect.getsource(service.preview)
    assert "format_number(" in preview_src


def test_at_most_once_is_delegated_to_the_kernel() -> None:
    """Hard rule 23: `dotmac_kernel.idempotency` owns replay, fingerprint
    comparison and the concurrent-key race, and nothing else may.

    A hand-rolled receipt lookup has a hole the kernel does not — two
    concurrent calls with the same key both miss it, both allocate, and the
    loser raises IntegrityError instead of replaying.
    """
    source = (MODULE_ROOT / "service.py").read_text(encoding="utf-8")
    assert "execute_once" in source and "execute_once_platform" in source
    # No second ledger, and no local replay decision.
    for reimplementation in (
        "class IdempotencyRecord",
        "request_fingerprint",
        "idempotency_conflict",
    ):
        assert (
            reimplementation not in source
        ), f"{reimplementation} reimplements kernel idempotency mechanics"


def test_every_counter_is_scoped_to_a_period() -> None:
    """A resetting series has one counter per period.

    With a single counter, yearly reset reuses value 1 and collides with the
    prior year's receipt; and a backdated allocation formats an old-period
    number from the current counter, which can duplicate an issued one.
    """
    for model in (models.SeriesCounter, models.PlatformSeriesCounter):
        assert "period_key" in model.__table__.c
        assert model.__table__.c["period_key"].nullable is False
    for model in (models.AllocationReceipt, models.PlatformAllocationReceipt):
        uniques = {
            tuple(sorted(c.columns.keys()))
            for c in model.__table__.constraints
            if type(c).__name__ == "UniqueConstraint"
        }
        keyed = any(
            "period_key" in cols and "allocated_value" in cols for cols in uniques
        )
        assert keyed, f"{model.__name__} must key value uniqueness on the period too"


def test_evidence_instants_are_server_defaults() -> None:
    """The other half of "reads no clock": the column has to fill itself."""
    for model, column in (
        (models.AllocationReceipt, "allocated_at"),
        (models.PlatformAllocationReceipt, "allocated_at"),
        (models.SeriesRepair, "repaired_at"),
        (models.PlatformSeriesRepair, "repaired_at"),
    ):
        assert model.__table__.c[column].server_default is not None, (
            f"{model.__name__}.{column} must be server-populated, or the "
            "module needs a clock read to fill it"
        )


def test_the_rendered_number_is_unique_per_series() -> None:
    """Value-and-period uniqueness cannot see a re-rendered string.

    A configuration change could move to a new period scheme, restart at the
    start value and reproduce a number already issued; the counter-value key
    would allow it because the period differs.
    """
    for model in (models.AllocationReceipt, models.PlatformAllocationReceipt):
        uniques = {
            tuple(sorted(c.columns.keys()))
            for c in model.__table__.constraints
            if type(c).__name__ == "UniqueConstraint"
        }
        assert any(
            "formatted_number" in cols for cols in uniques
        ), f"{model.__name__} must make the rendered string unique per series"


def test_identity_shaping_configuration_freezes_once_a_series_has_history() -> None:
    assert service.IDENTITY_SHAPING_FIELDS
    assert "reset_policy" in service.IDENTITY_SHAPING_FIELDS
    assert "min_digits" in service.IDENTITY_SHAPING_FIELDS
    # start_value seeds periods that have not begun and cannot move one that
    # has, so freezing it would refuse a safe change.
    assert "start_value" not in service.IDENTITY_SHAPING_FIELDS
    assert "_assert_safe_transition" in inspect.getsource(service.configure_series)


def test_every_deciding_path_locks_the_series_first() -> None:
    """Series, then counter — everywhere.

    Without the series lock a first allocation can render from configuration a
    concurrent update has already replaced. Preview is exempt: it decides
    nothing and writes nothing.
    """
    for func in (
        service.configure_series,
        service.allocate,
        service.advance_to_at_least,
    ):
        source = inspect.getsource(func)
        if func is service.allocate:
            # allocate() delegates to _do_allocate inside the kernel ledger.
            source = inspect.getsource(service._do_allocate)
        assert (
            "lock=True" in source
        ), f"{func.__name__} reads the series without locking it"


def test_the_freeze_is_also_a_database_trigger() -> None:
    """The service refuses first and explains better, but the online roles can
    write the configuration tables directly."""
    migration = MIGRATION.read_text(encoding="utf-8")
    assert "identity_freeze" in migration
    assert "BEFORE UPDATE ON mod_numbering.number_series" in migration
    assert "BEFORE UPDATE ON mod_numbering.platform_number_series" in migration
    # start_value is a prospective seed, so the trigger must not freeze it.
    assert "start_value" not in migration.split("_IDENTITY_COLUMNS")[1].split(")")[0]


def test_the_migration_mirrors_the_critical_validation_in_check_constraints() -> None:
    """The online roles can write the configuration tables directly, so a rule
    that lives only in Python is one a psql session walks straight past."""
    migration = MIGRATION.read_text(encoding="utf-8")
    for check in (
        "min_digits BETWEEN 1 AND 18",
        "year_digits IN (2, 4)",
        "start_value >= 1",
        "reset_policy IN ('never', 'yearly', 'monthly')",
        "reset_policy <> 'yearly' OR include_year = 1",
        "reset_policy <> 'monthly' OR (include_year = 1 AND include_month = 1)",
        "next_value >= 1",
    ):
        assert check in migration, f"missing CHECK: {check}"


def test_append_only_tables_carry_no_updated_at() -> None:
    for model in (
        models.AllocationReceipt,
        models.SeriesRepair,
        models.PlatformAllocationReceipt,
        models.PlatformSeriesRepair,
    ):
        assert (
            "updated_at" not in model.__table__.c
        ), f"{model.__name__} is append-only; an updated_at there is dead or a lie"
        # One domain timestamp per evidence row, not a second redundant one.
        assert "created_at" not in model.__table__.c, (
            f"{model.__name__} carries created_at beside its domain instant; "
            "two timestamps for one event invite them to disagree"
        )


def test_the_migration_makes_append_only_structural() -> None:
    """Grants and a trigger, not a service promise."""
    migration = MIGRATION.read_text(encoding="utf-8")
    assert "BEFORE UPDATE OR DELETE" in migration
    assert "refuse_mutation" in migration
    # The grant is emitted from a template over the append-only tuples, so
    # assert the template and the membership rather than a rendered string
    # that never appears in the source.
    assert "GRANT SELECT, INSERT ON mod_numbering.{table}" in migration
    for group in ("_TENANT_IMMUTABLE", "_PLATFORM_IMMUTABLE"):
        assert group in migration
    for table in models.IMMUTABLE_TABLES:
        assert (
            f'"{table}"' in migration
        ), f"{table} is declared append-only but the migration never names it"


def test_configuration_and_repair_are_typed_commands() -> None:
    """A consumer must not have to write module tables directly."""
    assert hasattr(service, "configure_series")
    assert hasattr(service, "SeriesConfiguration")
    repair = inspect.signature(service.advance_to_at_least).parameters
    for required in ("period_key", "reason", "repaired_by"):
        assert required in repair, f"repair cannot omit {required}"


def test_no_path_lowers_a_counter_or_deletes_a_receipt() -> None:
    """Repair is advance-only. ERP's `reset_sequence` rewinds and rewrites, and
    that is how a committed number gets reissued."""
    source = (MODULE_ROOT / "service.py").read_text(encoding="utf-8")
    assert "db.delete" not in source
    assert ".delete()" not in source
    repair = inspect.getsource(service.advance_to_at_least)
    # The counter only moves when the target is strictly higher.
    assert "changed = target > previous" in repair


def test_the_series_vocabulary_is_open() -> None:
    """`series_code` is a registered string. ERP's `SequenceType` is a
    27-member PostgreSQL enum, so a new document kind is a migration in a
    shared module — ADR-0008 forbids exactly that."""
    source = _module_source()
    assert "class SequenceType" not in source
    for product_term in ("invoice_number", "project_number", "credit_note_number"):
        assert product_term not in source, f"product vocabulary {product_term} leaked"
    assert isinstance(models.NumberSeries.__table__.c["series_code"].type.length, int)


def test_the_module_declares_no_permission_audit_or_setting_vocabulary() -> None:
    """It decides nothing a product would authorise."""
    assert not getattr(module, "permissions", ())
    assert not getattr(module, "audit_actions", ())
    assert not getattr(module, "setting_domains", ())


def test_the_service_never_commits_or_builds_a_session() -> None:
    """Allocation joins the caller's transaction so a failed document does not
    consume a committed number (hard rule 8)."""
    source = (MODULE_ROOT / "service.py").read_text(encoding="utf-8")
    for forbidden in ("db.commit()", "db.rollback()", "sessionmaker", "create_engine"):
        assert forbidden not in source, forbidden


def test_importing_the_package_never_builds_a_database_engine() -> None:
    source = _module_source()
    assert "create_engine" not in source


# ── Release registration ────────────────────────────────────────────────────


def test_the_release_entry_matches_the_allocation_it_publishes() -> None:
    entry = json.loads(
        (REPO_ROOT / ".github/release-modules.json").read_text(encoding="utf-8")
    )["modules"]["dotmac-numbering"]
    assert entry["db_schema"] == models.SCHEMA
    assert entry["import_name"] == "dotmac_numbering"
    assert entry["kernel_floor"] == "0.1.0a65"
    assert MIGRATION.name in " ".join(entry["wheel_contents"]["required"])


def test_the_pinned_kernel_floor_is_the_release_that_allocated_the_schema() -> None:
    manifest = tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text("utf-8"))
    assert manifest["tool"]["poetry"]["dependencies"]["dotmac-kernel"] == ">=0.1.0a65"


def test_the_dossier_records_no_adoption_yet() -> None:
    """Complete is not adopted (ADR-0030 § 2). A green suite is not a cutover."""
    dossier = tomllib.loads((PACKAGE_ROOT / "EXTRACTION.toml").read_text("utf-8"))
    assert dossier["contract_consumers"] == []
    assert dossier["candidate_consumers"]
