"""Template seeding: create the defaults, never clobber an edit.

The gap the 2026-08-10 audit found — Template Studio shipped with no seeding
mechanism at all, and named Sub's as the qualifying source: upsert by identity,
never overwrite an operator's edit, derive from the executable spec.

Rule 2 is the one worth guarding hardest. A deploy that silently reverted a
tenant's wording would be indistinguishable from data loss, and it would be
discovered by a customer receiving the wrong message.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from dotmac_kernel.exceptions import BadRequestError
from dotmac_kernel.models import Tenant
from dotmac_template_studio import TemplateSeed, seed_templates, service
from dotmac_template_studio.contexts import RenderContext, _reset_for_tests

CONTEXT = RenderContext(
    name="billing",
    variables=("customer_name", "invoice_number"),
    description="Fixture vocabulary.",
)

SEEDS = (
    TemplateSeed(
        slug="invoice-issued",
        channel="email",
        context="billing",
        name="Invoice issued",
        subject="Invoice {invoice_number}",
        body="Hi {customer_name}, invoice {invoice_number} is ready.",
    ),
    TemplateSeed(
        slug="invoice-issued",
        channel="sms",
        context="billing",
        name="Invoice issued (SMS)",
        body="Invoice {invoice_number} is ready.",
    ),
)


@pytest.fixture(autouse=True)
def _registry():
    previous = _reset_for_tests([CONTEXT])
    yield
    _reset_for_tests(previous.values())


@pytest.fixture
def tenant(db) -> Tenant:
    tenant = Tenant(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    db.add(tenant)
    db.flush()
    return tenant


def test_seeding_creates_each_template(db, tenant) -> None:
    outcome = seed_templates(db, tenant.id, SEEDS)
    assert outcome.created == ("invoice-issued/email", "invoice-issued/sms")
    assert outcome.skipped == ()
    assert outcome.total == 2


def test_a_seeded_template_is_immediately_renderable(db, tenant) -> None:
    """A seeded DRAFT would be invisible — `render_published` refuses a template
    with no published version — so seeding publishes version 1."""
    seed_templates(db, tenant.id, SEEDS)
    subject, body = service.render_published(
        db,
        tenant.id,
        "invoice-issued",
        "email",
        {"customer_name": "Jane", "invoice_number": "INV-1"},
    )
    assert subject == "Invoice INV-1"
    assert body == "Hi Jane, invoice INV-1 is ready."


def test_seeding_twice_creates_nothing_the_second_time(db, tenant) -> None:
    """Safe on every deploy, which is how it is meant to be run."""
    seed_templates(db, tenant.id, SEEDS)
    outcome = seed_templates(db, tenant.id, SEEDS)
    assert outcome.created == ()
    assert outcome.skipped == ("invoice-issued/email", "invoice-issued/sms")


def test_seeding_never_clobbers_an_operators_edit(db, tenant) -> None:
    """THE test. An operator's wording is the whole point of a tenant-authored
    template; a deploy reverting it is data loss wearing a deploy's clothes."""
    seed_templates(db, tenant.id, SEEDS)
    template = service.get_by_slug(db, tenant.id, "invoice-issued", "email")
    edited = service.create_version(
        db, tenant.id, template.id, body="Our own wording, {customer_name}."
    )
    service.publish_version(db, tenant.id, template.id, edited.version)

    seed_templates(db, tenant.id, SEEDS)

    _, body = service.render_published(
        db,
        tenant.id,
        "invoice-issued",
        "email",
        {"customer_name": "Jane", "invoice_number": "INV-1"},
    )
    assert body == "Our own wording, Jane."
    assert len(service.list_versions(db, tenant.id, template.id)) == 2


def test_the_same_slug_seeds_independently_per_channel(db, tenant) -> None:
    """The identity is `(slug, channel)` — the shape Sub's seeder needs, since it
    seeds one referral-reward code for both push and email."""
    seed_templates(db, tenant.id, SEEDS)
    assert (
        service.get_by_slug(db, tenant.id, "invoice-issued", "email").channel == "email"
    )
    assert service.get_by_slug(db, tenant.id, "invoice-issued", "sms").channel == "sms"


def test_seeds_are_isolated_per_tenant(db, tenant) -> None:
    other = Tenant(name="Other", slug=f"other-{uuid4().hex[:8]}")
    db.add(other)
    db.flush()
    seed_templates(db, tenant.id, SEEDS)
    outcome = seed_templates(db, other.id, SEEDS)
    assert outcome.created == ("invoice-issued/email", "invoice-issued/sms")


def test_a_seed_whose_placeholders_the_context_cannot_supply_fails_the_deploy(
    db, tenant
) -> None:
    """Validated at seed time, so a broken default fails the deploy rather than
    the send."""
    bad = TemplateSeed(
        slug="broken",
        channel="email",
        context="billing",
        name="Broken",
        body="Hi {not_a_declared_variable}",
    )
    with pytest.raises(BadRequestError, match="cannot supply"):
        seed_templates(db, tenant.id, (bad,))


def test_a_seed_naming_an_unregistered_context_fails_loudly(db, tenant) -> None:
    bad = TemplateSeed(
        slug="orphan",
        channel="email",
        context="nonexistent",
        name="Orphan",
        body="Hi {customer_name}",
    )
    with pytest.raises(BadRequestError, match="unknown render context"):
        seed_templates(db, tenant.id, (bad,))
