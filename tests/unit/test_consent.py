"""Parity proof for the ported consent ledger (ADR-0006 § 5c).

Sub's `tests/test_communication_eligibility.py` (14) and
`tests/test_notification_queue_suppression.py` (4), ported with the code they
prove. The product-first amendment requires the behaviour tests to come across
with the implementation — a port whose proof stayed behind is a rewrite.

What changed in the port, and why each change is faithful:

- **Tenanted.** Every call takes a `tenant_id`; Sub is single-tenant. The rules
  under test are unchanged, and cross-tenant isolation is proven separately
  against real Postgres in `tests/test_consent_isolation.py` — SQLite has no RLS,
  so nothing here may be read as evidence of isolation.
- **Marketing categories are registered, not hardcoded.** Sub owns
  `MARKETING_CATEGORIES = {"marketing", "campaign", "promotion"}`; a
  product-neutral kernel cannot. The MECHANISM under test — transactional unless
  declared — is identical.
- **`ValueError` → `ConsentError`.** A kernel-typed error, still a `ValueError`
  subclass, so a caller catching the broad form keeps working.
- **The four `*_committed` wrappers are not ported** and have no tests here:
  `dotmac_kernel.db` is the one transaction authority.

Tenancy note: these run on SQLite with no RLS. `tenant_id` is passed explicitly
and the queries filter on it, which is what is under test — not the database's
enforcement of it.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from dotmac_kernel import consent
from dotmac_kernel.consent import ConsentError
from dotmac_kernel.consent_models import (
    REASON_BOUNCE,
    REASON_UNSUBSCRIBE,
    SCOPE_ALL,
    SCOPE_MARKETING,
)
from dotmac_kernel.models import Tenant


@pytest.fixture(autouse=True)
def _registries():
    """The registries are process-global import-time declarations, so a test
    that registers a category would otherwise leak it into every later test."""
    previous_marketing, previous_numeric = consent._reset_registries_for_tests(
        marketing=("marketing", "campaign", "promotion")
    )
    yield
    consent._reset_registries_for_tests(
        marketing=previous_marketing, numeric=previous_numeric
    )


@pytest.fixture
def tenant(db) -> Tenant:
    tenant = Tenant(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    db.add(tenant)
    db.flush()
    return tenant


# ── The distinction that matters ────────────────────────────────────────────


def test_unsubscribe_stops_marketing(db, tenant) -> None:
    consent.suppress(db, tenant.id, channel="email", address="jane@example.com")
    assert not consent.may_send(
        db, tenant.id, channel="email", address="jane@example.com", category="marketing"
    )


def test_unsubscribe_does_not_stop_an_invoice(db, tenant) -> None:
    """THE test. An unsubscribe is a refusal of marketing; it is not permission
    to stop sending someone their invoice."""
    consent.suppress(db, tenant.id, channel="email", address="jane@example.com")
    assert consent.may_send(
        db, tenant.id, channel="email", address="jane@example.com", category="billing"
    )


def test_an_all_scoped_suppression_stops_a_transactional_send(db, tenant) -> None:
    """Sub: `test_notification_queue_suppression`. A hard bounce stops even the
    invoice — there is nowhere to deliver it."""
    consent.suppress(
        db,
        tenant.id,
        channel="email",
        address="gone@example.com",
        scope=SCOPE_ALL,
        reason=REASON_BOUNCE,
    )
    assert not consent.may_send(
        db, tenant.id, channel="email", address="gone@example.com", category="billing"
    )


def test_an_unknown_category_is_treated_as_transactional(db, tenant) -> None:
    """Defaulting the other way would mean a typo could stop someone's invoices."""
    consent.suppress(db, tenant.id, channel="email", address="jane@example.com")
    assert consent.may_send(
        db,
        tenant.id,
        channel="email",
        address="jane@example.com",
        category="mrketing",  # deliberate typo
    )


def test_an_undeclared_deployment_suppresses_nothing_by_marketing(db, tenant) -> None:
    """A product that declares no marketing categories still gets a working
    ledger — only `all` bites. That is the safe direction."""
    consent._reset_registries_for_tests(marketing=())
    consent.suppress(db, tenant.id, channel="email", address="jane@example.com")
    assert consent.may_send(
        db, tenant.id, channel="email", address="jane@example.com", category="marketing"
    )


# ── Address canonicalisation ────────────────────────────────────────────────


def test_email_suppression_is_case_insensitive(db, tenant) -> None:
    consent.suppress(db, tenant.id, channel="email", address="Jane@Example.COM")
    assert not consent.may_send(
        db, tenant.id, channel="email", address="jane@example.com", category="marketing"
    )


def test_phone_suppression_ignores_punctuation(db, tenant) -> None:
    consent.suppress(db, tenant.id, channel="sms", address="+234 801 234 5678")
    assert not consent.may_send(
        db, tenant.id, channel="sms", address="2348012345678", category="marketing"
    )


def test_the_raw_address_is_kept_for_audit(db, tenant) -> None:
    row = consent.suppress(db, tenant.id, channel="email", address="Jane@Example.COM")
    assert row.address == "jane@example.com"
    assert row.raw_address == "Jane@Example.COM"


def test_channels_are_independent(db, tenant) -> None:
    consent.suppress(db, tenant.id, channel="email", address="jane@example.com")
    assert consent.may_send(
        db, tenant.id, channel="sms", address="jane@example.com", category="marketing"
    )


# ── Escalation ──────────────────────────────────────────────────────────────


def test_suppress_is_idempotent(db, tenant) -> None:
    first = consent.suppress(db, tenant.id, channel="email", address="a@example.com")
    second = consent.suppress(db, tenant.id, channel="email", address="a@example.com")
    assert first.id == second.id


def test_scope_escalates_marketing_to_all(db, tenant) -> None:
    consent.suppress(db, tenant.id, channel="email", address="a@example.com")
    consent.suppress(
        db,
        tenant.id,
        channel="email",
        address="a@example.com",
        scope=SCOPE_ALL,
        reason=REASON_BOUNCE,
    )
    assert not consent.may_send(
        db, tenant.id, channel="email", address="a@example.com", category="billing"
    )


def test_scope_never_de_escalates(db, tenant) -> None:
    """A hard bounce must not be downgraded by a later unsubscribe click."""
    consent.suppress(
        db,
        tenant.id,
        channel="email",
        address="a@example.com",
        scope=SCOPE_ALL,
        reason=REASON_BOUNCE,
    )
    consent.suppress(
        db,
        tenant.id,
        channel="email",
        address="a@example.com",
        scope=SCOPE_MARKETING,
        reason=REASON_UNSUBSCRIBE,
    )
    assert not consent.may_send(
        db, tenant.id, channel="email", address="a@example.com", category="billing"
    )


# ── Removing a suppression ──────────────────────────────────────────────────


def test_unsuppress_restores_sending(db, tenant) -> None:
    consent.suppress(db, tenant.id, channel="email", address="a@example.com")
    assert consent.unsuppress(db, tenant.id, channel="email", address="a@example.com")
    assert consent.may_send(
        db, tenant.id, channel="email", address="a@example.com", category="marketing"
    )


def test_unsuppress_marketing_will_not_clear_a_hard_bounce(db, tenant) -> None:
    """Campaign administration is not authority to clear a bounce, whose `all`
    scope also protects transactional delivery."""
    consent.suppress(
        db,
        tenant.id,
        channel="email",
        address="gone@example.com",
        scope=SCOPE_ALL,
        reason=REASON_BOUNCE,
    )
    assert not consent.unsuppress_marketing(
        db, tenant.id, channel="email", address="gone@example.com"
    )
    assert not consent.may_send(
        db, tenant.id, channel="email", address="gone@example.com", category="billing"
    )


# ── Bulk ────────────────────────────────────────────────────────────────────


def test_filter_eligible_matches_the_single_address_rule(db, tenant) -> None:
    """The bulk path must not drift from the single path — a campaign
    hand-rolling a per-recipient loop is exactly how Sub's filter came to
    disagree with every other sender."""
    consent.suppress(db, tenant.id, channel="email", address="no@example.com")
    consent.suppress(
        db,
        tenant.id,
        channel="email",
        address="gone@example.com",
        scope=SCOPE_ALL,
        reason=REASON_BOUNCE,
    )
    addresses = ["yes@example.com", "no@example.com", "gone@example.com"]

    marketing = consent.filter_eligible(
        db, tenant.id, channel="email", addresses=addresses, category="marketing"
    )
    assert marketing == ["yes@example.com"]

    billing = consent.filter_eligible(
        db, tenant.id, channel="email", addresses=addresses, category="billing"
    )
    assert billing == ["yes@example.com", "no@example.com"]

    for address in addresses:
        for category in ("marketing", "billing"):
            single = consent.may_send(
                db, tenant.id, channel="email", address=address, category=category
            )
            bulk = address in consent.filter_eligible(
                db, tenant.id, channel="email", addresses=[address], category=category
            )
            assert single == bulk, (address, category)


def test_filter_eligible_preserves_the_callers_address_form(db, tenant) -> None:
    result = consent.filter_eligible(
        db,
        tenant.id,
        channel="email",
        addresses=["Jane@Example.COM"],
        category="billing",
    )
    assert result == ["Jane@Example.COM"]


# ── Edges ───────────────────────────────────────────────────────────────────


def test_an_empty_address_is_not_a_consent_decision(db, tenant) -> None:
    """A missing address is a DELIVERY bug. Reporting it as "suppressed" would
    hide that bug as a consent outcome; the sender must fail on its own terms."""
    assert consent.may_send(
        db, tenant.id, channel="email", address="", category="marketing"
    )
    assert (
        consent.suppression_reason(
            db, tenant.id, channel="email", address=None, category="marketing"
        )
        is None
    )


def test_suppressing_an_empty_address_is_refused(db, tenant) -> None:
    with pytest.raises(ConsentError, match="empty address"):
        consent.suppress(db, tenant.id, channel="email", address="   ")


def test_an_unknown_scope_or_reason_is_refused(db, tenant) -> None:
    """The vocabularies are closed and legal, not product-shaped — an unknown
    value is a bug, not a new category."""
    with pytest.raises(ConsentError, match="scope"):
        consent.suppress(
            db, tenant.id, channel="email", address="a@example.com", scope="sometimes"
        )
    with pytest.raises(ConsentError, match="reason"):
        consent.suppress(
            db,
            tenant.id,
            channel="email",
            address="a@example.com",
            reason="felt like it",
        )


def test_suppression_reason_reports_why(db, tenant) -> None:
    consent.suppress(
        db,
        tenant.id,
        channel="email",
        address="gone@example.com",
        scope=SCOPE_ALL,
        reason=REASON_BOUNCE,
    )
    assert (
        consent.suppression_reason(
            db,
            tenant.id,
            channel="email",
            address="gone@example.com",
            category="billing",
        )
        == REASON_BOUNCE
    )


def test_one_tenants_suppression_does_not_bind_another(db, tenant) -> None:
    """The service filters on tenant. This is the LOGIC check; the database's
    enforcement of it is proven in `tests/test_consent_isolation.py`."""
    other = Tenant(name="Other", slug=f"other-{uuid4().hex[:8]}")
    db.add(other)
    db.flush()
    consent.suppress(db, tenant.id, channel="email", address="jane@example.com")
    assert consent.may_send(
        db, other.id, channel="email", address="jane@example.com", category="marketing"
    )
