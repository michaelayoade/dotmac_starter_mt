"""One artifact, two brands: isolation and contrast, end to end.

The canary Michael's 2026-08-19 ruling asked for, and the one that exercises the
whole three-way boundary in a single test file:

- **`dotmac-brand-profiles`** supplies scoped values, provenance, precedence and
  locks.
- **The assembly** maps them into `dotmac_ui.BrandOverride` — done here exactly
  as an assembly would, because the module deliberately does not.
- **`dotmac-ui`** projects to CSS and validates contrast.

Two properties, and neither is sufficient alone:

1. **Isolation.** Two brand profiles resolved from ONE deployment must not leak
   into each other — not in values, not in locks, and not in the generated CSS.
   A resolver that merged the wrong layer would show the OEM's colours on the
   operator's portal, which is the failure that makes white-labelling unsellable.
2. **Contrast.** The generated palette is held to `dotmac-ui`'s OWN contrast
   contract. Asserting only "no warnings" would pass against a renderer that had
   stopped checking, so the sensitivity half drives a deliberately bad brand and
   asserts warnings ARE produced.
"""

from __future__ import annotations

import uuid
from collections.abc import Generator

import pytest
from dotmac_brand_profiles import (
    BRAND_OVERRIDE_INPUTS,
    ProfileFields,
    UpsertPlatformProfileCommand,
    UpsertTenantProfileCommand,
    activate_platform_profile,
    activate_tenant_profile,
    bind_host,
    module,
    resolve_by_host,
    resolve_for_tenant,
    upsert_platform_profile,
    upsert_tenant_profile,
)
from dotmac_kernel.audit_actions import AuditActionRegistry, install_audit_actions
from dotmac_kernel.models import Base
from dotmac_ui import BrandOverride, render_brand_css
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

_TENANT = uuid.UUID("11111111-1111-1111-1111-111111111111")

#: Two clearly distinct brands, both plausible as real operator colours.
_DOTMAC = {"primary_hex": "#206a07", "accent_hex": "#06b6d4"}
_NDIC = {"primary_hex": "#7c2d12", "accent_hex": "#a21caf"}


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


def _as_assembly_would(resolved) -> BrandOverride | None:  # type: ignore[no-untyped-def]
    """The assembly's mapping, written here because the module does not own it.

    Driven through `BRAND_OVERRIDE_INPUTS` rather than hard-coded field names, so
    this test exercises the same allowlist a real assembly would read — and would
    break the same way if the allowlist and `BrandOverride` ever disagreed.
    """
    mapped = {
        override_field: resolved.get(column)
        for column, override_field in BRAND_OVERRIDE_INPUTS.items()
    }
    if not mapped.get("primary"):
        return None
    return BrandOverride(**mapped)


def _brand(db: Session, code: str, host: str, values: dict[str, str]) -> None:
    """One platform brand, activated and bound to a host."""
    profile_id = upsert_platform_profile(
        db,
        UpsertPlatformProfileCommand(
            command_id=_cmd(),
            profile_code=code,
            fields=ProfileFields(display_name=code, **values),
        ),
    )
    activate_platform_profile(db, command_id=_cmd(), profile_id=profile_id)
    bind_host(
        db, command_id=_cmd(), host=host, profile_id=profile_id, is_canonical=True
    )


# ── Isolation ───────────────────────────────────────────────────────────────


class TestOneArtifactTwoBrandsStayIsolated:
    @pytest.fixture
    def two_brands(self, db: Session) -> Session:
        _brand(db, "dotmac-academy", "academy.dotmac.example", _DOTMAC)
        _brand(db, "ndic-academy", "learn.ndic.example", _NDIC)
        return db

    def test_each_host_resolves_only_its_own_values(self, two_brands) -> None:
        first = resolve_by_host(two_brands, "academy.dotmac.example")
        second = resolve_by_host(two_brands, "learn.ndic.example")
        assert first is not None and second is not None
        assert first.get("primary_hex") == _DOTMAC["primary_hex"]
        assert second.get("primary_hex") == _NDIC["primary_hex"]
        assert first.get("accent_hex") != second.get("accent_hex")

    def test_neither_brands_css_contains_the_others_colours(self, two_brands) -> None:
        """The property a values-only assertion misses: a resolver could return
        the right values and a renderer still seed both ramps from one palette."""
        first = render_brand_css(
            _as_assembly_would(resolve_by_host(two_brands, "academy.dotmac.example"))
        )
        second = render_brand_css(
            _as_assembly_would(resolve_by_host(two_brands, "learn.ndic.example"))
        )

        assert _DOTMAC["primary_hex"] in first.css
        assert _NDIC["primary_hex"] in second.css
        # The seed is pinned verbatim into its own ramp, so each other's seed
        # must be absent — bleed would show up here even if the values were right.
        assert _NDIC["primary_hex"] not in first.css
        assert _DOTMAC["primary_hex"] not in second.css
        assert _NDIC["accent_hex"] not in first.css
        assert _DOTMAC["accent_hex"] not in second.css

    def test_an_unbound_host_gets_neither_brand(self, two_brands) -> None:
        """Fail-open would serve whichever brand happened to be first."""
        assert resolve_by_host(two_brands, "unknown.example") is None

    def test_a_lock_on_one_brand_does_not_bind_the_other(self, db) -> None:
        """Locks are per resolution chain. A lock that leaked across brands
        would let one OEM freeze another's identity."""
        pinned = upsert_platform_profile(
            db,
            UpsertPlatformProfileCommand(
                command_id=_cmd(),
                profile_code="pinned",
                fields=ProfileFields(display_name="Pinned", legal_name="Pinned Ltd"),
                lock=["legal_name"],
            ),
        )
        activate_platform_profile(db, command_id=_cmd(), profile_id=pinned)
        bind_host(db, command_id=_cmd(), host="pinned.example", profile_id=pinned)
        _brand(db, "free", "free.example", _NDIC)

        locked = resolve_by_host(db, "pinned.example")
        free = resolve_by_host(db, "free.example")
        assert locked is not None and free is not None
        assert "legal_name" in locked.locked
        assert "legal_name" not in free.locked

    def test_a_tenant_brand_does_not_leak_into_a_platform_brand(self, db) -> None:
        """The two planes hold separate records that a resolver merges at read
        time — never rows that point at each other (hard rule 27)."""
        _brand(db, "oem", "oem.example", _NDIC)
        tenant_profile = upsert_tenant_profile(
            db,
            UpsertTenantProfileCommand(
                command_id=_cmd(),
                tenant_id=_TENANT,
                scope_type="tenant",
                profile_code="operator",
                fields=ProfileFields(display_name="Operator", **_DOTMAC),
            ),
        )
        activate_tenant_profile(
            db, command_id=_cmd(), tenant_id=_TENANT, profile_id=tenant_profile
        )

        oem = resolve_by_host(db, "oem.example")
        operator = resolve_for_tenant(db, tenant_id=_TENANT, chain=[("tenant", None)])
        assert oem is not None
        assert oem.get("primary_hex") == _NDIC["primary_hex"]
        assert operator.get("primary_hex") == _DOTMAC["primary_hex"]
        assert oem.source_of("primary_hex") == "platform"
        assert operator.source_of("primary_hex") == "tenant"


# ── Contrast ────────────────────────────────────────────────────────────────


class TestContrastIsCheckedByDotmacUi:
    """The contrast half, written against what `dotmac-ui` actually does.

    An earlier draft of this canary tried to prove sensitivity by driving a
    near-white brand and asserting warnings. It produced none — and the reason
    turned out to be a property worth asserting rather than a hole worth
    working around:

        generate_ramp("#ffffe0")["600"] == "#6e7800"

    `generate_ramp` places the seed at the step whose target lightness is closest
    to its own (here step 50) and drives every other step to a FIXED lightness
    target. So a brand seed cannot produce an unreadable palette however light it
    is — the ramp is clamped by construction.

    That makes "a brand that fails contrast" the wrong sensitivity probe. The
    right ones are below: prove the contract is live by failing it directly, and
    prove the clamping mechanism that makes seeds safe.
    """

    def test_both_brands_clear_the_published_contrast_contract(self, db) -> None:
        _brand(db, "dotmac-academy", "academy.dotmac.example", _DOTMAC)
        _brand(db, "ndic-academy", "learn.ndic.example", _NDIC)
        for host in ("academy.dotmac.example", "learn.ndic.example"):
            generated = render_brand_css(_as_assembly_would(resolve_by_host(db, host)))
            assert generated.is_clean, (
                host,
                [warning.message for warning in generated.warnings],
            )

    def test_the_contrast_contract_is_live_not_vacuous(self) -> None:
        """Sensitivity proof, aimed at the CHECKER rather than at a seed.

        Without this, `is_clean` above would pass just as happily against a
        renderer that had stopped checking contrast at all — which is precisely
        the failure a canary exists to catch, and precisely the one a
        clamped-by-construction generator makes invisible from the outside.
        """
        from dotmac_ui.a11y import check_contrast

        unreadable = {
            f"color-brand-{step}": "#ffffff"
            for step in (
                "50",
                "100",
                "200",
                "300",
                "400",
                "500",
                "600",
                "700",
                "800",
                "900",
                "950",
            )
        }
        failures = list(check_contrast(overrides=unreadable))
        assert failures, (
            "an all-white brand ramp must fail the contrast contract; if it "
            "does not, the contract is not being evaluated"
        )

    def test_a_light_seed_is_clamped_rather_than_refused(self, db) -> None:
        """The mechanism that makes every seed safe, asserted directly.

        An operator who enters a very light brand colour gets a usable palette
        rather than a rejection — and the module does not need to police colour
        lightness, because `dotmac-ui` already cannot produce an unreadable ramp.
        """
        from dotmac_ui import generate_ramp, hex_to_oklch

        seed = "#ffffe0"
        ramp = generate_ramp(seed)
        assert seed in ramp.values(), (
            "the seed must appear verbatim somewhere in its own ramp — an "
            "operator has to be able to find the colour they entered"
        )
        assert hex_to_oklch(ramp["600"]).lightness < hex_to_oklch(seed).lightness, (
            "the mid ramp step must be driven darker than a near-white seed; "
            "that clamping is why no seed can fail the contrast contract"
        )

    def test_the_seed_survives_into_the_generated_css(self, db) -> None:
        """D8's rule at the rendering boundary: a warning, never a silent
        adjustment. Clamping the REST of the ramp is documented behaviour; the
        entered colour itself is never quietly replaced."""
        _brand(db, "dotmac-academy", "academy.dotmac.example", _DOTMAC)
        generated = render_brand_css(
            _as_assembly_would(resolve_by_host(db, "academy.dotmac.example"))
        )
        assert _DOTMAC["primary_hex"] in generated.css
        assert _DOTMAC["accent_hex"] in generated.css
