"""D1 namespace/lineage identity (ADR-0006 amendment, `dotmac_kernel.namespaces`).

Covers the four things D1 makes structural — the `mod_` schema form, the
`public` closure, the immutable ledger, and the revision-id budget — plus every
duplicate-claim rejection `NamespaceRegistry` owns. The composed CI gate's
rejections live in `test_migration_gate.py`; this file is the declaration side.
"""

from __future__ import annotations

import dataclasses
import tomllib
from pathlib import Path

import pytest
from dotmac_kernel.features import FeatureManifest, load_manifests
from dotmac_kernel.modules import ModuleManifest, ModuleRegistry, ModuleRegistryError
from dotmac_kernel.namespaces import (
    ASSEMBLY_MIGRATION_OWNER,
    HOST_MIGRATION_OWNERS,
    HOST_SCHEMA,
    KERNEL_MIGRATION_OWNER,
    MAX_IDENTIFIER_LENGTH,
    MAX_REVISION_ID_LENGTH,
    MIGRATION_OWNER_LEDGER,
    DuplicateBranchLabelError,
    DuplicateMigrationPrefixError,
    DuplicateSchemaError,
    DuplicateTableOwnerError,
    HostSchemaClaimError,
    InvalidRevisionIdError,
    InvalidSchemaError,
    MigrationOwner,
    NamespaceAllocationError,
    NamespaceRegistry,
    UnallocatedNamespaceError,
    module_schema,
    qualified,
    revision_id,
    revision_id_pattern,
    schema_table_args,
    validate_schema,
)

from app.features import FEATURE_MODULES
from scripts.check_allocation_serialized import (
    EXEMPT_CLASSIFICATIONS,
    GATED_CLASSIFICATIONS,
    GateError,
    declared_allocation,
)


def _module(
    code: str = "billing",
    *,
    short_code: str = "bill",
    prefix: str = "bl",
    branch: str | None = None,
    tables: tuple[str, ...] = (),
    platform_tables: tuple[str, ...] = (),
) -> ModuleManifest:
    return ModuleManifest(
        code=code,
        version="1.0.0",
        short_code=short_code,
        migration_prefix=prefix,
        migration_branch=branch,
        tables=tables,
        platform_tables=platform_tables,
    )


def _ledger(*owners: MigrationOwner) -> tuple[MigrationOwner, ...]:
    return (*HOST_MIGRATION_OWNERS, *owners)


# ── Schema form ─────────────────────────────────────────────────────────────


def test_module_schema_is_the_mod_form_and_nothing_else() -> None:
    assert module_schema("bill") == "mod_bill"
    assert qualified("mod_bill", "invoices") == "mod_bill.invoices"


def test_db_schema_is_derived_and_read_only() -> None:
    """There is no settable schema attribute — the namespace cannot be
    re-pointed at runtime, which is half of what 'immutable' means."""
    manifest = _module()
    assert manifest.db_schema == "mod_bill"
    with pytest.raises(AttributeError):
        manifest.db_schema = "mod_other"  # type: ignore[misc]
    # ...and the declaration it derives from is frozen too.
    with pytest.raises(dataclasses.FrozenInstanceError):
        manifest.short_code = "other"  # type: ignore[misc]


def test_public_is_closed_to_installable_modules() -> None:
    with pytest.raises(HostSchemaClaimError) as exc:
        MigrationOwner(owner="billing", prefix="bl", branch_label="billing")
    assert HOST_SCHEMA in str(exc.value)


def test_a_module_may_not_claim_a_reserved_or_non_mod_schema() -> None:
    for schema in ("public", "pg_catalog", "information_schema", "billing"):
        with pytest.raises((InvalidSchemaError, HostSchemaClaimError)):
            MigrationOwner(
                owner="billing",
                prefix="bl",
                branch_label="billing",
                db_schema=schema,
            )


def test_an_over_long_schema_is_rejected_rather_than_truncated() -> None:
    """Postgres truncates identifiers at 63 bytes SILENTLY, and a truncated
    schema is a schema that collides."""
    with pytest.raises(InvalidSchemaError) as exc:
        validate_schema("mod_" + "b" * MAX_IDENTIFIER_LENGTH)
    assert str(MAX_IDENTIFIER_LENGTH) in str(exc.value)
    # The short-code budget stops it long before that anyway.
    with pytest.raises(InvalidSchemaError):
        module_schema("b" * (MAX_IDENTIFIER_LENGTH + 5))


def test_schema_table_args_binds_a_model_to_its_namespace() -> None:
    assert schema_table_args("mod_bill") == {"schema": "mod_bill"}
    with pytest.raises(HostSchemaClaimError):
        schema_table_args(HOST_SCHEMA)


# ── Revision-id budget ──────────────────────────────────────────────────────


def test_revision_id_format_and_the_verified_32_char_alembic_limit() -> None:
    """`alembic_version.version_num` is `String(32)` (alembic/ddl/impl.py). An
    over-long id fails at `alembic upgrade`, against a real database — so it
    must fail HERE instead."""
    assert MAX_REVISION_ID_LENGTH == 32
    assert revision_id("bl", 1, "invoice_lines") == "bl_0001_invoice_lines"
    assert len("bl_0001_invoice_lines") <= MAX_REVISION_ID_LENGTH
    with pytest.raises(InvalidRevisionIdError) as exc:
        revision_id("bl", 1, "a_very_long_slug_that_will_not_fit_at_all")
    assert "VARCHAR(32)" in str(exc.value)


def test_revision_id_rejects_a_bad_slug_or_sequence() -> None:
    with pytest.raises(InvalidRevisionIdError):
        revision_id("bl", 1, "Not-A-Slug")
    with pytest.raises(InvalidRevisionIdError):
        revision_id("bl", 99999, "x")


def test_revision_id_pattern_matches_only_its_own_lineage() -> None:
    pattern = revision_id_pattern("bl")
    assert pattern.match("bl_0001_invoices")
    assert not pattern.match("xx_0001_invoices")
    assert not pattern.match("bl_1_invoices")


# ── Ledger allocation = where immutability is enforced ──────────────────────


def test_the_shipped_ledger_is_the_host_owners_plus_allocated_modules() -> None:
    """The two grandfathered host owners, then every allocated module.

    Was "the two host owners" until `template_studio` became the first
    installable allocation (ADR-0006 M1). The host owners keep their properties
    — legacy revision ids, tables in `public`, no module schema — and a module
    row is the inverse of all three, which is the distinction the ledger exists
    to make.
    """
    assert MIGRATION_OWNER_LEDGER[:2] == (
        KERNEL_MIGRATION_OWNER,
        ASSEMBLY_MIGRATION_OWNER,
    )
    assert KERNEL_MIGRATION_OWNER.is_legacy and ASSEMBLY_MIGRATION_OWNER.is_legacy
    host, modules = MIGRATION_OWNER_LEDGER[:2], MIGRATION_OWNER_LEDGER[2:]
    assert all(owner.db_schema is None for owner in host)
    # Every allocated module owns a real `mod_` namespace and gets the strict
    # (non-legacy) revision-id rules.
    assert modules, "no installable module is allocated — expected template_studio"
    for owner in modules:
        assert owner.db_schema is not None
        assert owner.db_schema.startswith("mod_")
        assert not owner.is_legacy
    # Pinned so that adding an allocation is a visible, argued diff rather than
    # something that arrives unnoticed with a module. `application_directory`
    # (ADR-0021) is the fifth, and the first allocated for an assembly other
    # than this repository's — its consumer is the Tenant Workspace.
    # `files` is the sixth, allocated to the optional byte-lifecycle owner in
    # ADR-0022, and `imports` the seventh, to the bulk-import run ledger in
    # ADR-0025. `approvals` (ADR-0026) is the ninth, and the first allocated for
    # a capability that is real in BOTH planes in production — ERP approves
    # back-office subjects for a tenant, the vendor control plane approves fleet
    # plans with no tenant at all. `numbering` (ADR-0030) is the tenth, the
    # first enabling owner of the Cloud commerce programme, and dual-plane for
    # the same reason approvals is: a tenant allocates its own document series
    # and the control plane allocates vendor-side series no tenant may read.
    # `people` is the eleventh: a tenant-only employment directory that links
    # to, and never duplicates, the kernel Party-person catalogue.
    # `campaigns` (ADR-0032) is the twelfth and tenant-only because the source
    # audit found no real named platform consumer.
    # `durable_timers` is the thirteenth and reuses the kernel outbox relay rather
    # than adding another due-work engine. `commercial_agreements` (ADR-0057) is
    # the fourteenth: platform-only, because the vendor control plane is the one
    # consumer that exists today and no tenant data plane holds a vendor<->operator
    # agreement. `licensing` (ADR-0057 § 2) is the fifteenth, and its plane is a
    # SECURITY boundary rather than an absent consumer — issuance must not live
    # inside the deployment it authorises, and the receiving half already verifies
    # offline through `dotmac_kernel.licensing`. `deployment_control`
    # (ADR-0057 § 3) is the sixteenth, platform-only for a reason close to
    # tautological: a module that decides what a FLEET should run cannot live
    # inside one of the deployments it decides about. `brand_profiles`
    # (ADR-0057 § 2) is the seventeenth and genuinely dual-plane: Sub brands
    # its own portals and the vendor brands deployments it ships, and the
    # second needs a HOST binding because a profile must be selectable before
    # any tenant is resolved. `media_observations`, `content`, `publishing` and
    # `sites` are the eighteenth through twenty-first allocations. They remain
    # tenant-only and keep observations, editorial state, release intent and
    # local website composition as four independent owners.
    # `inventory` and `assets` are the twenty-second and twenty-third: the
    # tenant stock ledger (ADR-0036) and the tenant durable-unit lifecycle
    # (ADR-0037), deliberately separate because a part in stock and a
    # commissioned unit have different lifecycles and different writers.
    # The nine network-suite-v1 allocations (ADR-0038) are the twenty-fourth
    # through thirty-second. They are minted together because their first
    # adoption is one Sub-first cohort, but each owns an independent lineage —
    # a suite is a release cohort, not a shared namespace.
    # `positioning` is the thirty-third: provider-neutral position observations
    # and nothing that follows from them (ADR-0039). It is NOT part of the
    # network cohort above — Assets keeps a durable unit's authoritative
    # location, so the two are separate owners rather than one.
    # `referrals` is the thirty-fourth and tenant-only: a tenant programme owns
    # attribution and conversion evidence while rewards leave through the
    # outbox for the product that owns fulfilment.
    # `reseller_management` is the thirty-fifth and tenant-only: Sub's reseller
    # hierarchy and delegated-authority lifecycle move here while Party,
    # Customer, commissions and payouts keep their own owners.
    # The consolidated ERP/Backoffice/general cohort is the thirty-sixth
    # through fifty-first allocations:
    # sixteen independently releasable tenant owners sharing one allocation
    # release, with Documents and Party deliberately avoiding the already-owned
    # `dc` and `pa` prefixes.
    # `web_analytics` is the fifty-second: privacy-minimised first-party
    # observations and deterministic projections (ADR-0055). Property, origin,
    # consent and consequence policy remains with each adopting product.
    # ADR-0055's ten ISP essential owners follow. `services` takes `se` because
    # Surveys already owns `sv`, and `service_access_policy` takes the `sap`
    # settled by the three-way `sa` arbitration above rather than the `sa` its
    # own branch had claimed while unable to see Sales.
    # The four customer-journey owners (Sub PR #2624 audit, 2026-08-22) close
    # the set: service delivery readiness, payment intent, the durable change
    # request, and escalation policy. Each is narrower than the journey it
    # closes — the journey crosses owners; the module owns one decision in it.
    # None of these allocations installs behaviour in the kernel.
    assert {owner.owner for owner in modules} == {
        "template_studio",
        "ticketing",
        "release_catalog",
        "entitlement_allocation",
        "application_directory",
        "numbering",
        "files",
        "imports",
        "integration",
        "approvals",
        "people",
        "campaigns",
        "durable_timers",
        "commercial_agreements",
        "licensing",
        "deployment_control",
        "brand_profiles",
        "media_observations",
        "content",
        "publishing",
        "sites",
        "inventory",
        "assets",
        "ipam",
        "network_inventory",
        "network_observability",
        "network_topology",
        "network_assurance",
        "network_control",
        "fiber_plant",
        "network_access",
        "pon_access",
        "positioning",
        "referrals",
        "reseller_management",
        "accounting",
        "analytics",
        "banking",
        "documents",
        "expenses",
        "finance",
        "inbox",
        "party",
        "payables",
        "payroll",
        "procurement",
        "projects",
        "records",
        "surveys",
        "tax",
        "work_orders",
        "web_analytics",
        "fulfillment",
        # The `sa` arbitration: three unmerged candidate trains had each
        # allocated `prefix="sa"` independently, none able to see the others.
        # These three rows are DORMANT (no package on main declares them) and
        # exist to settle the prefix in the canonical ledger before any train
        # merges, while all three are unreleased and a rename is free.
        "sales",
        "support_access",
        "service_access_policy",
        # The ADR-0040 composable-unit cohort. Support Access already has its
        # row from the `sa` arbitration above; these six complete the seven, so
        # the whole cohort composes against one ledger and shares one floor.
        # Allocation only — no module is published, adopted or authoritative
        # because of this.
        "forms",
        "workflow_runtime",
        "platform_health",
        "remote_access",
        "compliance_reporting",
        "ai_operations",
        # The commerce chain: sell -> order -> subscribe -> bill -> collect.
        # `sales` already holds `sa` from the arbitration above.
        "billing",
        "collections",
        "orders",
        "subscriptions",
        "customers",
        "service_catalog",
        "qualification",
        "services",
        "usage",
        "usage_rating",
        "inbox_operations",
        "workforce",
        "fx_policy",
        "service_orders",
        "payments",
        "service_changes",
        "operational_escalations",
    }

    allocations = {
        owner.owner: (owner.db_schema, owner.prefix, owner.branch_label)
        for owner in modules
    }
    assert {
        name: allocations[name]
        for name in (
            "customers",
            "service_catalog",
            "qualification",
            "services",
            "usage",
            "usage_rating",
            "service_access_policy",
            "inbox_operations",
            "workforce",
            "fx_policy",
            "service_orders",
            "payments",
            "service_changes",
            "operational_escalations",
        )
    } == {
        "customers": ("mod_customers", "cu", "customers"),
        "service_catalog": ("mod_svc_cat", "sc", "service_catalog"),
        "qualification": ("mod_qual", "qu", "qualification"),
        "services": ("mod_services", "se", "services"),
        "usage": ("mod_usage", "us", "usage"),
        "usage_rating": ("mod_usage_rate", "ur", "usage_rating"),
        "service_access_policy": (
            "mod_serviceaccess",
            "sap",
            "service_access_policy",
        ),
        "inbox_operations": ("mod_inbox_ops", "io", "inbox_operations"),
        "workforce": ("mod_workforce", "wf", "workforce"),
        "fx_policy": ("mod_fx_policy", "fx", "fx_policy"),
        "service_orders": ("mod_serviceorders", "so", "service_orders"),
        "payments": ("mod_payments", "pm", "payments"),
        "service_changes": ("mod_servicechanges", "sch", "service_changes"),
        "operational_escalations": ("mod_escalations", "oe", "operational_escalations"),
    }


def test_the_shipped_ledger_itself_composes() -> None:
    """A direct assertion on the shipped constant, not an incidental one.

    `NamespaceRegistry.from_manifests` already defaults `ledger` to
    `MIGRATION_OWNER_LEDGER`, and many suites call it that way, so the shipped
    ledger was ALREADY validated as a side effect of unrelated tests. This adds
    no new coverage of a single tree; it makes the property explicit and
    attributable, so a duplicate reports as "the shipped ledger collides"
    rather than as a puzzling failure inside an isolation test that has nothing
    to do with allocation.

    What no in-tree check can do is see another branch — which is the whole
    reason the `sa` collision survived. That is the job of serialized
    allocation, not of this test.
    """
    registry = NamespaceRegistry.from_manifests([], ledger=MIGRATION_OWNER_LEDGER)

    assert registry is not None
    prefixes = [owner.prefix for owner in MIGRATION_OWNER_LEDGER]
    assert len(prefixes) == len(set(prefixes)), "two owners claim one prefix"
    labels = [owner.branch_label for owner in MIGRATION_OWNER_LEDGER]
    assert len(labels) == len(set(labels)), "two owners claim one branch label"
    schemas = [o.db_schema for o in MIGRATION_OWNER_LEDGER if o.db_schema is not None]
    assert len(schemas) == len(set(schemas)), "two owners claim one schema"


def test_the_three_way_sa_arbitration_holds() -> None:
    """`sales` keeps `sa`; the two access modules are separated permanently.

    Named rather than folded into the set-equality pin above because the point
    is not that three rows exist — it is that these three specific owners hold
    three DIFFERENT prefixes, which is exactly what a rebase could quietly undo
    by resurrecting one train's original `sa`.
    """
    allocated = {o.owner: o.prefix for o in MIGRATION_OWNER_LEDGER}

    assert allocated["sales"] == "sa"
    assert allocated["support_access"] == "sup"
    assert allocated["service_access_policy"] == "sap"


def test_every_module_packages_full_allocation_matches_the_ledger() -> None:
    """Current-tree integrity: declaration and ledger agree, in every field.

    This is NOT the serialization gate, and naming it as one was wrong. It sees
    only the tree it runs in, so two parallel branches can each add a colliding
    allocation together with its migrations and both pass. Serialization is a
    merge-base question: `scripts/check_allocation_serialized.py`, proven in
    `tests/architecture/test_allocation_serialized_gate.py`.

    What this establishes is that nothing in THIS tree drifted: a stateful
    module's `short_code`, `migration_prefix` and `migration_branch` must match
    its ledger row's `db_schema`, `prefix` and `branch_label` — the complete
    allocation, not just the prefix. Comparing only code and prefix would miss
    a module that quietly re-pointed its schema or branch label, which are
    equally load-bearing and equally immutable.

    Which packages are gated comes from each dossier's `classification` — the
    same source of truth the merge-base gate uses, and the same parser — rather
    than from a directory name or from "does it have a manifest", both of which
    fail open.
    """
    ledger = {owner.owner: owner for owner in MIGRATION_OWNER_LEDGER}
    by_label = {owner.branch_label: owner for owner in MIGRATION_OWNER_LEDGER}
    packages = Path(__file__).resolve().parents[2] / "packages"

    problems: list[str] = []
    stateful = 0
    for package in sorted(entry for entry in packages.iterdir() if entry.is_dir()):
        dossier = package / "EXTRACTION.toml"
        if not dossier.exists():
            problems.append(f"{package.name}: no EXTRACTION.toml to classify it")
            continue
        classification = tomllib.loads(dossier.read_text()).get("classification")
        if classification in EXEMPT_CLASSIFICATIONS:
            continue
        if classification not in GATED_CLASSIFICATIONS:
            problems.append(
                f"{package.name}: unknown classification {classification!r}"
            )
            continue

        manifests = sorted(package.glob("src/*/manifest.py"))
        revisions = [
            path
            for path in package.glob("src/*/migrations/versions/*.py")
            if path.name != "__init__.py"
        ]
        if len(manifests) > 1:
            problems.append(f"{package.name}: {len(manifests)} manifests")
            continue
        if not manifests:
            # A dossier-only stub with no source yet is a legitimate state; a
            # package owning a lineage without declaring an owner is not.
            if revisions:
                problems.append(
                    f"{package.name}: {len(revisions)} migration(s) but no manifest"
                )
            continue

        try:
            declared = declared_allocation(manifests[0].read_text(), str(manifests[0]))
        except GateError as exc:
            problems.append(f"{package.name}: {exc}")
            continue

        if declared["short_code"] is None and declared["migration_prefix"] is None:
            if revisions:
                problems.append(
                    f"{package.name}: stateless manifest owning "
                    f"{len(revisions)} migration(s)"
                )
            continue

        stateful += 1
        # Resolve by `code`, then `migration_branch` against `branch_label`.
        # The branch label is the immutable lineage identity, so a module whose
        # code was renamed after allocation still resolves to the row it owns
        # rather than looking unallocated and inviting a duplicate row.
        owner = ledger.get(str(declared["code"])) or by_label.get(
            str(declared["migration_branch"])
        )
        if owner is None:
            problems.append(
                f"{package.name}: neither code {declared['code']!r} nor "
                f"migration_branch {declared['migration_branch']!r} is allocated"
            )
            continue
        expected = (
            module_schema(str(declared["short_code"])),
            declared["migration_prefix"],
            declared["migration_branch"],
        )
        actual = (owner.db_schema, owner.prefix, owner.branch_label)
        if expected != actual:
            problems.append(
                f"{declared['code']!r}: declares (schema, prefix, branch) "
                f"{expected!r} but the ledger granted {actual!r}"
            )

    assert stateful, "found no stateful module packages — the scan is broken"
    assert not problems, "; ".join(problems)


# ── The two planes (ADR-0023) ───────────────────────────────────────────────


def test_a_table_declared_in_both_planes_is_rejected() -> None:
    """One table, one plane.

    Declaring both would ask the live-catalog gate to hold the table to two
    opposite isolation contracts at once — tenant-scoped WITH forced RLS and
    control-plane WITHOUT it — and whichever branch ran second would decide,
    silently.
    """
    with pytest.raises(ModuleRegistryError) as exc:
        _module(tables=("tickets",), platform_tables=("tickets",))
    assert "BOTH the tenant and platform planes" in str(exc.value)


def test_declared_tables_is_the_union_and_the_platform_set_is_the_subset() -> None:
    """`declared_tables` stays the full OWNERSHIP set the gate checks in both
    directions; the platform set is only the classification on top of it."""
    owner = MigrationOwner(
        owner="billing", prefix="bl", branch_label="billing", db_schema="mod_bill"
    )
    registry = NamespaceRegistry.from_manifests(
        [_module(tables=("invoices",), platform_tables=("vendor_invoices",))],
        ledger=_ledger(owner),
    )
    assert registry.declared_tables("mod_bill") == frozenset(
        {"invoices", "vendor_invoices"}
    )
    assert registry.declared_platform_tables("mod_bill") == frozenset(
        {"vendor_invoices"}
    )


def test_a_module_with_no_platform_plane_declares_an_empty_set() -> None:
    """Every module shipped before ADR-0023, and most after it. The default must
    be "tenant-only", never "unclassified"."""
    owner = MigrationOwner(
        owner="billing", prefix="bl", branch_label="billing", db_schema="mod_bill"
    )
    registry = NamespaceRegistry.from_manifests(
        [_module(tables=("invoices",))], ledger=_ledger(owner)
    )
    assert registry.declared_platform_tables("mod_bill") == frozenset()


def test_the_registry_also_refuses_a_table_smuggled_into_both_planes() -> None:
    """Defence in depth for the manifest check above.

    `from_manifests` is duck-typed on purpose — it reads `tables` and
    `platform_tables` off anything that answers `migration_owner()`, so an
    object that is not a `ModuleManifest` never runs that validation. Claiming
    both planes in ONE pass is what makes "one table, one plane" hold for those
    too, rather than only for well-behaved manifests.
    """

    class _Smuggler:
        tables = ("entries",)
        platform_tables = ("entries",)

        @staticmethod
        def migration_owner() -> MigrationOwner:
            return MigrationOwner(
                owner="billing",
                prefix="bl",
                branch_label="billing",
                db_schema="mod_bill",
            )

    owner = _Smuggler.migration_owner()
    with pytest.raises(DuplicateTableOwnerError):
        NamespaceRegistry.from_manifests([_Smuggler()], ledger=_ledger(owner))


def test_an_unallocated_module_cannot_own_a_namespace() -> None:
    # An explicitly fictional code, not the shared `_module()` default. That
    # default is `billing`, which meant "unallocated" only until Billing was
    # actually allocated — at which point this test would have handed the
    # registry an ALLOCATED module and stopped proving anything.
    with pytest.raises(UnallocatedNamespaceError) as exc:
        NamespaceRegistry.from_manifests(
            [_module(code="never_allocated", short_code="neverallocated", prefix="zz")]
        )
    assert "MIGRATION_OWNER_LEDGER" in str(exc.value)


def test_a_module_that_repoints_its_allocated_schema_is_rejected() -> None:
    """The immutability check: same module code, different schema than the
    ledger row. A module's schema is where its data physically lives."""
    allocated = MigrationOwner(
        owner="billing",
        prefix="bl",
        branch_label="billing",
        db_schema=module_schema("bill"),
    )
    with pytest.raises(NamespaceAllocationError) as exc:
        NamespaceRegistry.from_manifests(
            [_module(short_code="billing2")], ledger=_ledger(allocated)
        )
    assert "db_schema" in str(exc.value)


def test_a_module_that_repoints_its_allocated_prefix_is_rejected() -> None:
    allocated = MigrationOwner(
        owner="billing",
        prefix="bl",
        branch_label="billing",
        db_schema=module_schema("bill"),
    )
    with pytest.raises(NamespaceAllocationError) as exc:
        NamespaceRegistry.from_manifests(
            [_module(prefix="zz")], ledger=_ledger(allocated)
        )
    assert "prefix" in str(exc.value)


def test_an_allocated_module_registers_cleanly() -> None:
    allocated = MigrationOwner(
        owner="billing",
        prefix="bl",
        branch_label="billing",
        db_schema=module_schema("bill"),
    )
    registry = NamespaceRegistry.from_manifests(
        [_module(tables=("invoices",))], ledger=_ledger(allocated)
    )
    assert registry.module_schemas() == ("mod_bill",)
    assert registry.declared_tables("mod_bill") == frozenset({"invoices"})
    assert registry.owner_for_branch("billing") is allocated
    assert registry.owner_for_branch("kernel") is KERNEL_MIGRATION_OWNER
    assert registry.table_owner("mod_bill", "invoices") == "billing"


def test_the_full_ledger_is_validated_even_when_owners_are_not_installed() -> None:
    """Fleet-wide uniqueness comes from the shipped ledger, not merely the
    subset selected by one deployment. Two dormant allocations may not reserve
    the same prefix and wait until their first joint install to fail."""
    dormant_a = MigrationOwner("dormant_a", "dm", "dormant_a", module_schema("da"))
    dormant_b = MigrationOwner("dormant_b", "dm", "dormant_b", module_schema("db"))
    with pytest.raises(DuplicateMigrationPrefixError):
        NamespaceRegistry.from_manifests([], ledger=_ledger(dormant_a, dormant_b))


# ── Duplicate claims ────────────────────────────────────────────────────────


def test_two_owners_cannot_claim_one_schema() -> None:
    owners = [
        MigrationOwner("a", "aa", "a", module_schema("shared")),
        MigrationOwner("b", "bb", "b", module_schema("shared")),
    ]
    with pytest.raises(DuplicateSchemaError) as exc:
        NamespaceRegistry(owners)
    assert "mod_shared" in str(exc.value)


def test_two_owners_cannot_claim_one_migration_prefix() -> None:
    owners = [
        MigrationOwner("a", "xx", "a", module_schema("one")),
        MigrationOwner("b", "xx", "b", module_schema("two")),
    ]
    with pytest.raises(DuplicateMigrationPrefixError):
        NamespaceRegistry(owners)


def test_two_owners_cannot_claim_one_branch_label() -> None:
    owners = [
        MigrationOwner("a", "aa", "shared", module_schema("one")),
        MigrationOwner("b", "bb", "shared", module_schema("two")),
    ]
    with pytest.raises(DuplicateBranchLabelError):
        NamespaceRegistry(owners)


def test_one_table_cannot_be_claimed_twice() -> None:
    """Defence in depth inside one namespace. The CROSS-owner case — two
    modules creating the same qualified table — is unreachable here by
    construction (distinct schemas are checked first, which is exactly the
    point of D1: schemas make the F0 `parties`/`audit_events` collisions
    impossible rather than merely unlikely), so it is the composed gate that
    catches it from the migration files; see
    `test_migration_gate.py::test_rejects_duplicate_table_ownership`."""
    owner = MigrationOwner("a", "aa", "a", module_schema("one"))
    with pytest.raises(DuplicateTableOwnerError) as exc:
        NamespaceRegistry([owner], tables_by_owner={"a": ("invoices", "invoices")})
    assert "mod_one.invoices" in str(exc.value)


def test_a_schemaless_owner_may_not_claim_tables() -> None:
    with pytest.raises(HostSchemaClaimError):
        NamespaceRegistry(
            HOST_MIGRATION_OWNERS, tables_by_owner={"kernel": ("parties",)}
        )


# ── Manifest shape: stateful or stateless, never half ───────────────────────


def test_a_stateless_manifest_declares_no_namespace() -> None:
    manifest = ModuleManifest(code="web", version="1.0.0")
    assert manifest.db_schema is None
    assert manifest.is_stateful is False
    assert manifest.migration_owner() is None


def test_tables_without_a_short_code_are_rejected() -> None:
    """A half-declaration would put the module's tables in `public`."""
    with pytest.raises(ValueError) as exc:
        ModuleManifest(code="billing", version="1.0.0", tables=("invoices",))
    assert HOST_SCHEMA in str(exc.value)


def test_a_short_code_without_a_prefix_is_rejected() -> None:
    with pytest.raises(ValueError) as exc:
        ModuleManifest(code="billing", version="1.0.0", short_code="bill")
    assert "migration_prefix" in str(exc.value)


def test_a_duplicate_table_within_one_manifest_is_rejected() -> None:
    with pytest.raises(ValueError):
        _module(tables=("invoices", "invoices"))


# ── The reference assembly still validates ──────────────────────────────────


def test_the_reference_assemblys_features_are_all_stateless() -> None:
    """A gate that rejects the existing repo is useless. Every feature this
    assembly ships is a host feature: its tables live in `public`, owned by the
    `assembly` migration owner, so none of them declares a namespace."""
    registry = NamespaceRegistry.from_manifests(load_manifests(FEATURE_MODULES))
    assert registry.module_schemas() == ()
    assert {o.owner for o in registry.owners()} == {"kernel", "assembly"}


def test_module_registry_assigns_namespaces() -> None:
    registry = ModuleRegistry(load_manifests(FEATURE_MODULES))
    assert registry.namespaces().module_schemas() == ()
    payload = registry.inventory_payload()
    owners = {row["owner"] for row in payload["migration_owners"]}  # type: ignore[union-attr]
    assert owners == {"kernel", "assembly"}
    assert all(row["db_schema"] is None for row in payload["modules"])  # type: ignore[union-attr]


def test_a_plain_feature_manifest_is_stateless_under_d1() -> None:
    feature = FeatureManifest(name="legacy")
    registry = NamespaceRegistry.from_manifests([feature])
    assert registry.module_schemas() == ()
