"""The optional html part end to end on SQLite: service, API, seeding, compose.

Tenancy for the new column is proven on Postgres in
`tests/test_template_studio_isolation.py`; nothing here is evidence of isolation.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from uuid import uuid4

import pytest
from dotmac_kernel.exceptions import BadRequestError, ConflictError
from dotmac_kernel.models import Tenant
from dotmac_template_studio import TemplateSeed, router, seed_templates, service
from dotmac_template_studio.contexts import RenderContext, _reset_for_tests
from dotmac_template_studio.schemas import (
    RenderResult,
    VersionCreate,
    VersionRead,
    VersionUpdate,
)

CTX = RenderContext(
    name="hp",
    variables=("customer_name",),
    required=("portal_url",),
    url=("portal_url", "line_url"),
    lists={"lines": ("label", "line_url")},
)
HTML = '<p style="color:#333">Hi {customer_name}</p><a href="{portal_url}">Open</a>'
TEXT = "Hi {customer_name}, open {portal_url}"


@pytest.fixture(autouse=True)
def _registry():
    previous = _reset_for_tests([CTX])
    yield
    _reset_for_tests(previous.values())


@pytest.fixture
def tenant(db) -> Tenant:
    tenant = Tenant(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    db.add(tenant)
    db.flush()
    return tenant


@pytest.fixture
def template(db, tenant):
    return service.create_template(
        db, tenant.id, slug="welcome", channel="email", context="hp", name="Welcome"
    )


VALUES = {
    "customer_name": "Ada <b>",
    "portal_url": "https://portal.example.test/x?a=1&b=2",
}


# ── Service ─────────────────────────────────────────────────────────────────


def test_text_only_version_has_no_html_and_renders_as_before(db, tenant, template):
    v = service.create_version(db, tenant.id, template.id, body=TEXT, subject="S")
    assert v.html_body is None
    service.publish_version(db, tenant.id, template.id, v.version)
    subject, body = service.render_published(
        db, tenant.id, "welcome", "email", VALUES, strict=True
    )
    assert (subject, body) == (
        "S",
        "Hi Ada <b>, open https://portal.example.test/x?a=1&b=2",
    )
    parts = service.render_published_parts(db, tenant.id, "welcome", "email", VALUES)
    assert parts.html is None and parts.text == body


def test_html_version_stores_sanitized_html_and_renders_both_parts(
    db, tenant, template
):
    v = service.create_version(
        db, tenant.id, template.id, body=TEXT, html_body="<!DOCTYPE html>" + HTML
    )
    assert v.html_body is not None and v.html_body.startswith("<!DOCTYPE html>")
    assert 'style="color:#333"' in v.html_body
    assert v.variables == ["customer_name", "portal_url"]
    service.publish_version(db, tenant.id, template.id, v.version)
    parts = service.render_published_parts(db, tenant.id, "welcome", "email", VALUES)
    assert "Ada &lt;b&gt;" in (parts.html or "")
    assert 'href="https://portal.example.test/x?a=1&amp;b=2"' in (parts.html or "")
    assert parts.text.startswith("Hi Ada <b>,")  # text stays raw
    # The legacy entry point is unchanged: no html, same tuple.
    assert (
        service.render_published(db, tenant.id, "welcome", "email", VALUES)[1]
        == parts.text
    )


@pytest.mark.parametrize(
    "bad",
    [
        '<script>alert(1)</script><a href="{portal_url}">x</a>',
        '<a href="{portal_url}" onclick="x()">x</a>',
        '<p style="position:fixed">x</p><a href="{portal_url}">x</a>',
        '<a href="{portal_url}">x</a><!-- c -->',
    ],
)
def test_html_the_sanitizer_would_change_is_refused_and_nothing_is_stored(
    db, tenant, template, bad
):
    with pytest.raises(BadRequestError):
        service.create_version(db, tenant.id, template.id, body=TEXT, html_body=bad)
    assert service.list_versions(db, tenant.id, template.id) == []


def test_refusal_names_what_would_have_been_removed(db, tenant, template):
    with pytest.raises(BadRequestError) as caught:
        service.create_version(
            db,
            tenant.id,
            template.id,
            body=TEXT,
            html_body=(
                '<p onclick="x()">{customer_name}</p><a href="{portal_url}">x</a>'
            ),
        )
    assert "'onclick' removed from <p>" in str(caught.value)


def test_html_must_use_the_required_variables_too(db, tenant, template):
    with pytest.raises(BadRequestError, match="required variable"):
        service.create_version(
            db, tenant.id, template.id, body=TEXT, html_body="<p>{customer_name}</p>"
        )


def test_update_keeps_clears_and_replaces_html(db, tenant, template):
    v = service.create_version(db, tenant.id, template.id, body=TEXT, html_body=HTML)
    original = v.html_body
    service.update_version(db, tenant.id, template.id, v.version, subject="new")
    assert v.html_body == original  # omitted: untouched
    service.update_version(db, tenant.id, template.id, v.version, html_body=None)
    assert v.html_body is None  # explicit None removes
    service.update_version(db, tenant.id, template.id, v.version, html_body=HTML)
    assert v.html_body == original
    with pytest.raises(BadRequestError):
        service.update_version(
            db, tenant.id, template.id, v.version, html_body="<iframe></iframe>"
        )
    assert v.html_body == original  # a refused edit changes nothing
    service.publish_version(db, tenant.id, template.id, v.version)
    with pytest.raises(ConflictError):
        service.update_version(db, tenant.id, template.id, v.version, html_body=None)


def test_list_sections_in_table_rows_render(db, tenant, template):
    html = (
        '<a href="{portal_url}">x</a><table><tr><th>L</th></tr>'
        '{#lines}<tr><td><a href="{line_url}">{label}</a></td></tr>{/lines}</table>'
    )
    v = service.create_version(db, tenant.id, template.id, body=TEXT, html_body=html)
    service.publish_version(db, tenant.id, template.id, v.version)
    out = service.render_published_parts(
        db,
        tenant.id,
        "welcome",
        "email",
        {
            **VALUES,
            "lines": [
                {"label": "A", "line_url": "https://e.example/a"},
                {"label": "B", "line_url": "https://e.example/b"},
            ],
        },
    ).html
    assert out is not None and out.count("<tr><td>") == 2 and "{#" not in out


# ── API (route functions; the adapters are thin) ────────────────────────────


@pytest.fixture
def api(monkeypatch, db, tenant):
    monkeypatch.setattr(router, "write_audit_event", lambda *a, **k: None)
    return SimpleNamespace(db=db, tenant=tenant, actor=SimpleNamespace(id=uuid4()))


def _create(api, template, payload):
    return router.create_version(
        template.id, payload, db=api.db, tenant=api.tenant, actor=api.actor
    )


def test_api_round_trip_without_html_is_unchanged(api, template):
    payload = VersionCreate.model_validate({"body": TEXT, "subject": "S"})
    created = VersionRead.model_validate(_create(api, template, payload))
    assert created.html_body is None
    router.publish_version(
        template.id, created.version, db=api.db, tenant=api.tenant, actor=api.actor
    )
    result = router.render_template(
        "welcome",
        "email",
        SimpleNamespace(values=VALUES),
        db=api.db,
        tenant=api.tenant,
        _=None,
    )
    assert isinstance(result, RenderResult)
    assert result.html is None and result.body.startswith("Hi Ada")
    assert result.model_dump()["subject"] == "S"


def test_api_round_trip_with_html(api, template):
    payload = VersionCreate.model_validate({"body": TEXT, "html_body": HTML})
    created = VersionRead.model_validate(_create(api, template, payload))
    assert created.html_body and "{portal_url}" in created.html_body
    router.publish_version(
        template.id, created.version, db=api.db, tenant=api.tenant, actor=api.actor
    )
    result = router.render_template(
        "welcome",
        "email",
        SimpleNamespace(values=VALUES),
        db=api.db,
        tenant=api.tenant,
        _=None,
    )
    assert result.html and "Ada &lt;b&gt;" in result.html


def test_api_patch_distinguishes_omitted_from_null(api, template):
    created = _create(
        api, template, VersionCreate.model_validate({"body": TEXT, "html_body": HTML})
    )
    keep = VersionUpdate.model_validate({"subject": "x"})
    assert "html_body" not in keep.model_fields_set
    router.update_version(
        template.id,
        created.version,
        keep,
        db=api.db,
        tenant=api.tenant,
        actor=api.actor,
    )
    assert created.html_body is not None
    clear = VersionUpdate.model_validate({"html_body": None})
    assert "html_body" in clear.model_fields_set
    router.update_version(
        template.id,
        created.version,
        clear,
        db=api.db,
        tenant=api.tenant,
        actor=api.actor,
    )
    assert created.html_body is None


def test_api_refuses_hostile_html_with_a_bad_request(api, template):
    payload = VersionCreate.model_validate(
        {"body": TEXT, "html_body": '<a href="javascript:alert(1)">x</a>{portal_url}'}
    )
    with pytest.raises(BadRequestError):
        _create(api, template, payload)


def test_html_body_empty_string_is_a_validation_error() -> None:
    with pytest.raises(ValueError):
        VersionCreate.model_validate({"body": "x", "html_body": ""})


# ── Seeding ─────────────────────────────────────────────────────────────────

SEED = TemplateSeed(
    slug="seeded",
    channel="email",
    context="hp",
    name="Seeded",
    body=TEXT,
    html_body=HTML,
)


def test_seeding_with_html_is_idempotent_and_publishes_html(db, tenant):
    first = seed_templates(db, tenant.id, [SEED])
    assert first.created == ("seeded/email",)
    tpl = service.get_by_slug(db, tenant.id, "seeded", "email")
    published = service.get_published(db, tenant.id, tpl.id)
    assert published is not None and published.html_body
    again = seed_templates(db, tenant.id, [SEED])
    assert again.created == () and again.skipped == ("seeded/email",)
    assert len(service.list_versions(db, tenant.id, tpl.id)) == 1


def test_seeding_never_clobbers_an_operator_edit_or_adds_html(db, tenant):
    text_only = TemplateSeed(
        slug="seeded", channel="email", context="hp", name="Seeded", body=TEXT
    )
    seed_templates(db, tenant.id, [text_only])
    tpl = service.get_by_slug(db, tenant.id, "seeded", "email")
    edited = service.create_version(
        db, tenant.id, tpl.id, body="Operator wording {portal_url}"
    )
    service.publish_version(db, tenant.id, tpl.id, edited.version)
    outcome = seed_templates(db, tenant.id, [SEED])  # the seed now carries html
    assert outcome.skipped == ("seeded/email",)
    published = service.get_published(db, tenant.id, tpl.id)
    assert published is not None
    assert published.body == "Operator wording {portal_url}"
    assert published.html_body is None


def test_a_seed_with_unsafe_html_fails_the_deploy(db, tenant):
    bad = TemplateSeed(
        slug="bad",
        channel="email",
        context="hp",
        name="Bad",
        body=TEXT,
        html_body='<script>x</script><a href="{portal_url}">x</a>',
    )
    with pytest.raises(BadRequestError):
        seed_templates(db, tenant.id, [bad])


# ── compose_email ───────────────────────────────────────────────────────────


def _part(pid, order, body, html=None, subject=None):
    return service.RenderedEmailPart(
        part_id=pid, order=order, subject=subject, body=body, html=html
    )


def test_compose_composes_html_and_text_separately_in_order():
    a = _part("a", 20, "text a", "<p>a</p>", "Subj")
    b = _part("b", 10, "text b", "<p>b</p>")
    out = service.compose_email([a, b], primary_part_id="a")
    assert out.body == "text b\n\ntext a"
    assert out.html == "<p>b</p>\n\n<p>a</p>"
    assert out.subject == "Subj"


def test_compose_text_only_is_byte_identical_to_before():
    a = _part("a", 1, "Dear Ada,\nx\n", subject=" Subj ")
    b = _part("b", 2, "y")
    out = service.compose_email([a, b], primary_part_id="a")
    assert out.body == "Dear Ada,\nx\n\n\ny" and out.html is None
    # The positional shape of the dataclasses is unchanged for existing callers.
    legacy = service.RenderedEmailPart("c", 3, "s", "b")
    assert legacy.html is None
    assert list(inspect.signature(service.RenderedEmailPart).parameters)[:4] == [
        "part_id",
        "order",
        "subject",
        "body",
    ]


def test_compose_refuses_mixed_html_and_misplaced_doctype():
    with pytest.raises(ValueError, match="every part carries html or none"):
        service.compose_email(
            [_part("a", 1, "t", "<p>a</p>", "S"), _part("b", 2, "t")],
            primary_part_id="a",
        )
    with pytest.raises(ValueError, match="doctype"):
        service.compose_email(
            [
                _part("a", 1, "t", "<!DOCTYPE html><p>a</p>", "S"),
                _part("b", 2, "t", "<!DOCTYPE html><p>b</p>"),
            ],
            primary_part_id="a",
        )
    ok = service.compose_email(
        [
            _part("a", 1, "t", "<!DOCTYPE html><p>a</p>", "S"),
            _part("b", 2, "t", "<p>b</p>"),
        ],
        primary_part_id="a",
    )
    assert ok.html is not None and ok.html.startswith("<!DOCTYPE html>")
    with pytest.raises(ValueError, match="html must be"):
        _part("a", 1, "t", "  ")
