"""The recovery-bundle mutation matrix — every control, observed firing.

`backup.py` says only ``PROVED`` supports a recovery claim. This file is why
``PROVED`` can be reached: each required mutation is applied to a bundle that
otherwise passes, and each one is shown to FAIL. A control that has never been
watched fail is indistinguishable from a control that was deleted, and the
Vendor CP measurement is what that looks like in practice — 55 ACL entries and
26 policies were faithfully captured for years by a command that captured no
roles, and nothing anywhere noticed.

The fixtures below model the measured production shape: five roles (the three
everyone documents plus the two outbox dispatchers nobody does), a platform
table the tenant role must not reach, and a tenant table under FORCEd RLS.

Note what is NOT here: no database, no container, no daemon. `verify_recovery`
is a pure function over two snapshots, which is exactly what makes twelve
mutations twelve ordinary assertions.
"""

from __future__ import annotations

import ast
import dataclasses
import json
from pathlib import Path

import pytest
from dotmac_deployment_foundation.backup import (
    ArtefactClass,
    Assurance,
    BackupRecord,
    retention_keep,
)
from dotmac_deployment_foundation.errors import SpecError
from dotmac_deployment_foundation.recovery import (
    COMPONENTS,
    REQUIRED_COMPONENTS,
    RESTORE_PROCEDURE,
    BundleComponent,
    CatalogEvidence,
    DefaultPrivilegeFact,
    Disposition,
    EffectivePrivilegeFact,
    ExtensionFact,
    FunctionSecurityFact,
    MembershipFact,
    OwnershipFact,
    PolicyFact,
    PrivilegeFact,
    RestoreAttempt,
    RlsFact,
    RoleFact,
    TablespaceDecision,
    adjudicate_restore,
    build_manifest,
    build_recovery_receipt,
    classify_invariant_breaches,
    derive_role_closure,
    load_manifest,
    refuse_identity_stripping,
    restore_plan,
    verify_plane_isolation,
    verify_recovery,
)
from dotmac_deployment_foundation.recovery import (
    adjudicate_restore as _adjudicate,
)
from dotmac_deployment_foundation.spec import (
    IsolationInvariant,
    ProductDeploymentSpec,
)

# ── the modelled production shape ────────────────────────────────────────────

MIGRATION_OWNER = "app_admin"
TENANT_APP = "app_user"
PLATFORM_APP = "platform_api"
DISPATCHER = "outbox_dispatcher"
PLATFORM_DISPATCHER = "platform_outbox_dispatcher"

TENANT_TABLE = "public.parties"
PLATFORM_TABLE = "public.platform_admins"
PRODUCT = "dotmac_starter_mt"

_DIGEST = {
    component.value: "sha256:" + f"{index:02x}" * 32
    for index, component in enumerate(REQUIRED_COMPONENTS)
}


def _roles() -> tuple[RoleFact, ...]:
    return (
        RoleFact(
            name=MIGRATION_OWNER,
            can_login=True,
            inherit=True,
            superuser=False,
            createrole=False,
            createdb=False,
            replication=False,
            bypassrls=True,
        ),
        RoleFact(
            name=TENANT_APP,
            can_login=True,
            inherit=True,
            superuser=False,
            createrole=False,
            createdb=False,
            replication=False,
            bypassrls=False,
        ),
        RoleFact(
            name=PLATFORM_APP,
            can_login=True,
            inherit=True,
            superuser=False,
            createrole=False,
            createdb=False,
            replication=False,
            bypassrls=False,
        ),
        # NOINHERIT on purpose: a dispatcher borrows its access from a
        # SECURITY DEFINER routine and must not silently acquire whatever the
        # group it belongs to holds.
        RoleFact(
            name=DISPATCHER,
            can_login=True,
            inherit=False,
            superuser=False,
            createrole=False,
            createdb=False,
            replication=False,
            bypassrls=False,
        ),
        RoleFact(
            name=PLATFORM_DISPATCHER,
            can_login=True,
            inherit=False,
            superuser=False,
            createrole=False,
            createdb=False,
            replication=False,
            bypassrls=False,
        ),
    )


def _group(name: str) -> RoleFact:
    """A NOLOGIN group role — an identity that exists only to be inherited from."""
    return RoleFact(
        name=name,
        can_login=False,
        inherit=True,
        superuser=False,
        createrole=False,
        createdb=False,
        replication=False,
        bypassrls=False,
    )


def _evidence() -> CatalogEvidence:
    return CatalogEvidence(
        roles=_roles(),
        memberships=(
            MembershipFact(
                member=DISPATCHER, role=MIGRATION_OWNER, inherit_option=False
            ),
            MembershipFact(
                member=PLATFORM_DISPATCHER, role=MIGRATION_OWNER, inherit_option=False
            ),
        ),
        ownership=(
            OwnershipFact(kind="schema", identity="public", owner=MIGRATION_OWNER),
            OwnershipFact(kind="table", identity=TENANT_TABLE, owner=MIGRATION_OWNER),
            OwnershipFact(kind="table", identity=PLATFORM_TABLE, owner=MIGRATION_OWNER),
        ),
        privileges=(
            PrivilegeFact(
                scope="schema",
                identity="public",
                grantee=TENANT_APP,
                privilege="USAGE",
                grantor=MIGRATION_OWNER,
            ),
            PrivilegeFact(
                scope="table",
                identity=TENANT_TABLE,
                grantee=TENANT_APP,
                privilege="SELECT",
                grantor=MIGRATION_OWNER,
            ),
            PrivilegeFact(
                scope="table",
                identity=PLATFORM_TABLE,
                grantee=PLATFORM_APP,
                privilege="SELECT",
                grantor=MIGRATION_OWNER,
            ),
        ),
        effective_privileges=(
            EffectivePrivilegeFact(
                role=TENANT_APP,
                identity=PLATFORM_TABLE,
                privilege="SELECT",
                holds=False,
            ),
            EffectivePrivilegeFact(
                role=PLATFORM_APP,
                identity=PLATFORM_TABLE,
                privilege="SELECT",
                holds=True,
            ),
        ),
        functions=(
            FunctionSecurityFact(
                signature="public.drain_outbox()",
                owner=MIGRATION_OWNER,
                security_definer=True,
                public_may_execute=False,
                executors=(DISPATCHER,),
            ),
        ),
        default_privileges=(
            DefaultPrivilegeFact(
                owner=MIGRATION_OWNER,
                schema="public",
                object_kind="table",
                grantee=TENANT_APP,
                privilege="SELECT",
            ),
        ),
        policies=(
            PolicyFact(
                table=TENANT_TABLE,
                name="parties_tenant_isolation",
                command="ALL",
                roles=(TENANT_APP,),
            ),
        ),
        row_security=(RlsFact(table=TENANT_TABLE, enabled=True, forced=True),),
        extensions=(ExtensionFact(name="pgcrypto", version="1.3", schema="public"),),
        schemas=("public",),
        migration_heads=("a003",),
        tablespaces=TablespaceDecision(kind="none"),
    )


def _manifest(evidence: CatalogEvidence | None = None):
    return build_manifest(
        product=PRODUCT,
        environment="production",
        postgres_major=16,
        source_revision="c" * 40,
        captured_at_epoch=1_800_000_000,
        evidence=evidence if evidence is not None else _evidence(),
        component_digests=_DIGEST,
    )


PLANE_INVARIANT_CODE = "tenant-role-cannot-reach-platform-tables"
PLATFORM_REACHABLE_CODE = "platform-role-reaches-its-own-plane"


def _isolation() -> tuple[IsolationInvariant, ...]:
    """The descriptor's OWN invariants, not a stand-in.

    Using the real parsed objects means these tests exercise the type a product
    actually writes; a local look-alike would keep passing after the descriptor
    schema changed underneath it.
    """
    return _spec().database.isolation


def _verify(restored: CatalogEvidence) -> tuple[str, ...]:
    return verify_recovery(
        manifest=_manifest(),
        source=_evidence(),
        restored=restored,
        isolation=_isolation(),
    )


# ── the control group: an unmutated bundle passes ────────────────────────────


def test_an_unmutated_recovery_proves() -> None:
    """The baseline. Without it every refusal below could be passing for the
    wrong reason — a check that refuses everything is not a check."""
    assert _verify(_evidence()) == ()


def test_the_closure_finds_all_five_roles_not_the_three_that_are_documented() -> None:
    """The two dispatchers are reachable only through a SECURITY DEFINER routine
    and a membership. Every product document names three roles; the measured
    restore failed on five, and a hand-written list would have been short by
    exactly the two nobody thinks about."""
    closure = derive_role_closure(_evidence())
    assert closure.required == {
        MIGRATION_OWNER,
        TENANT_APP,
        PLATFORM_APP,
        DISPATCHER,
        PLATFORM_DISPATCHER,
    }
    assert "EXECUTE" in closure.reason_for(
        DISPATCHER
    ) or "member" in closure.reason_for(DISPATCHER)


def test_the_closure_walks_membership_transitively() -> None:
    """A role named nowhere in any ACL is still required when something that IS
    named inherits from it. One level of expansion is a guess that happens to be
    right for today's fleet."""
    evidence = dataclasses.replace(
        _evidence(),
        roles=(
            *_roles(),
            _group("reporting_group"),
            _group("analytics_group"),
        ),
        memberships=(
            MembershipFact(member=TENANT_APP, role="reporting_group"),
            MembershipFact(member="reporting_group", role="analytics_group"),
        ),
    )
    assert "analytics_group" in derive_role_closure(evidence).required


def test_a_builtin_role_is_not_counted_as_missing() -> None:
    """`pg_read_all_data` and PUBLIC are not objects a bundle carries. Counting
    them as missing would make every bundle fail, and a check that always fails
    gets deleted."""
    evidence = dataclasses.replace(
        _evidence(),
        privileges=(
            *_evidence().privileges,
            PrivilegeFact(
                scope="table",
                identity=TENANT_TABLE,
                grantee="PUBLIC",
                privilege="SELECT",
            ),
            PrivilegeFact(
                scope="table",
                identity=TENANT_TABLE,
                grantee="pg_read_all_data",
                privilege="SELECT",
            ),
        ),
    )
    closure = derive_role_closure(evidence)
    assert closure.required == derive_role_closure(_evidence()).required
    assert closure.builtin_referenced == {"PUBLIC", "pg_read_all_data"}


# ── REQUIRED MUTATION 1: remove a role ───────────────────────────────────────


def test_removing_a_role_fails() -> None:
    restored = dataclasses.replace(
        _evidence(),
        roles=tuple(role for role in _roles() if role.name != DISPATCHER),
    )
    findings = _verify(restored)
    assert any(
        DISPATCHER in finding and "absent" in finding for finding in findings
    ), findings


def test_a_bundle_whose_acls_name_a_role_it_does_not_define_is_refused_at_capture() -> (
    None
):
    """The 114-error shape, refused when the artefact is WRITTEN rather than
    discovered during an incident."""
    evidence = dataclasses.replace(
        _evidence(), roles=tuple(r for r in _roles() if r.name != PLATFORM_APP)
    )
    with pytest.raises(SpecError, match="114 missing-role errors"):
        _manifest(evidence)


# ── REQUIRED MUTATION 2: remove a membership ─────────────────────────────────


def test_removing_a_membership_fails() -> None:
    """The role still exists, so a role-existence check passes. It holds
    nothing, because what it held came through the group."""
    restored = dataclasses.replace(
        _evidence(),
        memberships=tuple(m for m in _evidence().memberships if m.member != DISPATCHER),
    )
    findings = _verify(restored)
    assert any(
        "membership" in finding and DISPATCHER in finding for finding in findings
    )
    assert DISPATCHER in {
        role.name for role in restored.roles
    }, "the point of this mutation is that the ROLE survives"


# ── REQUIRED MUTATION 3: remove an inheritance flag ──────────────────────────


def test_removing_a_role_inheritance_flag_fails() -> None:
    restored = dataclasses.replace(
        _evidence(),
        roles=tuple(
            dataclasses.replace(role, inherit=True) if role.name == DISPATCHER else role
            for role in _roles()
        ),
    )
    findings = _verify(restored)
    assert any("INHERIT" in finding and DISPATCHER in finding for finding in findings)


def test_losing_the_postgres_16_per_membership_inherit_option_fails() -> None:
    """From PostgreSQL 16 each GRANT carries its own WITH INHERIT. Deriving it
    from the member's rolinherit is right on 15 and silently permissive on 16."""
    restored = dataclasses.replace(
        _evidence(),
        memberships=tuple(
            dataclasses.replace(m, inherit_option=True) for m in _evidence().memberships
        ),
    )
    findings = _verify(restored)
    assert any("inherit_option" in finding for finding in findings), findings


# ── REQUIRED MUTATION 4: lose an ownership assignment ────────────────────────


def test_losing_an_ownership_assignment_fails() -> None:
    """Ownership drifting to the restoring identity is how a database ends up
    belonging to whoever ran pg_restore, with every table present."""
    restored = dataclasses.replace(
        _evidence(),
        ownership=tuple(
            dataclasses.replace(fact, owner="postgres")
            if fact.identity == TENANT_TABLE
            else fact
            for fact in _evidence().ownership
        ),
    )
    findings = _verify(restored)
    assert any("owned by 'postgres'" in finding for finding in findings), findings


def test_losing_a_security_definer_routines_owner_fails() -> None:
    """A SECURITY DEFINER routine executes as its owner, so its owner IS its
    privilege — and no table-privilege comparison would see this."""
    restored = dataclasses.replace(
        _evidence(),
        functions=(dataclasses.replace(_evidence().functions[0], owner="postgres"),),
    )
    findings = _verify(restored)
    assert any("SECURITY DEFINER" in finding for finding in findings), findings


def test_a_restored_routine_executable_by_public_fails() -> None:
    """A NULL proacl is not 'granted to nobody' — the default for a function is
    EXECUTE to PUBLIC, and for a definer routine that is an escalation."""
    restored = dataclasses.replace(
        _evidence(),
        functions=(
            dataclasses.replace(_evidence().functions[0], public_may_execute=True),
        ),
    )
    assert any("PUBLIC" in finding for finding in _verify(restored))


# ── REQUIRED MUTATION 5: lose a default privilege ────────────────────────────


def test_losing_a_default_privilege_fails() -> None:
    """Nothing is wrong today. The next migration creates a table the
    application cannot read — which is why this is not caught by any check that
    looks at objects that currently exist."""
    restored = dataclasses.replace(_evidence(), default_privileges=())
    findings = _verify(restored)
    assert any("default privilege" in finding for finding in findings), findings


# ── REQUIRED MUTATION 6: lose the platform revocation while policies remain ──


def test_losing_the_platform_revocation_while_policies_remain_fails() -> None:
    """The measured trap, exactly.

    Policies are present and unchanged; RLS is enabled and forced; the tenant
    role can now read the platform table. Under ADR-0023 the revocation IS the
    isolation on that plane, so nothing an operator would look at has changed.
    """
    restored = dataclasses.replace(
        _evidence(),
        effective_privileges=(
            EffectivePrivilegeFact(
                role=TENANT_APP,
                identity=PLATFORM_TABLE,
                privilege="SELECT",
                holds=True,
            ),
            EffectivePrivilegeFact(
                role=PLATFORM_APP,
                identity=PLATFORM_TABLE,
                privilege="SELECT",
                holds=True,
            ),
        ),
    )
    findings = _verify(restored)
    assert findings, "the tenant role can read the platform table and nothing objected"
    assert any(PLANE_INVARIANT_CODE in finding for finding in findings)
    # The policies are untouched — which is the whole reason this looks fine.
    assert restored.policies == _evidence().policies
    assert all(fact.forced for fact in restored.row_security)


def test_an_invariant_with_no_effective_observation_is_unproven_not_satisfied() -> None:
    """`information_schema.table_privileges` shows DIRECT grants only. A
    revocation checked that way reads as satisfied for a role holding the
    privilege through PUBLIC, through inheritance, or on a column — the check
    goes green exactly when the boundary is broken."""
    restored = dataclasses.replace(_evidence(), effective_privileges=())
    findings = verify_plane_isolation(restored, _isolation())
    assert any("UNPROVEN" in finding for finding in findings), findings


def test_a_platform_role_revoked_from_its_own_plane_also_fails() -> None:
    """Only checking the denial half is how a plane ends up isolated from
    itself: revoke everything and every 'cannot reach' assertion passes."""
    restored = dataclasses.replace(
        _evidence(),
        effective_privileges=(
            EffectivePrivilegeFact(
                role=TENANT_APP,
                identity=PLATFORM_TABLE,
                privilege="SELECT",
                holds=False,
            ),
            EffectivePrivilegeFact(
                role=PLATFORM_APP,
                identity=PLATFORM_TABLE,
                privilege="SELECT",
                holds=False,
            ),
        ),
    )
    assert any(PLATFORM_REACHABLE_CODE in finding for finding in _verify(restored))


def test_losing_rls_force_while_policies_remain_fails() -> None:
    """ENABLE without FORCE leaves the owner outside every policy, and a restore
    makes the owner whoever ran it."""
    restored = dataclasses.replace(
        _evidence(),
        row_security=(RlsFact(table=TENANT_TABLE, enabled=True, forced=False),),
    )
    findings = _verify(restored)
    assert any("FORCE" in finding for finding in findings), findings


# ── REQUIRED MUTATION 7: the validator manufacturing a role (structural) ─────


#: The `[database]` contract, appended to the repository's own real descriptor.
#:
#: Built from `deploy/product.toml` rather than hand-written, so this fixture
#: cannot drift away from the schema every other product uses - a fixture with
#: its own private idea of a valid descriptor tests the fixture.
DATABASE_SECTION = """
[database]
postgres_major = 16
expected_schemas = ["public"]
tablespaces = "none"

[[database.roles]]
name = "app_admin"
kind = "migration_owner"
bypassrls = true

[[database.roles]]
name = "app_user"
kind = "tenant_app"

[[database.roles]]
name = "platform_api"
kind = "platform_app"

[[database.roles]]
name = "outbox_dispatcher"
kind = "dispatcher"
inherit = false
member_of = ["app_admin"]

[[database.roles]]
name = "platform_outbox_dispatcher"
kind = "dispatcher"
inherit = false
member_of = ["app_admin"]

[[database.isolation]]
code = "tenant-role-cannot-reach-platform-tables"
role = "app_user"
scope = "table"
objects = ["public.platform_admins"]
privileges = ["SELECT"]
denied = true

[[database.isolation]]
code = "platform-role-reaches-its-own-plane"
role = "platform_api"
scope = "table"
objects = ["public.platform_admins"]
privileges = ["SELECT"]
denied = false
"""

REAL_DESCRIPTOR = (
    Path(__file__).resolve().parents[2] / "deploy" / "product.toml"
).read_text(encoding="utf-8")

DESCRIPTOR_WITH_EVERY_ROLE = REAL_DESCRIPTOR + DATABASE_SECTION


def _spec() -> ProductDeploymentSpec:
    return ProductDeploymentSpec.loads(DESCRIPTOR_WITH_EVERY_ROLE, source="<test>")


def test_the_descriptor_declares_every_role_and_still_cannot_supply_one() -> None:
    """**The structural guard.**

    The descriptor names all five roles correctly. The bundle is missing its
    role closure. A validator that could fall back on the descriptor would now
    produce a plan, restore those five roles, and then verify that the five
    roles it created are present — passing its own check with evidence it
    manufactured.

    It refuses instead, and the refusal is observable precisely because the
    descriptor IS complete: nothing here is missing except the one input the
    restore is allowed to use.
    """
    spec = _spec()
    assert len(spec.database.roles) == 5, "the descriptor must be complete"
    assert {role.name for role in spec.database.roles} == {
        MIGRATION_OWNER,
        TENANT_APP,
        PLATFORM_APP,
        DISPATCHER,
        PLATFORM_DISPATCHER,
    }

    without_roles = {
        code: value
        for code, value in _DIGEST.items()
        if code != BundleComponent.ROLE_CLOSURE.value
    }
    payload = json.loads(_manifest().to_json())
    payload["components"] = without_roles
    with pytest.raises(SpecError, match="not a recovery bundle"):
        restore_plan(spec, load_manifest(json.dumps(payload)))


ROLE_DDL = ("CREATE ROLE", "CREATE USER", "ALTER ROLE", "DROP ROLE")


def role_ddl_offenders(source: str, *, label: str) -> list[str]:
    """Role DDL in an executable string, ignoring docstrings.

    Prose is excluded deliberately and structurally, not by keyword. This module
    explains at length that `pg_dumpall --globals-only` emits
    `CREATE ROLE ... PASSWORD ...`, and a guard that tripped on that sentence
    would be switched off within a week — the facility's own conformance test
    records exactly that having happened once already. So docstrings are
    identified by position (the first statement of a module, class or function)
    and skipped, and every OTHER string constant is checked.

    A pure function over source text so the detector can be shown to bite.
    """
    tree = ast.parse(source)
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ):
            body = getattr(node, "body", None)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in docstrings:
            continue
        for forbidden in ROLE_DDL:
            if forbidden in node.value:
                offenders.append(f"{label}:{node.lineno}: {forbidden}")
    return offenders


def test_no_module_in_the_facility_emits_role_ddl() -> None:
    """The structural half of "the validator cannot manufacture a role".

    `restore_plan` refusing is a refusal somebody can add a fallback to. A
    package that contains no role DDL at all cannot grow one without the diff
    saying so.
    """
    package = (
        Path(__file__).resolve().parents[2]
        / "packages"
        / "dotmac-deployment-foundation"
        / "src"
    )
    offenders: list[str] = []
    for path in sorted(package.rglob("*.py")):
        offenders.extend(
            role_ddl_offenders(path.read_text(encoding="utf-8"), label=path.name)
        )
    assert offenders == [], (
        "the facility emits role DDL: "
        + "; ".join(offenders)
        + ". A validator that can create the role it is checking for can always "
        "make its own check pass"
    )


def test_the_role_ddl_guard_actually_bites() -> None:
    """The sensitivity proof. A scan over a clean tree passes for the wrong
    reason otherwise — which is indistinguishable from the guard being gone."""
    planted = 'def install(cur):\n    cur.execute("CREATE ROLE app_user LOGIN")\n'
    assert role_ddl_offenders(planted, label="planted")


def test_the_role_ddl_guard_does_not_read_its_own_documentation() -> None:
    """The other half of the sensitivity proof, and the one that keeps the guard
    switched on: explaining why something is forbidden is not doing it."""
    prose = '"""We never emit CREATE ROLE; the bundle carries roles."""\n'
    assert role_ddl_offenders(prose, label="prose") == []


def test_a_bundle_carrying_a_superuser_is_refused() -> None:
    with pytest.raises(SpecError, match="SUPERUSER"):
        RoleFact(
            name="root",
            can_login=True,
            inherit=True,
            superuser=True,
            createrole=True,
            createdb=True,
            replication=True,
            bypassrls=True,
        )


def test_a_role_fact_has_nowhere_to_put_a_password() -> None:
    """Structural, not a scan. `pg_dumpall --globals-only` — the flag the one
    repaired script in the fleet reaches for — emits SCRAM verifiers, so a role
    capture without `--no-role-passwords` writes password material into whatever
    bucket the artefact ships to. A type with no field for it cannot."""
    fields = {field.name for field in dataclasses.fields(RoleFact)}
    assert not (
        fields & {"password", "password_hash", "rolpassword", "verifier", "secret"}
    )


# ── REQUIRED MUTATION 8: supply a database-only dump ─────────────────────────


def test_a_database_only_dump_is_refused_despite_being_a_plausible_whole() -> None:
    """**The control that matters most.**

    Every other mutation removes a piece. This one supplies something that looks
    complete: a real custom-format archive, the newest one in production, which
    restores 45 tables and 23 policies. It is refused on the artefact's SHAPE,
    before a target exists — the only point at which the difference is still
    cheap to see.
    """
    payload = json.loads(_manifest().to_json())
    payload["components"] = {
        BundleComponent.DATABASE_DUMP.value: _DIGEST[
            BundleComponent.DATABASE_DUMP.value
        ]
    }
    with pytest.raises(SpecError) as caught:
        load_manifest(json.dumps(payload))
    message = str(caught.value)
    assert "not a recovery bundle" in message
    # The refusal must SAY that restoring it looks fine, or the next operator
    # reads "missing components" as a packaging nit and forces past it.
    assert "policies appear" in message


def test_a_raw_dump_offered_as_a_manifest_is_named_for_what_it_is() -> None:
    with pytest.raises(SpecError, match="database-only dump"):
        load_manifest(b"PGDMP\x00\x01\x0c\x00")


def test_building_a_bundle_without_every_component_is_refused() -> None:
    for component in REQUIRED_COMPONENTS:
        partial = {
            code: value for code, value in _DIGEST.items() if code != component.value
        }
        with pytest.raises(SpecError, match="missing"):
            build_manifest(
                product=PRODUCT,
                environment="production",
                postgres_major=16,
                source_revision="c" * 40,
                captured_at_epoch=1_800_000_000,
                evidence=_evidence(),
                component_digests=partial,
            )


def test_every_component_says_what_its_digest_covers_and_what_absence_means() -> None:
    """A receipt cites component digests. 'The bundle hashed to X' tells a
    reader nothing about which property X protects."""
    assert set(COMPONENTS) == set(REQUIRED_COMPONENTS)
    for component, spec in COMPONENTS.items():
        assert spec.covers.strip(), component
        assert spec.absent_means.strip(), component


# ── step 4: a non-zero restore is destroyed, not reported ────────────────────


def test_a_non_zero_restore_that_left_a_usable_looking_database_is_destroyed() -> None:
    """The measured Vendor CP run, as a unit test."""
    attempt = RestoreAttempt(
        exit_status=1,
        tables_present=45,
        policies_present=23,
        rls_tables_present=16,
        missing_role_errors=114,
    )
    verdict = adjudicate_restore(attempt)
    assert verdict.disposition is Disposition.DESTROY
    assert verdict.must_destroy
    assert any("45 table" in reason for reason in verdict.reasons)
    assert any("114 missing-role" in reason for reason in verdict.reasons)


def test_a_zero_exit_with_missing_role_errors_is_also_destroyed() -> None:
    """Zero-with-errors is the worse of the two: nothing downstream looks again."""
    verdict = _adjudicate(RestoreAttempt(exit_status=0, missing_role_errors=3))
    assert verdict.disposition is Disposition.DESTROY


def test_a_clean_restore_proceeds() -> None:
    assert (
        _adjudicate(RestoreAttempt(exit_status=0, tables_present=45)).disposition
        is Disposition.PROCEED
    )


# ── the ten steps ────────────────────────────────────────────────────────────


def test_the_restore_procedure_has_exactly_ten_ordered_steps() -> None:
    assert len(RESTORE_PROCEDURE) == 10
    assert [step.order for step in RESTORE_PROCEDURE] == list(range(1, 11))


def test_step_two_admits_the_bundle_and_not_the_descriptor() -> None:
    step = RESTORE_PROCEDURE[1]
    assert BundleComponent.ROLE_CLOSURE in step.inputs
    assert BundleComponent.DATABASE_DUMP not in step.inputs
    assert "descriptor is not an input" in step.refuses


def test_restore_plan_refuses_a_bundle_from_another_product() -> None:
    payload = json.loads(_manifest().to_json())
    payload["product"] = "somebody-else"
    with pytest.raises(SpecError, match="captured from"):
        restore_plan(_spec(), load_manifest(json.dumps(payload)))


def test_restore_plan_refuses_a_bundle_at_different_migration_heads() -> None:
    payload = json.loads(_manifest().to_json())
    payload["migration_heads"] = ["a001_something_older"]
    with pytest.raises(SpecError, match="different revision"):
        restore_plan(_spec(), load_manifest(json.dumps(payload)))


def test_a_correct_bundle_yields_the_full_procedure() -> None:
    assert restore_plan(_spec(), _manifest()) == RESTORE_PROCEDURE


# ── the receipt ──────────────────────────────────────────────────────────────


def test_the_receipt_carries_the_restore_wall_clock() -> None:
    """A bundle proved to restore in twenty minutes and one proved to restore in
    six hours are both PROVED and are different facts. Only one of them can be
    part of a recovery-time claim."""
    receipt = build_recovery_receipt(
        manifest=_manifest(),
        adjudication=_adjudicate(RestoreAttempt(exit_status=0)),
        findings=(),
        restore_duration_seconds=1_187,
        readiness_role=TENANT_APP,
        readiness_passed=True,
        image_digest="sha256:" + "a" * 64,
        proved_at_epoch=1_800_000_500,
    )
    assert receipt.proved
    assert receipt.restore_duration_seconds == 1_187
    assert receipt.content["roles_restored"] == 5


def test_a_receipt_is_not_proved_when_readiness_used_the_wrong_identity() -> None:
    receipt = build_recovery_receipt(
        manifest=_manifest(),
        adjudication=_adjudicate(RestoreAttempt(exit_status=0)),
        findings=(),
        restore_duration_seconds=60,
        readiness_role=TENANT_APP,
        readiness_passed=False,
        image_digest="sha256:" + "a" * 64,
        proved_at_epoch=1,
    )
    assert not receipt.proved


def test_a_receipt_with_findings_is_not_proved() -> None:
    receipt = build_recovery_receipt(
        manifest=_manifest(),
        adjudication=_adjudicate(RestoreAttempt(exit_status=0)),
        findings=("role 'app_user' is absent",),
        restore_duration_seconds=60,
        readiness_role=TENANT_APP,
        readiness_passed=True,
        image_digest="sha256:" + "a" * 64,
        proved_at_epoch=1,
    )
    assert not receipt.proved


def test_the_receipt_is_value_free() -> None:
    """It carries names, counts, digests and durations. A DSN with a password in
    it would be refused by the same guard the descriptor uses."""
    receipt = build_recovery_receipt(
        manifest=_manifest(),
        adjudication=_adjudicate(RestoreAttempt(exit_status=0)),
        findings=(),
        restore_duration_seconds=60,
        readiness_role=TENANT_APP,
        readiness_passed=True,
        image_digest="sha256:" + "a" * 64,
        proved_at_epoch=1,
    )
    body = receipt.to_json()
    assert "password" not in body.lower()
    assert "://" not in body


# ── the capture-side default that was discarding the evidence ────────────────


@pytest.mark.parametrize(
    "flag", ["--no-owner", "-O", "--no-privileges", "-x", "--no-acl"]
)
def test_identity_stripping_dump_flags_are_refused(flag: str) -> None:
    """This facility's own Compose provider defaulted to
    ("--no-owner", "--no-privileges"), so every adopting product inherited a dump
    with no ownership and no grants in it. A component cannot record evidence the
    capture threw away."""
    with pytest.raises(SpecError, match="strip ownership"):
        refuse_identity_stripping([flag], where="test")


def test_ordinary_dump_flags_are_allowed() -> None:
    refuse_identity_stripping(["--format=custom", "--no-role-passwords"], where="test")


def test_the_compose_provider_refuses_the_old_default_for_a_recovery_product(
    tmp_path: Path,
) -> None:
    """The guard where it actually bites.

    This provider's `pg_dump_extra_args` still DEFAULTS to
    ("--no-owner", "--no-privileges") for products that have not adopted the
    contract, because changing it under them would break a working backup path.
    A product that declares [database] is asking for a recovery bundle, and for
    that product the default is refused rather than silently honoured.
    """
    from dotmac_deployment_foundation.providers.compose_host import ComposeHostEffects

    spec = _spec()
    effects = ComposeHostEffects(spec, tmp_path)
    with pytest.raises(SpecError, match="strip ownership"):
        effects._backup_command(spec.backup_datasets[0])


def test_the_compose_provider_still_serves_a_product_without_the_contract(
    tmp_path: Path,
) -> None:
    """The other side of the same guard: an existing product is untouched. A
    refusal that fired for everyone would be reverted, not adopted."""
    from dotmac_deployment_foundation.providers.compose_host import ComposeHostEffects

    text = DESCRIPTOR_WITH_EVERY_ROLE.split("[database]")[0]
    spec = ProductDeploymentSpec.loads(text, source="<test>")
    assert spec.database is None
    effects = ComposeHostEffects(spec, tmp_path)
    assert "pg_dump" in effects._backup_command(spec.backup_datasets[0])


# ── retention ────────────────────────────────────────────────────────────────


def _record(*, epoch: int, assurance: Assurance, klass: ArtefactClass) -> BackupRecord:
    return BackupRecord(
        dataset="primary",
        path=f"/var/backups/primary_{epoch}",
        size_bytes=1024,
        checksum="e" * 64,
        checksum_algorithm="sha256",
        completed_at_epoch=epoch,
        assurance=assurance,
        restore_proved_at_epoch=epoch if assurance is Assurance.PROVED else None,
        artefact_class=klass,
    )


NOW = 1_800_000_000
DAY = 86_400


def test_a_data_export_cannot_claim_to_be_restorable() -> None:
    with pytest.raises(SpecError, match="data_export"):
        _record(
            epoch=NOW,
            assurance=Assurance.PROVED,
            klass=ArtefactClass.DATA_EXPORT,
        )


def test_the_newest_proved_bundle_is_kept_regardless_of_age() -> None:
    """The clause that stops a policy deleting the only thing that ever worked.
    This bundle is a year past the window."""
    ancient = _record(
        epoch=NOW - 365 * DAY,
        assurance=Assurance.PROVED,
        klass=ArtefactClass.RECOVERY_BUNDLE,
    )
    keep, prune = retention_keep(_spec(), "primary", [ancient], now_epoch=NOW)
    assert ancient in keep
    assert prune == ()


def test_an_aged_data_export_is_kept_while_nothing_has_been_proved() -> None:
    """The fleet's existing dumps are the only copy of the data. Deleting one on
    a retention window before anything is proved restorable trades a weak
    artefact for none at all."""
    old_export = _record(
        epoch=NOW - 90 * DAY,
        assurance=Assurance.VERIFIED,
        klass=ArtefactClass.DATA_EXPORT,
    )
    keep, prune = retention_keep(_spec(), "primary", [old_export], now_epoch=NOW)
    assert old_export in keep
    assert prune == ()


def test_an_aged_data_export_is_pruned_once_a_newer_proved_bundle_exists() -> None:
    old_export = _record(
        epoch=NOW - 90 * DAY,
        assurance=Assurance.VERIFIED,
        klass=ArtefactClass.DATA_EXPORT,
    )
    proved = _record(
        epoch=NOW - 60 * DAY,
        assurance=Assurance.PROVED,
        klass=ArtefactClass.RECOVERY_BUNDLE,
    )
    keep, prune = retention_keep(
        _spec(), "primary", [old_export, proved], now_epoch=NOW
    )
    assert proved in keep
    assert old_export in prune


# ── descriptor refusals ──────────────────────────────────────────────────────


def test_a_tenant_app_role_declaring_bypassrls_is_refused() -> None:
    text = DESCRIPTOR_WITH_EVERY_ROLE.replace(
        '[[database.roles]]\nname = "app_user"\nkind = "tenant_app"',
        '[[database.roles]]\nname = "app_user"\nkind = "tenant_app"\nbypassrls = true',
    )
    with pytest.raises(SpecError, match="decorative"):
        ProductDeploymentSpec.loads(text, source="<test>")


def test_a_membership_naming_an_undeclared_group_is_refused() -> None:
    text = DESCRIPTOR_WITH_EVERY_ROLE.replace(
        'member_of = ["app_admin"]', 'member_of = ["nobody_declared_me"]', 1
    )
    with pytest.raises(SpecError, match="does not declare"):
        ProductDeploymentSpec.loads(text, source="<test>")


def test_a_database_contract_with_no_postgres_dataset_is_refused() -> None:
    # Anchored to the dataset's own block. A bare replace also rewrites
    # `[[external_dependencies]]`, whose `kind = "postgres"` is a DIFFERENT
    # vocabulary - the descriptor then fails on the dependency instead, and the
    # test would pass on an unrelated refusal.
    text = DESCRIPTOR_WITH_EVERY_ROLE.replace(
        'code = "primary"\nkind = "postgres"',
        'code = "primary"\nkind = "volume"',
        1,
    )
    with pytest.raises(SpecError, match="nobody backs up"):
        ProductDeploymentSpec.loads(text, source="<test>")


def test_a_descriptor_cannot_ask_for_a_superuser_database_role() -> None:
    """There is no key to write, so the refusal is the schema's unknown-field
    rule rather than a value check somebody could relax."""
    text = DESCRIPTOR_WITH_EVERY_ROLE.replace(
        'kind = "migration_owner"\nbypassrls = true',
        'kind = "migration_owner"\nbypassrls = true\nsuperuser = true',
    )
    with pytest.raises(SpecError, match="unknown key"):
        ProductDeploymentSpec.loads(text, source="<test>")


# ── the rehearsal as a DRIFT detector, not only a recovery proof ─────────────


def _with_effective(
    evidence: CatalogEvidence, *, tenant_holds: bool, platform_holds: bool = True
) -> CatalogEvidence:
    return dataclasses.replace(
        evidence,
        effective_privileges=(
            EffectivePrivilegeFact(
                role=TENANT_APP,
                identity=PLATFORM_TABLE,
                privilege="SELECT",
                holds=tenant_holds,
            ),
            EffectivePrivilegeFact(
                role=PLATFORM_APP,
                identity=PLATFORM_TABLE,
                privilege="SELECT",
                holds=platform_holds,
            ),
        ),
    )


def test_a_breach_only_in_the_restored_copy_is_named_a_RESTORE_DEFECT() -> None:
    """The source is clean, so the bundle or the restore introduced it. The
    operator should be looking at the recovery path."""
    findings = verify_recovery(
        manifest=_manifest(),
        source=_with_effective(_evidence(), tenant_holds=False),
        restored=_with_effective(_evidence(), tenant_holds=True),
        isolation=_isolation(),
    )
    assert any(finding.startswith("RESTORE DEFECT") for finding in findings), findings
    assert not any(finding.startswith("SOURCE DRIFT") for finding in findings)


def test_a_breach_in_BOTH_catalogues_is_named_SOURCE_DRIFT() -> None:
    """The Platform CP rehearsal, 2026-08-30, as a unit test.

    It found `platform_api` holding DELETE on a delivery-target table in the
    restored copy. The tempting reading is "the restore is unfaithful". Checking
    production found the SAME permission, so it was real drift: a revocation the
    declared contract requires whose migration has never run.

    Those two readings have opposite remedies, which is the whole reason the
    classification is worth producing rather than leaving to a careful operator.
    """
    drifted = _with_effective(_evidence(), tenant_holds=True)
    findings = verify_recovery(
        manifest=_manifest(),
        source=drifted,
        restored=drifted,
        isolation=_isolation(),
    )
    assert any(finding.startswith("SOURCE DRIFT") for finding in findings), findings
    assert not any(finding.startswith("RESTORE DEFECT") for finding in findings)
    assert any("Fix production" in finding for finding in findings)


def test_source_drift_still_fails_the_proof() -> None:
    """The label changes where the operator looks, never whether it is PROVED.

    A faithfully restored database that violates its own declared invariants has
    not proved isolation. If drift were exonerating, the cheap repair would be to
    relax the bundle until the check passed.
    """
    drifted = _with_effective(_evidence(), tenant_holds=True)
    findings = verify_recovery(
        manifest=_manifest(),
        source=drifted,
        restored=drifted,
        isolation=_isolation(),
    )
    receipt = build_recovery_receipt(
        manifest=_manifest(),
        adjudication=_adjudicate(RestoreAttempt(exit_status=0)),
        findings=findings,
        restore_duration_seconds=1_187,
        readiness_role=TENANT_APP,
        readiness_passed=True,
        image_digest="sha256:" + "a" * 64,
        proved_at_epoch=1,
    )
    assert not receipt.proved


def test_a_clean_pair_produces_neither_label() -> None:
    assert (
        classify_invariant_breaches(
            source=_with_effective(_evidence(), tenant_holds=False),
            restored=_with_effective(_evidence(), tenant_holds=False),
            isolation=_isolation(),
        )
        == ()
    )


def test_the_manifest_reports_counts_as_OBSERVATIONS_and_gates_on_none_of_them() -> (
    None
):
    """A grant matrix is a good invariant and a poor assertion.

    `app_admin 315 / app_user 62 / platform_api 164` changes with every
    migration, so pinning it literally produces a gate that fails on correct
    work. The counts are recorded because they are useful to a reader; the
    GATE is the property - the tenant role cannot reach platform tables, the
    platform role holds its required revocations.

    Proven by construction: the counts differ between source and restored here
    and the verification is still clean, because nothing compares them.
    """
    source = _evidence()
    restored = dataclasses.replace(
        source,
        privileges=(
            *source.privileges,
            PrivilegeFact(
                scope="table",
                identity=TENANT_TABLE,
                grantee=TENANT_APP,
                privilege="INSERT",
                grantor=MIGRATION_OWNER,
            ),
        ),
    )
    assert len(restored.privileges) != len(source.privileges)
    manifest = _manifest()
    assert "counts" in manifest.content
    # The extra privilege IS reported - as a set difference, which is fidelity,
    # not a pinned total.
    findings = verify_recovery(
        manifest=manifest,
        source=source,
        restored=restored,
        isolation=_isolation(),
    )
    assert any("INSERT" in finding for finding in findings)
    assert not any("count" in finding.lower() for finding in findings)
