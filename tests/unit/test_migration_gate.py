"""The composed migration gate (ADR-0006 D1, item 6).

Every rejection D1 names gets its own test, built from real revision files
written into a tmp version location — the gate is a static scanner, so a
fixture directory exercises exactly the production code path with no mocking.

Two rules this file holds itself to:

- **The reference assembly must PASS.** `test_the_real_repo_composes` runs the
  gate over this repo's own `alembic.ini`. A gate that rejects the existing
  composition is not a gate, it is a bug.
- **Every rejection must be red-sensitive.** Each test asserts on the specific
  violation text, so neutering the corresponding production check turns that
  test red rather than leaving it vacuously green. The neutering runs are
  recorded in the D1 commit message.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from dotmac_kernel.migrations.gate import (
    run_gate,
    scan_revision_file,
    version_locations_from_ini,
)
from dotmac_kernel.modules import ModuleManifest
from dotmac_kernel.namespaces import (
    HOST_MIGRATION_OWNERS,
    DuplicateMigrationPrefixError,
    DuplicateSchemaError,
    MigrationOwner,
    NamespaceRegistry,
    module_schema,
)

from app.assembly import assembly

REPO_ROOT = Path(__file__).resolve().parents[2]

BILLING_OWNER = MigrationOwner(
    owner="billing",
    prefix="bl",
    branch_label="billing",
    db_schema=module_schema("bill"),
)
LEDGER = (*HOST_MIGRATION_OWNERS, BILLING_OWNER)

BILLING_MANIFEST = ModuleManifest(
    code="billing",
    version="1.0.0",
    short_code="bill",
    migration_prefix="bl",
    migration_branch="billing",
    tables=("invoices",),
)


def _write(
    location: Path,
    name: str,
    *,
    revision: str,
    down_revision: str | None = None,
    branch_labels: tuple[str, ...] | None = None,
    depends_on: str | None = None,
    body: str = "    pass",
    extra: str = "",
) -> Path:
    location.mkdir(parents=True, exist_ok=True)
    path = location / f"{name}.py"
    path.write_text(
        "from alembic import op\n\n"
        f"revision = {revision!r}\n"
        f"down_revision = {down_revision!r}\n"
        f"branch_labels = {branch_labels!r}\n"
        f"depends_on = {depends_on!r}\n\n\n"
        "def upgrade() -> None:\n"
        f"{body}\n\n\n"
        "def downgrade() -> None:\n"
        "    pass\n"
        f"{extra}",
        encoding="utf-8",
    )
    return path


def _billing_root(location: Path, **kwargs: object) -> Path:
    defaults: dict[str, object] = {
        "revision": "bl_0001_invoices",
        "down_revision": None,
        "branch_labels": ("billing",),
        "body": '    op.create_table("invoices", schema="mod_bill")',
    }
    defaults.update(kwargs)
    return _write(location, "bl_0001_invoices", **defaults)  # type: ignore[arg-type]


def _gate(*locations: Path):
    return run_gate(
        [BILLING_MANIFEST],
        list(locations),
        registry=NamespaceRegistry.from_manifests([BILLING_MANIFEST], ledger=LEDGER),
    )


def _messages(report) -> str:
    return "\n".join(report.violations)


# ── The reference assembly passes ───────────────────────────────────────────


def test_the_real_repo_composes() -> None:
    """The repo's OWN composition — kernel (`0001`…`0020`), assembly
    (`a001`…`a003`), and the first installed MODULE lineage (`ts_0001`,
    `ts_0002`) — must pass. The two host lineages are grandfathered (legacy
    revision-id format, tables in `public`); `template_studio` gets the strict
    rules.

    Gated from `assembly.modules`, the composition the app actually boots, NOT
    from `load_manifests(FEATURE_MODULES)`: that narrower set omits installed
    modules, so a module's branch label would be unattributable and the gate
    would be checking something other than what ships."""
    locations = version_locations_from_ini(REPO_ROOT / "alembic.ini")
    report = run_gate(assembly.modules, locations)
    assert report.ok, report.render()
    # Non-vacuity: a gate that walked an empty set would pass silently. Bump
    # this deliberately when a lineage gains a revision.
    assert len(report.revisions) == 27
    owners = {a["owner"] for a in report.attribution.values()}
    assert owners == {"kernel", "assembly", "template_studio", "ticketing"}


def test_every_real_revision_is_attributed_to_exactly_one_owner() -> None:
    """D1 item 5: `alembic_version` stays the truth, and every row in it is
    explainable through manifest-to-branch attribution."""
    locations = version_locations_from_ini(REPO_ROOT / "alembic.ini")
    report = run_gate(assembly.modules, locations)
    assert report.attribution["0001_initial_tenant_schema"]["owner"] == "kernel"
    assert report.attribution["a001_adopt_cfd"]["owner"] == "assembly"
    assert report.attribution["ts_0001_templates"]["owner"] == "template_studio"
    assert all(a["owner"] is not None for a in report.attribution.values())


def test_a_clean_module_lineage_composes(tmp_path: Path) -> None:
    location = tmp_path / "billing"
    _billing_root(location)
    _write(
        location,
        "bl_0002_lines",
        revision="bl_0002_lines",
        down_revision="bl_0001_invoices",
        body='    op.add_column("invoices", None, schema="mod_bill")',
    )
    report = _gate(location)
    assert report.ok, report.render()
    assert report.attribution["bl_0002_lines"]["db_schema"] == "mod_bill"


# ── Rejection 1: duplicate revision id ──────────────────────────────────────


def test_rejects_a_duplicate_revision_id(tmp_path: Path) -> None:
    location = tmp_path / "billing"
    _billing_root(location)
    _write(
        location,
        "bl_0002_a",
        revision="bl_0002_lines",
        down_revision="bl_0001_invoices",
    )
    _write(
        location,
        "bl_0002_b",
        revision="bl_0002_lines",
        down_revision="bl_0001_invoices",
    )
    report = _gate(location)
    assert not report.ok
    assert "duplicate revision id 'bl_0002_lines'" in _messages(report)


# ── Rejection 2: duplicate / unregistered migration prefix ──────────────────


def test_rejects_a_revision_id_outside_its_owners_prefix(tmp_path: Path) -> None:
    location = tmp_path / "billing"
    _billing_root(location)
    _write(
        location,
        "zz_0002_lines",
        revision="zz_0002_lines",
        down_revision="bl_0001_invoices",
    )
    report = _gate(location)
    assert not report.ok
    assert "does not match <bl>_<sequence>_<slug>" in _messages(report)


def test_rejects_a_duplicate_prefix(tmp_path: Path) -> None:
    """Two owners, one prefix: the composition is refused on the declarations
    alone, before any revision file is read."""
    other = ModuleManifest(
        code="ledger",
        version="1.0.0",
        short_code="ledg",
        migration_prefix="bl",
        migration_branch="ledger",
    )
    ledger = (
        *LEDGER,
        MigrationOwner("ledger", "bl", "ledger", module_schema("ledg")),
    )
    with pytest.raises(DuplicateMigrationPrefixError) as exc:
        NamespaceRegistry.from_manifests([BILLING_MANIFEST, other], ledger=ledger)
    assert "migration prefix 'bl'" in str(exc.value)


def test_the_gate_reports_a_namespace_fault_instead_of_raising(
    tmp_path: Path,
) -> None:
    """`run_gate` never raises for a composition problem — an operator should
    see every fault in one CI run, not one per run."""
    location = tmp_path / "billing"
    _billing_root(location)
    report = run_gate([BILLING_MANIFEST], [location])  # unallocated in the ledger
    assert not report.ok
    assert "namespace composition:" in _messages(report)
    assert "MIGRATION_OWNER_LEDGER" in _messages(report)


# ── Rejection 3: duplicate / foreign branch label ───────────────────────────


def test_rejects_an_unregistered_branch_label(tmp_path: Path) -> None:
    location = tmp_path / "billing"
    _billing_root(location, branch_labels=("not_allocated",))
    report = _gate(location)
    assert not report.ok
    assert "branch label 'not_allocated' is not registered" in _messages(report)


def test_rejects_a_revision_relabelling_another_owners_branch(
    tmp_path: Path,
) -> None:
    location = tmp_path / "billing"
    _billing_root(location)
    _write(
        location,
        "bl_0002_lines",
        revision="bl_0002_lines",
        down_revision="bl_0001_invoices",
        branch_labels=("kernel",),
    )
    report = _gate(location)
    assert not report.ok
    assert "may not relabel another owner's branch" in _messages(report)


def test_rejects_two_lineage_roots_in_one_location(tmp_path: Path) -> None:
    location = tmp_path / "billing"
    _billing_root(location)
    _write(
        location,
        "bl_0002_lines",
        revision="bl_0002_lines",
        down_revision=None,
        branch_labels=("billing",),
    )
    report = _gate(location)
    assert not report.ok
    assert "expected exactly ONE lineage root" in _messages(report)


# ── Rejection 4: duplicate schema claim ─────────────────────────────────────


def test_rejects_two_modules_claiming_one_schema(tmp_path: Path) -> None:
    twin = ModuleManifest(
        code="ledger",
        version="1.0.0",
        short_code="bill",
        migration_prefix="lg",
        migration_branch="ledger",
    )
    ledger = (
        *LEDGER,
        MigrationOwner("ledger", "lg", "ledger", module_schema("bill")),
    )
    with pytest.raises(DuplicateSchemaError) as exc:
        NamespaceRegistry.from_manifests([BILLING_MANIFEST, twin], ledger=ledger)
    assert "Postgres schema 'mod_bill'" in str(exc.value)


def test_rejects_a_module_creating_outside_its_own_schema(tmp_path: Path) -> None:
    location = tmp_path / "billing"
    _billing_root(location, body='    op.create_table("invoices", schema="mod_other")')
    report = _gate(location)
    assert not report.ok
    assert "outside its own namespace 'mod_bill'" in _messages(report)


# ── Rejection 5: duplicate table ownership ──────────────────────────────────


def test_rejects_duplicate_table_ownership(tmp_path: Path) -> None:
    """The F0 collision class: two OWNERS creating one qualified table. Same
    name, different shape, fails quietly — an `add_column` mutates the other
    module's table."""
    billing = tmp_path / "billing"
    _billing_root(billing)
    other = tmp_path / "other"
    _write(
        other,
        "0001_root",
        revision="0001_root",
        down_revision=None,
        branch_labels=("kernel",),
        body='    op.create_table("invoices", schema="mod_bill")',
    )
    report = _gate(billing, other)
    assert not report.ok
    assert "duplicate table ownership: mod_bill.invoices" in _messages(report)


def test_rejects_a_table_the_manifest_does_not_declare(tmp_path: Path) -> None:
    location = tmp_path / "billing"
    _billing_root(location)
    _write(
        location,
        "bl_0002_lines",
        revision="bl_0002_lines",
        down_revision="bl_0001_invoices",
        body='    op.create_table("undeclared", schema="mod_bill")',
    )
    report = _gate(location)
    assert not report.ok
    assert "its manifest's `tables` does not declare" in _messages(report)


def test_an_empty_table_declaration_does_not_disable_ownership_checks(
    tmp_path: Path,
) -> None:
    """An empty declaration means the module owns no tables; it is not an
    allow-all switch for whatever its migrations happen to create."""
    manifest = ModuleManifest(
        code="billing",
        version="1.0.0",
        short_code="bill",
        migration_prefix="bl",
        migration_branch="billing",
    )
    location = tmp_path / "billing"
    _billing_root(location)
    report = run_gate(
        [manifest],
        [location],
        registry=NamespaceRegistry.from_manifests([manifest], ledger=LEDGER),
    )
    assert not report.ok
    assert "its manifest's `tables` does not declare" in _messages(report)


# ── Lineage, qualification and length rules stated alongside them ───────────


def test_rejects_a_cross_lineage_down_revision(tmp_path: Path) -> None:
    """D1 item 4: cross-lineage ordering uses `depends_on`, never
    `down_revision`."""
    billing = tmp_path / "billing"
    _billing_root(billing)
    other = tmp_path / "other"
    _write(
        other,
        "0001_root",
        revision="0001_root",
        down_revision=None,
        branch_labels=("kernel",),
    )
    _write(
        billing,
        "bl_0002_lines",
        revision="bl_0002_lines",
        down_revision="0001_root",
    )
    report = _gate(billing, other)
    assert not report.ok
    assert "cross-lineage ordering uses `depends_on`" in _messages(report)


def test_accepts_a_cross_lineage_depends_on(tmp_path: Path) -> None:
    billing = tmp_path / "billing"
    other = tmp_path / "other"
    _write(
        other,
        "0001_root",
        revision="0001_root",
        down_revision=None,
        branch_labels=("kernel",),
    )
    _billing_root(billing, depends_on="0001_root")
    report = _gate(billing, other)
    assert report.ok, report.render()


def test_rejects_a_revision_id_over_the_alembic_column_length(
    tmp_path: Path,
) -> None:
    location = tmp_path / "billing"
    long_id = "bl_0001_" + "x" * 40
    _billing_root(location, revision=long_id)
    report = _gate(location)
    assert not report.ok
    assert "VARCHAR(32)" in _messages(report)


def test_rejects_module_ddl_that_depends_on_search_path(tmp_path: Path) -> None:
    location = tmp_path / "billing"
    _billing_root(location, body='    op.create_table("invoices")')
    report = _gate(location)
    assert not report.ok
    assert "omits `schema=`" in _messages(report)
    assert "never depend on `search_path`" in _messages(report)


def test_rejects_module_ddl_hidden_in_a_local_helper(tmp_path: Path) -> None:
    """Putting DDL behind an upgrade helper must not bypass the AST gate."""
    location = tmp_path / "billing"
    _billing_root(
        location,
        body="    _create_invoices()",
        extra=(
            "\n\ndef _create_invoices() -> None:\n" '    op.create_table("invoices")\n'
        ),
    )
    report = _gate(location)
    assert not report.ok
    assert "omits `schema=`" in _messages(report)


def test_rejects_module_ddl_targeting_another_schema(tmp_path: Path) -> None:
    """Qualification alone is insufficient: mutating another owner's schema
    is exactly the cross-module writer path D1 closes."""
    location = tmp_path / "billing"
    _billing_root(location)
    _write(
        location,
        "bl_0002_lines",
        revision="bl_0002_lines",
        down_revision="bl_0001_invoices",
        body='    op.add_column("invoices", None, schema="mod_other")',
    )
    report = _gate(location)
    assert not report.ok
    assert "targets schema 'mod_other' outside its own namespace" in _messages(report)


def test_accepts_constants_and_local_helpers_for_schema_qualified_ddl(
    tmp_path: Path,
) -> None:
    """The safe helpers D1 publishes must also be statically understandable;
    otherwise the enforcement would reject its own recommended path."""
    location = tmp_path / "billing"
    _billing_root(
        location,
        body="    _create_invoices()",
        extra=(
            '\n\nSCHEMA = module_schema("bill")\n\n'
            "def _create_invoices() -> None:\n"
            '    op.create_table("invoices", schema=SCHEMA)\n'
        ),
    )
    report = _gate(location)
    assert report.ok, report.render()


def test_a_fully_qualified_foreign_key_is_accepted(tmp_path: Path) -> None:
    location = tmp_path / "billing"
    _billing_root(location)
    _write(
        location,
        "bl_0002_lines",
        revision="bl_0002_lines",
        down_revision="bl_0001_invoices",
        body=(
            '    op.create_foreign_key("fk_lines_invoice", "invoice_lines", '
            '"invoices", ["invoice_id"], ["id"], '
            'source_schema="mod_bill", referent_schema="mod_bill")'
        ),
    )
    report = _gate(location)
    assert report.ok, report.render()


def test_rejects_an_unqualified_inline_foreign_key(tmp_path: Path) -> None:
    """A schema on `op.create_table` does not qualify the SQLAlchemy FK target;
    the referent must carry its schema too."""
    location = tmp_path / "billing"
    _billing_root(
        location,
        body=(
            '    op.create_table("invoices", '
            'sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]), '
            'schema="mod_bill")'
        ),
    )
    report = _gate(location)
    assert not report.ok
    assert "ForeignKeyConstraint(referent)" in _messages(report)


def test_an_annotated_alembic_revision_is_scanned(tmp_path: Path) -> None:
    """Current Alembic templates type-annotate revision metadata. The gate
    must attribute those files rather than reporting a misleading empty dir."""
    location = tmp_path / "billing"
    location.mkdir()
    path = location / "bl_0001_invoices.py"
    path.write_text(
        "revision: str = 'bl_0001_invoices'\n"
        "down_revision: str | None = None\n"
        "branch_labels: tuple[str, ...] = ('billing',)\n"
        "depends_on: str | None = None\n\n"
        "def upgrade() -> None:\n"
        "    op.create_table('invoices', schema='mod_bill')\n",
        encoding="utf-8",
    )
    record = scan_revision_file(path, location)
    assert record is not None
    assert record.revision == "bl_0001_invoices"
    assert _gate(location).ok


def test_rejects_module_raw_sql_that_never_names_its_schema(
    tmp_path: Path,
) -> None:
    location = tmp_path / "billing"
    _billing_root(
        location,
        body=(
            '    op.create_table("invoices", schema="mod_bill")\n'
            '    op.execute("ALTER TABLE invoices ENABLE ROW LEVEL SECURITY")'
        ),
    )
    report = _gate(location)
    assert not report.ok
    assert "raw SQL never names 'mod_bill'" in _messages(report)


def test_accepts_module_raw_sql_that_is_fully_qualified(tmp_path: Path) -> None:
    location = tmp_path / "billing"
    _billing_root(
        location,
        body=(
            '    op.create_table("invoices", schema="mod_bill")\n'
            "    op.execute("
            '"ALTER TABLE mod_bill.invoices ENABLE ROW LEVEL SECURITY")'
        ),
    )
    report = _gate(location)
    assert report.ok, report.render()


def test_rejects_a_stateful_module_that_ships_no_lineage(tmp_path: Path) -> None:
    """A module owning a schema whose migrations are not in any selected
    location: its models would map to tables nothing creates."""
    other = tmp_path / "other"
    _write(
        other,
        "0001_root",
        revision="0001_root",
        down_revision=None,
        branch_labels=("kernel",),
    )
    report = _gate(other)
    assert not report.ok
    assert "no selected version location carries its lineage" in _messages(report)


# ── Scanner behaviour ───────────────────────────────────────────────────────


def test_downgrade_ddl_is_not_an_ownership_claim(tmp_path: Path) -> None:
    """`downgrade()` legitimately re-creates what `upgrade()` replaced (the
    kernel's own `0003_party_identity` rebuilds the pre-Party tables). Only
    `upgrade()` establishes ownership."""
    path = tmp_path / "0001_x.py"
    path.write_text(
        "from alembic import op\n\n"
        "revision = '0001_x'\ndown_revision = None\n"
        "branch_labels = ('kernel',)\ndepends_on = None\n\n\n"
        "def upgrade() -> None:\n    op.create_table('new_one')\n\n\n"
        "def downgrade() -> None:\n    op.create_table('old_one')\n",
        encoding="utf-8",
    )
    record = scan_revision_file(path, tmp_path)
    assert record is not None
    assert record.creates_tables == ("public.new_one",)


def test_a_helper_module_in_a_version_location_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "helpers.py"
    path.write_text("VALUE = 1\n", encoding="utf-8")
    assert scan_revision_file(path, tmp_path) is None


def test_version_locations_are_read_from_the_alembic_config() -> None:
    """The gate reads the config the deployment actually uses — a location
    added to `alembic.ini` is automatically one the gate must attribute."""
    locations = version_locations_from_ini(REPO_ROOT / "alembic.ini")
    assert len(locations) == 4
    assert all(location.is_dir() for location in locations)


def test_a_missing_version_location_is_a_violation(tmp_path: Path) -> None:
    report = _gate(tmp_path / "does-not-exist")
    assert not report.ok
    assert "does not exist" in _messages(report)
