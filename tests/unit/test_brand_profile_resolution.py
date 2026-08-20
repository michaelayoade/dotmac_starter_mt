"""Precedence, locking, and the source report that makes both auditable.

The invariant this file protects: **a resolved brand is the merge the caller's
chain specifies, a locked field is not overridable by anything below it, and
every resolved field says which layer supplied it.**

A suite that only checked merged values would pass against an implementation
with no locking at all — and locking is the half that stops a reseller
rebranding the operator's legal identity, which is the failure ADR-0006 § 3's
second safety rule exists for.

In-memory SQLite — logic only. RLS, grants and the dual-plane isolation are
proven against real Postgres in
`tests/test_brand_profiles_dual_plane_isolation.py`.
"""

from __future__ import annotations

import uuid
from collections.abc import Generator

import pytest
from dotmac_brand_profiles import (
    BRAND_OVERRIDE_INPUTS,
    DISPLAY_FIELDS,
    IDENTITY_FIELDS,
    LOCKABLE_FIELDS,
    BrandProfile,
    Disposition,
    PlatformBrandProfile,
    ProfileFields,
    ProfileRefusedError,
    ProfileStatus,
    UnknownLockedFieldError,
    UpsertPlatformProfileCommand,
    UpsertTenantProfileCommand,
    activate_platform_profile,
    activate_tenant_profile,
    bind_host,
    module,
    resolvable_by,
    resolve,
    resolve_by_host,
    resolve_for_tenant,
    translate_legacy_brand_values,
    upsert_platform_profile,
    upsert_tenant_profile,
    validate_brand_values,
)
from dotmac_brand_profiles.ports import HostBindingRefusedError
from dotmac_kernel.audit_actions import AuditActionRegistry, install_audit_actions
from dotmac_kernel.models import Base
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

_TENANT = uuid.UUID("11111111-1111-1111-1111-111111111111")


@pytest.fixture(autouse=True)
def _installed_module_audit_actions() -> None:
    install_audit_actions(AuditActionRegistry.from_manifests([module]))


@pytest.fixture
def db() -> Generator[Session, None, None]:
    engine = create_engine("sqlite://", future=True)

    @event.listens_for(engine, "connect")
    def _attach(dbapi_connection, _record):  # type: ignore[no-untyped-def]
        dbapi_connection.isolation_level = None
        dbapi_connection.execute("ATTACH DATABASE ':memory:' AS mod_brand")

    @event.listens_for(engine, "begin")
    def _emit_begin(connection):  # type: ignore[no-untyped-def]
        connection.exec_driver_sql("BEGIN")

    Base.metadata.create_all(
        engine,
        tables=[
            table
            for table in Base.metadata.tables.values()
            if table.schema == "mod_brand"
            or table.name
            in {
                "idempotency_records",
                "platform_idempotency_records",
                "audit_events",
                "platform_audit_events",
                "platform_admins",
                "tenants",
                "parties",
            }
        ],
    )
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _cmd() -> str:
    return f"cmd-{uuid.uuid4().hex[:12]}"


def _profile(**overrides: object) -> BrandProfile:
    """A detached row, for the pure-merge tests. `resolve` takes rows and does
    no I/O, so the precedence mechanics can be driven without a database at
    all — which is what makes them cheap enough to test exhaustively."""
    fields: dict[str, object] = {
        "tenant_id": _TENANT,
        "scope_type": "tenant",
        "profile_code": "default",
        "display_name": "Base",
        "status": ProfileStatus.ACTIVE.value,
        "record_version": 1,
    }
    fields.update(overrides)
    return BrandProfile(**fields)  # type: ignore[arg-type]


# ── The merge mechanics ─────────────────────────────────────────────────────


class TestPrecedenceIsFirstNonNullWins:
    def test_a_higher_layer_supplies_the_value(self) -> None:
        resolved = resolve(
            [
                ("reseller", _profile(display_name="Acme Broadband")),
                ("tenant", _profile(display_name="Base")),
            ]
        )
        assert resolved.get("display_name") == "Acme Broadband"
        assert resolved.source_of("display_name") == "reseller"

    def test_a_lower_layer_fills_a_gap(self) -> None:
        """The point of a chain: a partial override is partial, not total."""
        resolved = resolve(
            [
                ("reseller", _profile(display_name="Acme Broadband")),
                ("tenant", _profile(display_name="Base", legal_name="Base Ltd")),
            ]
        )
        assert resolved.get("legal_name") == "Base Ltd"
        assert resolved.source_of("legal_name") == "tenant"

    def test_a_null_does_not_override(self) -> None:
        """`None` means "not set at this layer", never "clear it". A merge that
        treated null as a value would make every partially-filled higher layer
        blank the one below."""
        resolved = resolve(
            [
                ("reseller", _profile(display_name="Acme", legal_name=None)),
                ("tenant", _profile(display_name="Base", legal_name="Base Ltd")),
            ]
        )
        assert resolved.get("legal_name") == "Base Ltd"

    def test_row_mechanics_never_leak_into_the_merge(self) -> None:
        """Merging `id`, `status` or `record_version` would produce a resolved
        brand carrying one layer's primary key."""
        resolved = resolve([("tenant", _profile())])
        assert set(resolved.values) <= LOCKABLE_FIELDS
        for mechanic in ("id", "status", "record_version", "tenant_id"):
            assert mechanic not in resolved.values

    def test_an_empty_chain_resolves_to_nothing_rather_than_failing(self) -> None:
        """A tenant with no profile has no brand, which is a legitimate state —
        the caller falls back to the deployment default."""
        resolved = resolve([])
        assert resolved.values == {}
        assert resolved.locked == frozenset()


class TestALockBeatsPrecedence:
    def test_a_locked_field_is_reported_as_locked(self) -> None:
        resolved = resolve(
            [("tenant", _profile(legal_name="Base Ltd", locked_fields=["legal_name"]))]
        )
        assert "legal_name" in resolved.locked

    def test_a_lock_from_a_higher_layer_covers_the_whole_chain(self) -> None:
        """Without this, precedence is only a default and a lower layer can
        rebrand the operator's legal identity."""
        resolved = resolve(
            [
                (
                    "platform",
                    _profile(legal_name="Operator Ltd", locked_fields=["legal_name"]),
                ),
                ("reseller", _profile(legal_name="Reseller Ltd")),
            ]
        )
        assert resolved.get("legal_name") == "Operator Ltd"
        assert "legal_name" in resolved.locked
        assert "legal_name" not in resolvable_by(resolved, scope_type="reseller")

    def test_identity_fields_and_display_fields_partition_the_lockable_set(
        self,
    ) -> None:
        """ADR-0006 § 3's third rule made expressible: "let them change the
        look, not who they are" has to be one call, not a convention."""
        assert DISPLAY_FIELDS | IDENTITY_FIELDS == LOCKABLE_FIELDS
        assert not (DISPLAY_FIELDS & IDENTITY_FIELDS)
        assert "legal_name" in IDENTITY_FIELDS
        assert "display_name" in DISPLAY_FIELDS

    def test_locking_the_identity_set_leaves_display_free(self) -> None:
        resolved = resolve(
            [("platform", _profile(locked_fields=sorted(IDENTITY_FIELDS)))]
        )
        free = resolvable_by(resolved, scope_type="reseller")
        assert DISPLAY_FIELDS <= free
        assert not (IDENTITY_FIELDS & free)

    def test_a_lock_naming_an_unknown_field_is_refused(self, db) -> None:
        """A lock nothing honours is worse than no lock: the operator who set it
        believes the field is pinned, and it is not."""
        with pytest.raises(UnknownLockedFieldError, match="cannot lock"):
            upsert_tenant_profile(
                db,
                UpsertTenantProfileCommand(
                    command_id=_cmd(),
                    tenant_id=_TENANT,
                    scope_type="tenant",
                    profile_code="default",
                    fields=ProfileFields(display_name="Base"),
                    lock=["record_version"],
                ),
            )

    def test_a_lock_on_a_row_mechanic_is_ignored_by_the_merge(self) -> None:
        """Belt and braces: even a lock written directly to the column cannot
        pin something that does not participate in precedence."""
        resolved = resolve([("tenant", _profile(locked_fields=["id", "status"]))])
        assert resolved.locked == frozenset()


# ── The colour boundary ─────────────────────────────────────────────────────


class TestTheColourBoundary:
    """Michael 2026-08-19: the module owns constrained runtime values; the
    ASSEMBLY maps them into `BrandOverride`; `dotmac-ui` owns vocabulary,
    projection and contrast."""

    def test_the_allowlist_is_exactly_dotmac_uis_accepted_inputs(self) -> None:
        """The anti-drift device — see the architecture guard for the assertion
        against `BrandOverride`'s own fields. Here we just pin the shape a caller
        maps through."""
        assert BRAND_OVERRIDE_INPUTS == {
            "primary_hex": "primary",
            "accent_hex": "accent",
        }

    def test_the_module_exposes_no_brand_override_constructor(self) -> None:
        """Returning a ready-made override would take the assembly's job back."""
        import dotmac_brand_profiles

        assert not hasattr(dotmac_brand_profiles, "brand_override")

    def test_a_malformed_colour_is_refused_on_write(self, db) -> None:
        """Validation is dotmac-ui's, called on write so a bad value fails where
        it was entered rather than when a page renders. This module owns no
        colour parser; a second one would eventually accept something the first
        refuses."""
        with pytest.raises(Exception):  # noqa: B017 - dotmac_ui owns the type
            upsert_tenant_profile(
                db,
                UpsertTenantProfileCommand(
                    command_id=_cmd(),
                    tenant_id=_TENANT,
                    scope_type="tenant",
                    profile_code="bad",
                    fields=ProfileFields(
                        display_name="Bad", primary_hex="not-a-colour"
                    ),
                ),
            )

    def test_an_uncoloured_profile_is_legitimate(self) -> None:
        """A deployment with no brand colour falls back to dotmac-ui's own
        tokens rather than to whatever this module happened to pick."""
        validate_brand_values({})

    def test_an_accent_without_a_primary_is_refused(self) -> None:
        """`render_brand_css` generates the accent ramp only alongside a brand
        ramp, so this would be a value that silently never reaches a page."""
        with pytest.raises(ValueError, match="never reach a page"):
            validate_brand_values({"accent_hex": "#06b6d4"})

    def test_the_models_carry_no_css_column(self) -> None:
        """ADR-0006 D8 made structural: a `custom_css` column cannot be added
        because there is nothing here that could render it."""
        for model in (BrandProfile, PlatformBrandProfile):
            names = set(model.__table__.columns.keys())
            for forbidden in ("custom_css", "css", "stylesheet", "theme_css"):
                assert forbidden not in names, model.__tablename__


class TestLegacyTranslationReportsWhatItCannotCarry:
    """`dotmac_ui.BrandWarning`'s rule applied to migration: unsupported input is
    reported to the caller, never quietly dropped."""

    def test_subs_two_colour_columns_translate(self) -> None:
        result = translate_legacy_brand_values(
            {"primary_color": "#206a07", "secondary_color": "#06b6d4"}
        )
        assert result.accepted == {
            "primary_hex": "#206a07",
            "accent_hex": "#06b6d4",
        }
        assert result.is_lossless

    def test_already_migrated_spellings_pass_through(self) -> None:
        """So a cutover can hand this a partially-migrated record without
        special-casing it."""
        result = translate_legacy_brand_values({"primary_hex": "#206a07"})
        assert result.accepted == {"primary_hex": "#206a07"}

    def test_the_semantic_quintet_is_reported_as_owned_by_a_published_token(
        self,
    ) -> None:
        """RULED 2026-08-19. The objection is ownership, not safety: Sub already
        constrains these to known tones, 6-digit hex and WCAG AA in both themes.
        `dotmac_ui.SEMANTIC_INTENTS` publishes the same five names as tokens with
        built-in ramps, so a per-profile override would be a second authority."""
        result = translate_legacy_brand_values(
            {
                "primary_color": "#206a07",
                "positive": "#15803d",
                "info": "#1d4ed8",
                "warning": "#a16207",
                "negative": "#b91c1c",
                "neutral": "#475569",
            }
        )
        assert result.accepted == {"primary_hex": "#206a07"}
        assert not result.is_lossless
        assert len(result.unsupported) == 5
        assert all(
            item.disposition is Disposition.OWNED_BY_PUBLISHED_TOKEN
            for item in result.unsupported
        )

    def test_subs_flat_semantic_spellings_are_recognised_too(self) -> None:
        """Sub uses `semantic_<tone>_color` in its static map and the bare tone
        in `metadata_`. A translation that knew only one spelling would silently
        pass the other through to `NOT_AN_ALLOWLISTED_INPUT` and lose the
        actionable reason."""
        result = translate_legacy_brand_values(
            {
                "semantic_positive_color": "#15803d",
                "brand_semantic_info_color": "#1d4ed8",
            }
        )
        assert len(result.unsupported) == 2
        assert all(
            item.disposition is Disposition.OWNED_BY_PUBLISHED_TOKEN
            for item in result.unsupported
        )

    def test_the_reported_value_is_carried_not_just_the_key(self) -> None:
        """A cutover reviewer deciding whether a tone mattered needs to see what
        it was."""
        result = translate_legacy_brand_values({"positive": "#15803d"})
        assert result.unsupported[0].value == "#15803d"
        assert result.unsupported[0].source_key == "positive"
        assert "SEMANTIC_INTENTS" in result.unsupported[0].detail or (
            "semantic" in result.unsupported[0].detail
        )

    def test_an_unknown_key_is_a_different_disposition(self) -> None:
        """`OWNED_BY_PUBLISHED_TOKEN` has a path forward — change the published
        token. `NOT_AN_ALLOWLISTED_INPUT` does not. Collapsing them would leave
        an operator unable to tell which."""
        result = translate_legacy_brand_values({"glow_colour": "#ff00ff"})
        assert result.unsupported[0].disposition is (
            Disposition.NOT_AN_ALLOWLISTED_INPUT
        )

    def test_unset_values_are_skipped_not_reported(self) -> None:
        """An unset column is not an unsupported value, and reporting it would
        bury the five that matter under every blank optional field."""
        result = translate_legacy_brand_values(
            {"primary_color": "#206a07", "secondary_color": None, "positive": None}
        )
        assert result.is_lossless
        assert result.accepted == {"primary_hex": "#206a07"}


# ── The tenant plane, end to end ────────────────────────────────────────────


class TestTenantProfiles:
    def test_an_upsert_lands_as_draft(self, db) -> None:
        """A profile becomes resolvable only by explicit activation. Creating
        straight to active would put a half-entered brand in front of customers
        the moment someone opened the form."""
        profile_id = upsert_tenant_profile(
            db,
            UpsertTenantProfileCommand(
                command_id=_cmd(),
                tenant_id=_TENANT,
                scope_type="tenant",
                profile_code="default",
                fields=ProfileFields(display_name="Base"),
            ),
        )
        row = db.get(BrandProfile, profile_id)
        assert row is not None
        assert row.status == ProfileStatus.DRAFT.value

    def test_a_draft_does_not_resolve(self, db) -> None:
        upsert_tenant_profile(
            db,
            UpsertTenantProfileCommand(
                command_id=_cmd(),
                tenant_id=_TENANT,
                scope_type="tenant",
                profile_code="default",
                fields=ProfileFields(display_name="Base"),
            ),
        )
        resolved = resolve_for_tenant(db, tenant_id=_TENANT, chain=[("tenant", None)])
        assert resolved.values == {}

    def test_an_activated_profile_resolves(self, db) -> None:
        profile_id = upsert_tenant_profile(
            db,
            UpsertTenantProfileCommand(
                command_id=_cmd(),
                tenant_id=_TENANT,
                scope_type="tenant",
                profile_code="default",
                fields=ProfileFields(display_name="Base", legal_name="Base Ltd"),
            ),
        )
        activate_tenant_profile(
            db, command_id=_cmd(), tenant_id=_TENANT, profile_id=profile_id
        )
        resolved = resolve_for_tenant(db, tenant_id=_TENANT, chain=[("tenant", None)])
        assert resolved.get("display_name") == "Base"
        assert resolved.source_of("legal_name") == "tenant"

    def test_an_upsert_leaves_unset_fields_alone(self, db) -> None:
        """`None` means "leave alone", never "clear" — the behaviour an admin
        form that posts one section needs, and the opposite of what a naive
        `setattr` loop over every attribute would do."""
        profile_id = upsert_tenant_profile(
            db,
            UpsertTenantProfileCommand(
                command_id=_cmd(),
                tenant_id=_TENANT,
                scope_type="tenant",
                profile_code="default",
                fields=ProfileFields(display_name="Base", legal_name="Base Ltd"),
            ),
        )
        upsert_tenant_profile(
            db,
            UpsertTenantProfileCommand(
                command_id=_cmd(),
                tenant_id=_TENANT,
                scope_type="tenant",
                profile_code="default",
                fields=ProfileFields(display_name="Renamed"),
            ),
        )
        row = db.get(BrandProfile, profile_id)
        assert row is not None
        assert row.display_name == "Renamed"
        assert row.legal_name == "Base Ltd"

    def test_replaying_a_command_id_does_not_write_twice(self, db) -> None:
        command = UpsertTenantProfileCommand(
            command_id="cmd-fixed",
            tenant_id=_TENANT,
            scope_type="tenant",
            profile_code="default",
            fields=ProfileFields(display_name="Base"),
        )
        first = upsert_tenant_profile(db, command)
        second = upsert_tenant_profile(db, command)
        assert first == second
        row = db.get(BrandProfile, first)
        assert row is not None
        assert row.record_version == 1

    def test_a_profile_with_no_display_name_cannot_activate(self, db) -> None:
        profile_id = upsert_tenant_profile(
            db,
            UpsertTenantProfileCommand(
                command_id=_cmd(),
                tenant_id=_TENANT,
                scope_type="tenant",
                profile_code="default",
                fields=ProfileFields(display_name="Base"),
            ),
        )
        row = db.get(BrandProfile, profile_id)
        assert row is not None
        row.display_name = ""
        db.flush()
        with pytest.raises(ProfileRefusedError, match="nothing to show"):
            activate_tenant_profile(
                db, command_id=_cmd(), tenant_id=_TENANT, profile_id=profile_id
            )


# ── The platform plane and host bindings ────────────────────────────────────


class TestPlatformProfilesAndHosts:
    def _active_platform(self, db, code: str = "ndic-academy"):  # type: ignore[no-untyped-def]
        profile_id = upsert_platform_profile(
            db,
            UpsertPlatformProfileCommand(
                command_id=_cmd(),
                profile_code=code,
                fields=ProfileFields(
                    display_name="NDIC Academy", primary_hex="#123456"
                ),
            ),
        )
        activate_platform_profile(db, command_id=_cmd(), profile_id=profile_id)
        return profile_id

    def test_a_host_resolves_to_a_brand_before_any_tenant_exists(self, db) -> None:
        """The property that makes a brand profile something other than a tenant
        setting: the answer is available before a tenant is resolved."""
        profile_id = self._active_platform(db)
        bind_host(
            db,
            command_id=_cmd(),
            host="learn.ndic.example",
            profile_id=profile_id,
            is_canonical=True,
        )
        resolved = resolve_by_host(db, "learn.ndic.example")
        assert resolved is not None
        assert resolved.get("display_name") == "NDIC Academy"
        assert resolved.source_of("display_name") == "platform"

    def test_one_artifact_presents_two_brands_at_two_hosts(self, db) -> None:
        """The requirement in one test: the same released artifact appearing as
        Dotmac Academy and as NDIC Academy through approved profiles."""
        ndic = self._active_platform(db, "ndic-academy")
        dotmac = upsert_platform_profile(
            db,
            UpsertPlatformProfileCommand(
                command_id=_cmd(),
                profile_code="dotmac-academy",
                fields=ProfileFields(display_name="Dotmac Academy"),
            ),
        )
        activate_platform_profile(db, command_id=_cmd(), profile_id=dotmac)
        bind_host(db, command_id=_cmd(), host="learn.ndic.example", profile_id=ndic)
        bind_host(
            db, command_id=_cmd(), host="academy.dotmac.example", profile_id=dotmac
        )

        first = resolve_by_host(db, "learn.ndic.example")
        second = resolve_by_host(db, "academy.dotmac.example")
        assert first is not None and second is not None
        assert first.get("display_name") == "NDIC Academy"
        assert second.get("display_name") == "Dotmac Academy"

    def test_an_unbound_host_resolves_to_nothing(self, db) -> None:
        """Fail-open would mean serving whichever brand happened to be first."""
        assert resolve_by_host(db, "unknown.example") is None

    def test_a_draft_profile_does_not_resolve_by_host(self, db) -> None:
        profile_id = upsert_platform_profile(
            db,
            UpsertPlatformProfileCommand(
                command_id=_cmd(),
                profile_code="pending",
                fields=ProfileFields(display_name="Pending"),
            ),
        )
        bind_host(db, command_id=_cmd(), host="pending.example", profile_id=profile_id)
        assert resolve_by_host(db, "pending.example") is None

    def test_an_unnormalised_host_is_refused(self, db) -> None:
        """This module binds hosts and does not normalise them: two normalisers
        eventually disagree, at which point one binds a host the other cannot
        find."""
        profile_id = self._active_platform(db)
        with pytest.raises(HostBindingRefusedError, match="not normalised"):
            bind_host(
                db,
                command_id=_cmd(),
                host="Learn.NDIC.Example",
                profile_id=profile_id,
            )

    def test_rebinding_a_host_moves_it_rather_than_duplicating(self, db) -> None:
        """A host has one brand. Two rows would make resolution order-dependent,
        and the order would be whatever the query planner chose that day."""
        from dotmac_brand_profiles import PlatformBrandHostBinding

        first = self._active_platform(db, "ndic-academy")
        second = self._active_platform(db, "other-academy")
        bind_host(db, command_id=_cmd(), host="shared.example", profile_id=first)
        bind_host(db, command_id=_cmd(), host="shared.example", profile_id=second)
        rows = (
            db.query(PlatformBrandHostBinding)
            .filter(PlatformBrandHostBinding.host == "shared.example")
            .all()
        )
        assert len(rows) == 1
        assert rows[0].profile_id == second


# ── Transaction authority ───────────────────────────────────────────────────


class TestTheModuleOwnsNoTransaction:
    def test_nothing_is_committed_so_a_rollback_discards_it(self, db) -> None:
        profile_id = upsert_tenant_profile(
            db,
            UpsertTenantProfileCommand(
                command_id=_cmd(),
                tenant_id=_TENANT,
                scope_type="tenant",
                profile_code="default",
                fields=ProfileFields(display_name="Base"),
            ),
        )
        db.rollback()
        assert db.get(BrandProfile, profile_id) is None
