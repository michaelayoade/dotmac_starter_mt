"""Template Studio service logic (SQLite, no RLS — logic only).

Tenancy correctness for this module is proven separately against real Postgres
in `tests/test_template_studio_isolation.py`; nothing here may be read as
evidence of isolation.

The placeholder contract these tests exercise is Sub's, ported under ADR-0006
§ 5b. Its dedicated parity proof lives in `test_template_studio_renderer.py`;
this file covers the service decisions built on top of it.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from dotmac_kernel.exceptions import BadRequestError, ConflictError, NotFoundError
from dotmac_kernel.models import Tenant
from dotmac_template_studio import service
from dotmac_template_studio.contexts import RenderContext, _reset_for_tests
from dotmac_template_studio.models import Template

CONTEXT = RenderContext(
    name="testing",
    variables=("name", "plan", "who", "first_name", "a", "b", "other"),
    description="Fixture vocabulary for the service tests.",
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


def _template(db, tenant: Tenant, **kwargs) -> Template:
    defaults = {
        "slug": "welcome-email",
        "channel": "email",
        "context": "testing",
        "name": "Welcome email",
    }
    return service.create_template(db, tenant.id, **{**defaults, **kwargs})


# ── Variable extraction ─────────────────────────────────────────────────────


def test_variables_are_derived_from_the_body_not_declared() -> None:
    assert service.extract_variables("Hi {name}, your {plan} is ready") == [
        "name",
        "plan",
    ]


def test_variables_include_the_subject() -> None:
    assert service.extract_variables("body", "Welcome {first_name}") == ["first_name"]


def test_variables_are_deduplicated_and_sorted() -> None:
    assert service.extract_variables("{b} {a} {b}") == ["a", "b"]


def test_placeholder_tolerates_inner_whitespace() -> None:
    assert service.extract_variables("{name} and {  other  }") == ["name", "other"]


# ── Rendering ───────────────────────────────────────────────────────────────


def test_render_substitutes_values() -> None:
    assert service.render("Hi {name}", {"name": "Ada"}) == "Hi Ada"


def test_render_is_strict_about_a_missing_value() -> None:
    with pytest.raises(BadRequestError, match="missing value"):
        service.render("Hi {name}", {})


def test_render_non_strict_leaves_the_placeholder_intact() -> None:
    """A preview must not fail just because a value is not supplied yet."""
    assert service.render("Hi {name}", {}, strict=False) == "Hi {name}"


def test_render_does_not_evaluate_expressions() -> None:
    """The body is untrusted operator input — substitution only, never Jinja.

    Neither an expression nor an attribute walk is even a placeholder under this
    contract: the name pattern is a bare identifier, so both survive untouched
    rather than being evaluated.
    """
    body = "{ 7 * 6 } and {name.__class__}"
    assert service.render(body, {"name": "Ada"}, strict=False) == body


# ── Templates ───────────────────────────────────────────────────────────────


def test_create_template_rejects_an_unregistered_context(db, tenant) -> None:
    with pytest.raises(BadRequestError, match="unknown render context"):
        _template(db, tenant, context="carrier-pigeon")


def test_create_template_rejects_a_bad_slug(db, tenant) -> None:
    with pytest.raises(BadRequestError, match="slug"):
        _template(db, tenant, slug="Welcome Email")


def test_create_template_rejects_a_bad_channel(db, tenant) -> None:
    with pytest.raises(BadRequestError, match="channel"):
        _template(db, tenant, channel="E-Mail!")


def test_create_template_rejects_a_duplicate_slug_on_one_channel(db, tenant) -> None:
    _template(db, tenant)
    with pytest.raises(ConflictError):
        _template(db, tenant)


def test_the_same_slug_is_allowed_across_channels(db, tenant) -> None:
    """`(slug, channel)` is the identity — the shape Sub's rows need.

    Sub seeds one referral-reward code for both `push` and `email`; the pre-5b
    `kind`-discriminated shape could not represent that at all.
    """
    _template(db, tenant, slug="invoice-due", channel="email")
    sms = _template(db, tenant, slug="invoice-due", channel="sms")
    assert sms.channel == "sms"


def test_get_by_slug_is_the_caller_facing_lookup(db, tenant) -> None:
    created = _template(db, tenant)
    found = service.get_by_slug(db, tenant.id, "welcome-email", "email")
    assert found.id == created.id


def test_get_template_raises_for_an_unknown_id(db, tenant) -> None:
    with pytest.raises(NotFoundError):
        service.get_template(db, tenant.id, uuid4())


def test_update_template_cannot_move_the_identity_or_context(db, tenant) -> None:
    """Metadata only. Changing the context would silently re-validate every
    existing revision against a different vocabulary."""
    template = _template(db, tenant)
    with pytest.raises(TypeError):
        service.update_template(db, tenant.id, template.id, context="other")  # type: ignore[call-arg]


# ── Versions ────────────────────────────────────────────────────────────────


def test_version_numbers_are_allocated_monotonically(db, tenant) -> None:
    template = _template(db, tenant)
    first = service.create_version(db, tenant.id, template.id, body="one")
    second = service.create_version(db, tenant.id, template.id, body="two")
    assert (first.version, second.version) == (1, 2)


def test_a_new_version_stores_its_derived_variables(db, tenant) -> None:
    template = _template(db, tenant)
    version = service.create_version(
        db, tenant.id, template.id, body="Hi {name}", subject="Hello {name}"
    )
    assert version.variables == ["name"]


def test_a_new_version_is_validated_against_the_templates_context(db, tenant) -> None:
    """The load-bearing rule: an unsendable revision never reaches the database,
    so it can never be published by someone who did not author it."""
    template = _template(db, tenant)
    with pytest.raises(BadRequestError, match="cannot supply"):
        service.create_version(db, tenant.id, template.id, body="Hi {not_declared}")
    assert service.list_versions(db, tenant.id, template.id) == []


def test_a_new_version_rejects_double_braces(db, tenant) -> None:
    template = _template(db, tenant)
    with pytest.raises(BadRequestError, match="double braces"):
        service.create_version(db, tenant.id, template.id, body="Hi {{name}}")


def test_versions_list_newest_first(db, tenant) -> None:
    template = _template(db, tenant)
    service.create_version(db, tenant.id, template.id, body="one")
    service.create_version(db, tenant.id, template.id, body="two")
    assert [v.version for v in service.list_versions(db, tenant.id, template.id)] == [
        2,
        1,
    ]


def test_publishing_points_the_template_at_that_version(db, tenant) -> None:
    template = _template(db, tenant)
    service.create_version(db, tenant.id, template.id, body="one")
    service.publish_version(db, tenant.id, template.id, 1)
    assert template.published_version == 1
    assert service.get_published(db, tenant.id, template.id).version == 1


def test_publishing_is_idempotent_and_keeps_the_first_timestamp(db, tenant) -> None:
    template = _template(db, tenant)
    service.create_version(db, tenant.id, template.id, body="one")
    first = service.publish_version(db, tenant.id, template.id, 1)
    stamped = first.published_at
    again = service.publish_version(db, tenant.id, template.id, 1)
    assert again.published_at == stamped


def test_publishing_a_second_version_keeps_the_first(db, tenant) -> None:
    """History is the point — the superseded revision is not deleted."""
    template = _template(db, tenant)
    service.create_version(db, tenant.id, template.id, body="one")
    service.create_version(db, tenant.id, template.id, body="two")
    service.publish_version(db, tenant.id, template.id, 1)
    service.publish_version(db, tenant.id, template.id, 2)
    assert template.published_version == 2
    assert len(service.list_versions(db, tenant.id, template.id)) == 2


def test_a_draft_version_can_be_edited(db, tenant) -> None:
    template = _template(db, tenant)
    service.create_version(db, tenant.id, template.id, body="draft")
    edited = service.update_version(db, tenant.id, template.id, 1, body="Hi {who}")
    assert edited.body == "Hi {who}"
    assert edited.variables == ["who"]


def test_an_edited_draft_is_re_validated(db, tenant) -> None:
    template = _template(db, tenant)
    service.create_version(db, tenant.id, template.id, body="draft")
    with pytest.raises(BadRequestError, match="cannot supply"):
        service.update_version(db, tenant.id, template.id, 1, body="Hi {smuggled}")


def test_a_published_version_cannot_be_edited(db, tenant) -> None:
    """What was sent must not change after the fact."""
    template = _template(db, tenant)
    service.create_version(db, tenant.id, template.id, body="sent")
    service.publish_version(db, tenant.id, template.id, 1)
    with pytest.raises(ConflictError, match="published"):
        service.update_version(db, tenant.id, template.id, 1, body="rewritten")


# ── Rendering the published revision ────────────────────────────────────────


def test_render_published_uses_the_published_revision(db, tenant) -> None:
    template = _template(db, tenant)
    service.create_version(db, tenant.id, template.id, body="v1 {name}")
    service.create_version(db, tenant.id, template.id, body="v2 {name}")
    service.publish_version(db, tenant.id, template.id, 1)
    subject, body = service.render_published(
        db, tenant.id, "welcome-email", "email", {"name": "Ada"}
    )
    assert body == "v1 Ada"
    assert subject is None


def test_render_published_refuses_a_template_with_only_drafts(db, tenant) -> None:
    template = _template(db, tenant)
    service.create_version(db, tenant.id, template.id, body="draft")
    with pytest.raises(ConflictError, match="no published version"):
        service.render_published(db, tenant.id, "welcome-email", "email", {})


def test_render_published_refuses_an_inactive_template(db, tenant) -> None:
    template = _template(db, tenant)
    service.create_version(db, tenant.id, template.id, body="hello")
    service.publish_version(db, tenant.id, template.id, 1)
    service.update_template(db, tenant.id, template.id, is_active=False)
    with pytest.raises(ConflictError, match="not active"):
        service.render_published(db, tenant.id, "welcome-email", "email", {})
