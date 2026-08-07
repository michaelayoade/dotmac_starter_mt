"""Template Studio service logic (SQLite, no RLS — logic only).

Tenancy correctness for this module is proven separately against real Postgres
in `tests/test_template_studio_isolation.py`; nothing here may be read as
evidence of isolation.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from dotmac_kernel.exceptions import BadRequestError, ConflictError, NotFoundError
from dotmac_kernel.models import Tenant
from dotmac_template_studio import service
from dotmac_template_studio.models import Template


@pytest.fixture
def tenant(db) -> Tenant:
    tenant = Tenant(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    db.add(tenant)
    db.flush()
    return tenant


def _template(db, tenant: Tenant, **kwargs) -> Template:
    defaults = {
        "kind": "notification",
        "slug": "welcome-email",
        "name": "Welcome email",
        "channel": "email",
    }
    return service.create_template(db, tenant.id, **{**defaults, **kwargs})


# ── Variable extraction ─────────────────────────────────────────────────────


def test_variables_are_derived_from_the_body_not_declared() -> None:
    assert service.extract_variables("Hi {{ name }}, your {{ plan }} is ready") == [
        "name",
        "plan",
    ]


def test_variables_include_the_subject() -> None:
    assert service.extract_variables("body", "Welcome {{ first_name }}") == [
        "first_name"
    ]


def test_variables_are_deduplicated_and_sorted() -> None:
    assert service.extract_variables("{{ b }} {{ a }} {{ b }}") == ["a", "b"]


def test_placeholder_tolerates_inner_whitespace() -> None:
    assert service.extract_variables("{{name}} and {{  other  }}") == ["name", "other"]


# ── Rendering ───────────────────────────────────────────────────────────────


def test_render_substitutes_values() -> None:
    assert service.render("Hi {{ name }}", {"name": "Ada"}) == "Hi Ada"


def test_render_is_strict_about_a_missing_value() -> None:
    with pytest.raises(BadRequestError, match="missing value"):
        service.render("Hi {{ name }}", {})


def test_render_non_strict_leaves_the_placeholder_intact() -> None:
    """A preview must not fail just because a value is not supplied yet."""
    assert service.render("Hi {{ name }}", {}, strict=False) == "Hi {{ name }}"


def test_render_does_not_evaluate_expressions() -> None:
    """The body is untrusted operator input — substitution only, never Jinja."""
    body = "{{ 7 * 6 }} and {{ name.__class__ }}"
    assert service.render(body, {}, strict=False) == body


# ── Templates ───────────────────────────────────────────────────────────────


def test_create_template_rejects_an_unknown_kind(db, tenant) -> None:
    with pytest.raises(BadRequestError, match="unknown template kind"):
        _template(db, tenant, kind="carrier-pigeon")


def test_create_template_rejects_a_bad_slug(db, tenant) -> None:
    with pytest.raises(BadRequestError, match="slug"):
        _template(db, tenant, slug="Welcome Email")


def test_create_template_rejects_a_duplicate_slug_within_a_kind(db, tenant) -> None:
    _template(db, tenant)
    with pytest.raises(ConflictError):
        _template(db, tenant)


def test_the_same_slug_is_allowed_across_kinds(db, tenant) -> None:
    """`(kind, slug)` is the identity, not `slug` alone."""
    _template(db, tenant, kind="notification", slug="invoice")
    document = _template(db, tenant, kind="document", slug="invoice", channel=None)
    assert document.kind == "document"


def test_get_by_slug_is_the_caller_facing_lookup(db, tenant) -> None:
    created = _template(db, tenant)
    assert service.get_by_slug(db, tenant.id, "notification", "welcome-email").id == (
        created.id
    )


def test_get_template_raises_for_an_unknown_id(db, tenant) -> None:
    with pytest.raises(NotFoundError):
        service.get_template(db, tenant.id, uuid4())


# ── Versions ────────────────────────────────────────────────────────────────


def test_version_numbers_are_allocated_monotonically(db, tenant) -> None:
    template = _template(db, tenant)
    first = service.create_version(db, tenant.id, template.id, body="one")
    second = service.create_version(db, tenant.id, template.id, body="two")
    assert (first.version, second.version) == (1, 2)


def test_a_new_version_stores_its_derived_variables(db, tenant) -> None:
    template = _template(db, tenant)
    version = service.create_version(
        db, tenant.id, template.id, body="Hi {{ name }}", subject="Hello {{ name }}"
    )
    assert version.variables == ["name"]


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
    edited = service.update_version(db, tenant.id, template.id, 1, body="Hi {{ who }}")
    assert edited.body == "Hi {{ who }}"
    assert edited.variables == ["who"]


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
    service.create_version(db, tenant.id, template.id, body="v1 {{ name }}")
    service.create_version(db, tenant.id, template.id, body="v2 {{ name }}")
    service.publish_version(db, tenant.id, template.id, 1)
    subject, body = service.render_published(
        db, tenant.id, "notification", "welcome-email", {"name": "Ada"}
    )
    assert body == "v1 Ada"
    assert subject is None


def test_render_published_refuses_a_template_with_only_drafts(db, tenant) -> None:
    template = _template(db, tenant)
    service.create_version(db, tenant.id, template.id, body="draft")
    with pytest.raises(ConflictError, match="no published version"):
        service.render_published(db, tenant.id, "notification", "welcome-email", {})


def test_render_published_refuses_an_inactive_template(db, tenant) -> None:
    template = _template(db, tenant)
    service.create_version(db, tenant.id, template.id, body="hello")
    service.publish_version(db, tenant.id, template.id, 1)
    service.update_template(db, tenant.id, template.id, is_active=False)
    with pytest.raises(ConflictError, match="not active"):
        service.render_published(db, tenant.id, "notification", "welcome-email", {})
