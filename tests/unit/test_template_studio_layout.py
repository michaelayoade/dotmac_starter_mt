# ruff: noqa: F401, F811
"""Optional email layout and brand resolver (S4, TS-5).

Service, renderer, deploy-check and editor-surface behaviour on SQLite. Tenancy
for the new column is proven on Postgres in `tests/test_template_studio_isolation.py`
and `tests/test_template_studio_layout_migration.py`. Brand values use the
documentation domain `example.test`; nothing here names a product.
"""

from __future__ import annotations

import pytest
from dotmac_kernel.exceptions import BadRequestError, ConflictError
from dotmac_template_studio import deploycheck, layout, service
from dotmac_template_studio.contexts import (
    RenderContext,
    _reset_for_tests,
    register_contexts,
)
from dotmac_template_studio.layout import (
    BrandSpec,
    LayoutRefusedError,
    configure_brand,
    fragment_problem,
    wrap_part,
)
from dotmac_template_studio.models import TemplateVersion
from dotmac_template_studio.rendering import render_part
from dotmac_template_studio.schemas import VersionRead

from .test_template_studio_editor import (
    _audit_actions,
    _build_client,
    _login,
    _party,
    _same_session,
    _url,
    allow_all,
    author,
    client,
    cookies,
)

CTX = RenderContext(
    name="lay",
    variables=("customer_name",),
    required=("portal_url",),
    url=("portal_url",),
    sample={"customer_name": "Sample", "portal_url": "https://portal.example.test/x"},
)
TEXT = "Hi {customer_name}, open {portal_url}"
HTML = '<p>Hi {customer_name}</p><a href="{portal_url}">Open</a>'
VALUES = {
    "customer_name": "Ada <b>&\"'",
    "portal_url": "https://portal.example.test/x?a=1&b=2",
}

LAYOUT_HTML = (
    '<table style="width:100%"><tbody>'
    '<tr><td style="color:#0B4F31">{brand_name}</td></tr>'
    "<tr><td>{content}</td></tr>"
    '<tr><td><img src="{logo_url}"></td></tr></tbody></table>'
)
LAYOUT_TEXT = "== {brand_name} ==\n{content}\n-- {footer}"


def _resolver(db, tenant_id):
    return {
        "brand_name": "Acme <Co>",
        "logo_url": "https://cdn.example.test/logo.png",
        "footer": "Example Street",
    }


@pytest.fixture(autouse=True)
def _registry():
    previous = _reset_for_tests([CTX])
    configure_brand(
        BrandSpec(
            variables=("brand_name", "logo_url", "footer"),
            url=("logo_url",),
            resolver=_resolver,
        )
    )
    yield
    configure_brand(None)
    _reset_for_tests(previous.values())


@pytest.fixture
def template(db, tenant_row):
    return service.create_template(
        db, tenant_row.id, slug="welcome", channel="email", context="lay", name="W"
    )


def _make_layout(
    db, tenant, *, html=LAYOUT_HTML, text=LAYOUT_TEXT, slug="brand", publish=True
):
    t = service.create_template(
        db,
        tenant.id,
        slug=slug,
        channel=layout.LAYOUT_CHANNEL,
        context=layout.LAYOUT_CONTEXT_NAME,
        name="Brand layout",
    )
    v = service.create_version(db, tenant.id, t.id, body=text, html_body=html)
    if publish:
        service.publish_version(db, tenant.id, t.id, v.version)
    return t, v


def _message(db, tenant, template, *, layout_slug=None, publish=True, **kw):
    kw.setdefault("body", TEXT)
    kw.setdefault("html_body", HTML)
    v = service.create_version(
        db, tenant.id, template.id, subject="S", layout_slug=layout_slug, **kw
    )
    if publish:
        service.publish_version(db, tenant.id, template.id, v.version)
    return v


# ── Golden: no layout means no change ───────────────────────────────────────


def test_no_layout_is_byte_identical_to_plain_render(db, tenant_row, template):
    _message(db, tenant_row, template)
    parts = service.render_published_parts(
        db, tenant_row.id, "welcome", "email", VALUES
    )
    assert parts.text == render_part(TEXT, VALUES, "text", context=CTX)
    assert parts.html == render_part(HTML, VALUES, "html", context=CTX)
    assert parts.html == (
        "<p>Hi Ada &lt;b&gt;&amp;&quot;&#x27;</p>"
        '<a href="https://portal.example.test/x?a=1&amp;b=2">Open</a>'
    )


def test_no_layout_identical_with_and_without_brand_resolver(db, tenant_row, template):
    _message(db, tenant_row, template)
    with_brand = service.render_published_parts(
        db, tenant_row.id, "welcome", "email", VALUES
    )
    configure_brand(None)
    without = service.render_published_parts(
        db, tenant_row.id, "welcome", "email", VALUES
    )
    assert with_brand == without


# ── Golden: with a layout ───────────────────────────────────────────────────


def test_layout_golden_output(db, tenant_row, template):
    _make_layout(db, tenant_row)
    _message(db, tenant_row, template, layout_slug="brand")
    parts = service.render_published_parts(
        db, tenant_row.id, "welcome", "email", VALUES
    )
    content_html = render_part(HTML, VALUES, "html", context=CTX)
    assert parts.html == (
        '<table style="width:100%"><tbody>'
        '<tr><td style="color:#0B4F31">Acme &lt;Co&gt;</td></tr>'
        f"<tr><td>{content_html}</td></tr>"
        '<tr><td><img src="https://cdn.example.test/logo.png"></td></tr></tbody></table>'
    )
    # the content is inserted exactly once and NOT escaped a second time
    assert parts.html.count(content_html) == 1 and "&amp;amp;" not in parts.html
    assert parts.text == (
        "== Acme <Co> ==\n"
        + render_part(TEXT, VALUES, "text", context=CTX)
        + "\n-- Example Street"
    )
    assert parts.subject == "S"


def test_text_only_message_with_layout_wraps_text_only(db, tenant_row, template):
    _make_layout(db, tenant_row)
    _message(db, tenant_row, template, layout_slug="brand", html_body=None)
    parts = service.render_published_parts(
        db, tenant_row.id, "welcome", "email", VALUES
    )
    assert parts.html is None and parts.text.startswith("== Acme <Co> ==")


# ── Adversarial: slot ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad",
    [
        "<p>x</p></td></tr></table><p>escaped",  # closes the layout's structure
        "<div><p>x</p>",  # leaves an element open into the layout
        "<p>x</p></div>",
        "<html><body><p>x</p></body></html>",
        "<p>x</p><!-- c -->",
        "<b><i>x</b></i>",
    ],
)
def test_unbalanced_or_wrapped_content_is_refused(bad):
    assert fragment_problem(bad) is not None
    with pytest.raises(LayoutRefusedError):
        wrap_part(
            "<table><tr><td>{content}</td></tr></table>",
            "html",
            bad,
            {},
            layout.layout_context(),
        )


def test_balanced_fragment_with_void_elements_is_accepted():
    assert (
        fragment_problem('<p>a<br>b<img src="https://x.example.test/a.png"></p><hr>')
        is None
    )
    assert fragment_problem("<p>a<br/>b</p>") is None


def test_stored_unbalanced_content_is_refused_at_render(db, tenant_row, template):
    """A legacy/tampered row (bypassing the save-time gates) cannot break out."""
    _make_layout(db, tenant_row)
    v = _message(db, tenant_row, template, layout_slug="brand")
    v.html_body = "<p>x</p></td></tr></table><p>escaped"
    db.flush()
    with pytest.raises(BadRequestError):
        service.render_published_parts(db, tenant_row.id, "welcome", "email", VALUES)


def test_content_with_slot_token_look_alikes_is_data_not_slot(db, tenant_row, template):
    _make_layout(db, tenant_row)
    _message(db, tenant_row, template, layout_slug="brand")
    hostile = dict(VALUES, customer_name="{content} {brand_name} {{content}} TSSLOT00")
    parts = service.render_published_parts(
        db, tenant_row.id, "welcome", "email", hostile
    )
    assert parts.html.count("&#x27;") == 0
    assert "{content} {brand_name}" in parts.html  # literal text, not re-substituted
    assert parts.html.count("Acme &lt;Co&gt;") == 1
    assert parts.text.count("Acme <Co>") == 1


def test_brand_value_cannot_inject_markup_or_forge_the_slot(db, tenant_row, template):
    configure_brand(
        BrandSpec(
            variables=("brand_name", "logo_url", "footer"),
            url=("logo_url",),
            resolver=lambda d, t: {
                "brand_name": "<script>alert(1)</script>TSSLOT",
                "logo_url": "https://cdn.example.test/l.png",
                "footer": "f",
            },
        )
    )
    _make_layout(db, tenant_row)
    _message(db, tenant_row, template, layout_slug="brand")
    parts = service.render_published_parts(
        db, tenant_row.id, "welcome", "email", VALUES
    )
    assert "<script>" not in parts.html
    assert "&lt;script&gt;" in parts.html


@pytest.mark.parametrize(
    "bad_logo",
    [
        "javascript:alert(1)",
        "data:text/html,x",
        "//evil.example.test/x",
        "https://a.example.test/ x",
        "/relative.png",
    ],
)
def test_brand_url_is_scheme_checked(db, tenant_row, template, bad_logo):
    _make_layout(db, tenant_row)
    _message(db, tenant_row, template, layout_slug="brand")
    configure_brand(
        BrandSpec(
            variables=("brand_name", "logo_url", "footer"),
            url=("logo_url",),
            resolver=lambda d, t: {
                "brand_name": "A",
                "logo_url": bad_logo,
                "footer": "f",
            },
        )
    )
    with pytest.raises(BadRequestError):
        service.render_published_parts(db, tenant_row.id, "welcome", "email", VALUES)


@pytest.mark.parametrize(
    "bad",
    [
        {"brand_name": "A", "footer": "f"},  # missing required
        {
            "brand_name": "A",
            "logo_url": "https://c.example.test/l",
            "footer": "f",
            "extra": "x",
        },
        {"brand_name": 3, "logo_url": "https://c.example.test/l", "footer": "f"},
    ],
)
def test_resolver_contract_violations_refuse(db, tenant_row, template, bad):
    configure_brand(
        BrandSpec(
            variables=("brand_name", "logo_url", "footer"),
            url=("logo_url",),
            resolver=lambda d, t: bad,
        )
    )
    with pytest.raises(LayoutRefusedError):
        layout.resolve_brand(db, tenant_row.id)


def test_resolver_exception_is_generic(db, tenant_row):
    def boom(d, t):
        raise RuntimeError("secret-detail")

    configure_brand(BrandSpec(variables=("brand_name",), resolver=boom))
    with pytest.raises(LayoutRefusedError) as err:
        layout.resolve_brand(db, tenant_row.id)
    assert "secret-detail" not in str(err.value)


def test_optional_brand_variable_may_be_absent():
    configure_brand(
        BrandSpec(
            variables=("brand_name", "tagline"),
            optional=("tagline",),
            resolver=lambda d, t: {"brand_name": "A"},
        )
    )
    assert layout.resolve_brand(None, None) == {"brand_name": "A"}
    assert "tagline" in layout.layout_context().optional


def test_brand_spec_rejects_slot_name_and_stray_names():
    with pytest.raises(ValueError):
        BrandSpec(variables=("content",), resolver=lambda d, t: {})
    with pytest.raises(ValueError):
        BrandSpec(variables=("a",), url=("b",), resolver=lambda d, t: {})
    with pytest.raises(ValueError):
        BrandSpec(variables=("Bad-Name",), resolver=lambda d, t: {})


# ── Save-time validation of the layout itself ───────────────────────────────


def _new_layout_template(db, tenant):
    return service.create_template(
        db,
        tenant.id,
        slug="l2",
        channel=layout.LAYOUT_CHANNEL,
        context=layout.LAYOUT_CONTEXT_NAME,
        name="L",
    )


def test_layout_needs_exactly_one_slot(db, tenant_row):
    t = _new_layout_template(db, tenant_row)
    for html in ("<p>none</p>", "<p>{content}</p><p>{content}</p>"):
        with pytest.raises(BadRequestError):
            service.create_version(
                db, tenant_row.id, t.id, body="{content}", html_body=html
            )
    with pytest.raises(BadRequestError):
        service.create_version(db, tenant_row.id, t.id, body="no slot")
    with pytest.raises(BadRequestError):
        service.create_version(db, tenant_row.id, t.id, body="{content} {content}")


def test_slot_may_not_be_inside_a_section_or_an_attribute(db, tenant_row):
    configure_brand(
        BrandSpec(
            variables=("brand_name",),
            optional=("brand_name",),
            resolver=lambda d, t: {},
        )
    )
    t = _new_layout_template(db, tenant_row)
    with pytest.raises(BadRequestError):
        service.create_version(
            db,
            tenant_row.id,
            t.id,
            body="{content}",
            html_body="<p>{#brand_name}{content}{/brand_name}</p>",
        )
    with pytest.raises(BadRequestError):
        service.create_version(
            db,
            tenant_row.id,
            t.id,
            body="{content}",
            html_body='<a href="{content}">x</a>',
        )
    with pytest.raises(BadRequestError):
        service.create_version(
            db,
            tenant_row.id,
            t.id,
            body="{content}",
            html_body='<p title="{content}">x</p>',
        )


def test_layout_html_goes_through_the_sanitizer_and_scanner(db, tenant_row):
    t = _new_layout_template(db, tenant_row)
    for html in (
        "<script>x</script>{content}",
        '<p onclick="x()">{content}</p>',
        '<img src="{content}">',
        "<style>p{}</style>{content}",
    ):
        with pytest.raises(BadRequestError):
            service.create_version(
                db, tenant_row.id, t.id, body="{content}", html_body=html
            )


def test_no_resolver_means_brand_variables_fail_validation(db, tenant_row):
    configure_brand(None)
    t = _new_layout_template(db, tenant_row)
    with pytest.raises(BadRequestError) as err:
        service.create_version(
            db,
            tenant_row.id,
            t.id,
            body="{content}",
            html_body="<p>{brand_name}</p>{content}",
        )
    assert "brand_name" in str(err.value)
    # a slot-only layout is still fine without a resolver
    service.create_version(
        db, tenant_row.id, t.id, body="{content}", html_body="<div>{content}</div>"
    )


def test_layout_context_is_reserved_and_tied_to_its_channel(db, tenant_row):
    with pytest.raises(ValueError):
        register_contexts(RenderContext(name="email_layout", variables=("x",)))
    with pytest.raises(BadRequestError):
        service.create_template(
            db,
            tenant_row.id,
            slug="x1",
            channel="email",
            context="email_layout",
            name="n",
        )
    with pytest.raises(BadRequestError):
        service.create_template(
            db,
            tenant_row.id,
            slug="x2",
            channel="email_layout",
            context="lay",
            name="n",
        )


def test_layout_cannot_be_rendered_as_a_message(db, tenant_row):
    _make_layout(db, tenant_row)
    with pytest.raises(ConflictError):
        service.render_published_parts(db, tenant_row.id, "brand", "email_layout", {})


# ── Opt-in rules ────────────────────────────────────────────────────────────


def test_opt_in_requires_existing_active_layout_and_email_channel(
    db, tenant_row, template
):
    with pytest.raises(BadRequestError):
        service.create_version(
            db, tenant_row.id, template.id, body=TEXT, layout_slug="nope"
        )
    t, _ = _make_layout(db, tenant_row)
    service.update_template(db, tenant_row.id, t.id, is_active=False)
    with pytest.raises(BadRequestError):
        service.create_version(
            db, tenant_row.id, template.id, body=TEXT, layout_slug="brand"
        )
    sms = service.create_template(
        db, tenant_row.id, slug="smsy", channel="sms", context="lay", name="s"
    )
    service.update_template(db, tenant_row.id, t.id, is_active=True)
    with pytest.raises(BadRequestError):
        service.create_version(
            db, tenant_row.id, sms.id, body=TEXT, layout_slug="brand"
        )
    # a layout cannot itself opt in
    with pytest.raises(BadRequestError):
        service.create_version(
            db,
            tenant_row.id,
            t.id,
            body="{content}",
            html_body="<p>{content}</p>",
            layout_slug="brand",
        )


def test_default_is_no_layout_and_it_can_be_removed(db, tenant_row, template):
    _make_layout(db, tenant_row)
    v = service.create_version(db, tenant_row.id, template.id, body=TEXT)
    assert v.layout_slug is None
    service.update_version(
        db, tenant_row.id, template.id, v.version, layout_slug="brand"
    )
    assert v.layout_slug == "brand"
    service.update_version(db, tenant_row.id, template.id, v.version, body=TEXT + "!")
    assert v.layout_slug == "brand"  # omitted keeps
    service.update_version(db, tenant_row.id, template.id, v.version, layout_slug=None)
    assert v.layout_slug is None


def test_render_fails_closed_when_the_layout_is_gone_or_unpublished(
    db, tenant_row, template
):
    t, _ = _make_layout(db, tenant_row)
    _message(db, tenant_row, template, layout_slug="brand")
    t.is_active = False  # legacy state: the service now refuses to create it
    db.flush()
    with pytest.raises(BadRequestError):
        service.render_published_parts(db, tenant_row.id, "welcome", "email", VALUES)
    t.is_active = True
    db.flush()
    t.published_version = None
    db.flush()
    with pytest.raises(BadRequestError):
        service.render_published_parts(db, tenant_row.id, "welcome", "email", VALUES)


def test_message_with_html_needs_a_layout_with_html(db, tenant_row, template):
    _make_layout(db, tenant_row, html=None)
    with pytest.raises(BadRequestError) as err:
        _message(db, tenant_row, template, layout_slug="brand")  # publish dry-run
    assert "no html part" in str(err.value)


# ── Publish-time re-validation ──────────────────────────────────────────────


def test_publish_without_published_layout_is_refused(db, tenant_row, template):
    _make_layout(db, tenant_row, publish=False)
    with pytest.raises(BadRequestError):
        _message(db, tenant_row, template, layout_slug="brand")


def test_layout_publish_is_refused_if_it_would_break_a_message(
    db, tenant_row, template
):
    t, _ = _make_layout(db, tenant_row)
    _message(db, tenant_row, template, layout_slug="brand")
    v2 = service.create_version(db, tenant_row.id, t.id, body="{content}")  # text only
    with pytest.raises(BadRequestError) as err:
        service.publish_version(db, tenant_row.id, t.id, v2.version)
    assert "welcome/email" in str(err.value)
    assert t.published_version == 1


def test_layout_republish_changes_the_output_for_users(db, tenant_row, template):
    t, _ = _make_layout(db, tenant_row)
    _message(db, tenant_row, template, layout_slug="brand")
    v2 = service.create_version(
        db,
        tenant_row.id,
        t.id,
        body="[{brand_name}] {content}",
        html_body="<div>{content}</div>",
    )
    service.publish_version(db, tenant_row.id, t.id, v2.version)
    parts = service.render_published_parts(
        db, tenant_row.id, "welcome", "email", VALUES
    )
    assert parts.text.startswith("[Acme <Co>] Hi")
    assert parts.html.startswith("<div><p>Hi")


# ── Preview ─────────────────────────────────────────────────────────────────


def test_layout_preview_wraps_a_sample_message(db, tenant_row):
    t, v = _make_layout(db, tenant_row, publish=False)
    out = service.render_preview(db, tenant_row.id, t.id, v.version)
    assert "Sample message content." in out.text and "Acme <Co>" in out.text
    assert (
        "<p>Sample message content.</p>" in out.html and "Acme &lt;Co&gt;" in out.html
    )


def test_message_preview_includes_published_layout(db, tenant_row, template):
    _make_layout(db, tenant_row)
    v = _message(db, tenant_row, template, layout_slug="brand", publish=False)
    out = service.render_preview(db, tenant_row.id, template.id, v.version)
    assert "Acme &lt;Co&gt;" in out.html and "Sample" in out.html


# ── Deploy check ────────────────────────────────────────────────────────────


def _check(db, tenant):
    return deploycheck.check_published_revisions(
        list_tenant_ids=lambda: [tenant.id], tenant_session=lambda _t: _same_session(db)
    )


def test_deploy_check_covers_layouts_and_opt_ins(db, tenant_row, template):
    _make_layout(db, tenant_row)
    _message(db, tenant_row, template, layout_slug="brand")
    report = _check(db, tenant_row)
    assert report.ok and report.revisions_checked == 2


def test_deploy_check_fails_when_brand_vocabulary_is_gone(db, tenant_row, template):
    _make_layout(db, tenant_row)
    _message(db, tenant_row, template, layout_slug="brand")
    configure_brand(None)  # the deploy no longer registers the brand spec
    report = _check(db, tenant_row)
    assert not report.ok
    names = {f.slug for f in report.failures}
    assert "brand" in names and "welcome" in names


def test_deploy_check_fails_when_resolver_breaks(db, tenant_row, template):
    _make_layout(db, tenant_row)
    _message(db, tenant_row, template, layout_slug="brand")
    configure_brand(
        BrandSpec(
            variables=("brand_name", "logo_url", "footer"),
            url=("logo_url",),
            resolver=lambda d, t: {},
        )
    )
    report = _check(db, tenant_row)
    assert not report.ok


# ── Schema surface ──────────────────────────────────────────────────────────


def test_version_read_exposes_layout_slug(db, tenant_row, template):
    _make_layout(db, tenant_row)
    v = _message(db, tenant_row, template, layout_slug="brand")
    assert VersionRead.model_validate(v).layout_slug == "brand"
    assert isinstance(v, TemplateVersion)


# ── Editor (web) ────────────────────────────────────────────────────────────


def test_editor_create_layout_opt_in_preview_and_publish(
    client, cookies, db, tenant_row, template
):
    created = client.post(
        "/admin/templates",
        cookies=cookies,
        data={
            "slug": "brand",
            "channel": "email_layout",
            "context": "email_layout",
            "name": "Brand",
        },
    )
    assert created.status_code == 200
    lay = service.get_by_slug(db, tenant_row.id, "brand", "email_layout")
    v = service.create_version(
        db, tenant_row.id, lay.id, body=LAYOUT_TEXT, html_body=LAYOUT_HTML
    )
    page = client.get(_url(lay, "versions", v.version), cookies=cookies)
    assert page.status_code == 200
    assert "<iframe" in page.text and "Acme" in page.text
    assert "Sample message content." in page.text
    # preview html is only ever inside the sandboxed srcdoc attribute
    assert "<table" not in page.text.split("<iframe")[0].split("Preview")[-1]
    bad = client.post(
        _url(lay, "versions", v.version, "edit"),
        cookies=cookies,
        data={"body": "no slot", "html_body": LAYOUT_HTML},
    )
    assert "exactly one" in bad.text
    conf = client.post(
        _url(lay, "versions", v.version, "publish"), cookies=cookies, data={}
    )
    assert "Confirm to continue" in conf.text
    ok = client.post(
        _url(lay, "versions", v.version, "publish"),
        cookies=cookies,
        data={"confirm": "yes"},
    )
    assert "is now published" in ok.text
    msg = service.create_version(
        db, tenant_row.id, template.id, body=TEXT, html_body=HTML
    )
    page = client.get(_url(template, "versions", msg.version), cookies=cookies)
    assert 'name="layout_slug"' in page.text and "Brand (brand)" in page.text
    saved = client.post(
        _url(template, "versions", msg.version, "edit"),
        cookies=cookies,
        data={"subject": "S", "body": TEXT, "html_body": HTML, "layout_slug": "brand"},
    )
    assert "Draft saved." in saved.text
    db.refresh(msg)
    assert msg.layout_slug == "brand"
    missing = client.post(
        _url(template, "versions", msg.version, "edit"),
        cookies=cookies,
        data={"subject": "S", "body": TEXT, "html_body": HTML, "layout_slug": "ghost"},
    )
    assert "does not exist" in missing.text


# ── Review fixes (S4 round 2) ───────────────────────────────────────────────


def test_layout_in_use_cannot_be_deactivated_or_deleted(db, tenant_row, template):
    t, _ = _make_layout(db, tenant_row)
    draft = service.create_version(
        db, tenant_row.id, template.id, body=TEXT, layout_slug="brand"
    )  # a DRAFT reference is enough
    for call in (
        lambda: service.update_template(db, tenant_row.id, t.id, is_active=False),
        lambda: service.delete_template(db, tenant_row.id, t.id),
    ):
        with pytest.raises(ConflictError) as err:
            call()
        assert "welcome/email" in str(err.value)
    assert t.is_active
    # once the reference is removed both are allowed
    service.update_version(
        db, tenant_row.id, template.id, draft.version, layout_slug=None
    )
    service.update_template(db, tenant_row.id, t.id, is_active=False)
    service.delete_template(db, tenant_row.id, t.id)


def test_layout_in_use_api_paths_are_409(db, tenant_row, template, author):
    from dotmac_template_studio import router as api
    from dotmac_template_studio.schemas import TemplateUpdate

    t, _ = _make_layout(db, tenant_row)
    _message(db, tenant_row, template, layout_slug="brand")
    with pytest.raises(ConflictError):
        api.update_template(
            t.id, TemplateUpdate(is_active=False), db, tenant_row, author
        )
    with pytest.raises(ConflictError):
        api.delete_template(t.id, db, tenant_row, author)
    assert service.get_template(db, tenant_row.id, t.id).is_active


def test_layout_in_use_web_deactivate_is_refused_in_the_page(
    client, cookies, db, tenant_row, template
):
    t, _ = _make_layout(db, tenant_row)
    _message(db, tenant_row, template, layout_slug="brand")
    resp = client.post(_url(t, "status"), cookies=cookies, data={"active": "0"})
    assert resp.status_code == 200 and "welcome/email" in resp.text
    db.refresh(t)
    assert t.is_active


# Each case escapes (or would escape) a layout element under HTML5 parsing even
# though a strict-nesting check passes it.
_ESCAPES = [
    ("<td>x</td>", "td"),
    ("<tr><td>x</td></tr>", "tr"),
    ("<li>x</li>", "li"),
    ("<p><div>x</div></p>", "div in p"),
    ("<p><ul><li>x</li></ul></p>", "ul in p"),
    (
        '<a href="https://a.example.test/">x<a href="https://b.example.test/">y</a></a>',
        "a in a",
    ),
    ("<thead><tr><th>x</th></tr></thead>", "thead"),
]


@pytest.mark.parametrize(("bad", "why"), _ESCAPES)
def test_html5_implicit_close_escapes_are_refused(bad, why):
    assert fragment_problem(bad) is not None, why


def test_content_owned_table_and_list_are_allowed():
    ok = "<table><tbody><tr><td>x</td></tr></tbody></table><ul><li>a</li></ul>"
    assert fragment_problem(ok) is None


def _html5(html: str) -> str:
    import nh3

    return nh3.clean(
        html,
        tags={"table", "tbody", "tr", "td", "th", "p", "div", "ul", "li", "a", "b"},
        attributes={},
    )


def test_layout_structure_survives_html5_parsing_for_allowed_content():
    layout_html = (
        "<table><tbody><tr><td>{content}</td></tr>"
        "<tr><td>FOOTER</td></tr></tbody></table>"
    )
    content = "<table><tbody><tr><td>x</td></tr></tbody></table><ul><li>a</li></ul>"
    out = wrap_part(layout_html, "html", content, {}, layout.layout_context())
    cleaned = _html5(out)
    assert cleaned.endswith("<tr><td>FOOTER</td></tr></tbody></table>")
    assert cleaned.count("FOOTER") == 1


@pytest.mark.parametrize(("bad", "why"), _ESCAPES)
def test_escape_content_is_refused_at_wrap_time(bad, why):
    with pytest.raises(LayoutRefusedError):
        wrap_part(
            "<table><tbody><tr><td>{content}</td></tr><tr><td>FOOTER</td></tr>"
            "</tbody></table>",
            "html",
            bad,
            {},
            layout.layout_context(),
        )


def test_the_td_escape_is_real_under_html5():
    """Why the rule exists: a content <td> splits the layout cell in html5."""
    out = (
        "<table><tbody><tr><td>"
        + "<td>x</td>"
        + "</td><td>FOOTER</td></tr></tbody></table>"
    )
    assert "<td>x</td></td>" not in _html5(out)


@pytest.mark.parametrize("parent", ["p", "li", "a", "span", "b"])
def test_slot_parent_must_be_a_safe_container(db, tenant_row, parent):
    t = _new_layout_template(db, tenant_row)
    html = f"<{parent}>{{content}}</{parent}>"
    with pytest.raises(BadRequestError) as err:
        service.create_version(
            db, tenant_row.id, t.id, body="{content}", html_body=html
        )
    assert "may only sit" in str(err.value) or "html" in str(err.value)


@pytest.mark.parametrize("parent", ["div", "blockquote"])
def test_slot_parent_allowed_containers(db, tenant_row, parent):
    t = _new_layout_template(db, tenant_row)
    service.create_version(
        db,
        tenant_row.id,
        t.id,
        body="{content}",
        html_body=f"<{parent}>{{content}}</{parent}>",
    )
    assert layout.slot_parent_problems("html", "{content}") == []
    assert (
        layout.slot_parent_problems(
            "html", "<table><tbody><tr><td>{content}</td></tr></tbody></table>"
        )
        == []
    )


def test_render_records_the_layout_revision(db, tenant_row, template):
    t, v = _make_layout(db, tenant_row)
    _message(db, tenant_row, template, layout_slug="brand")
    parts = service.render_published_parts(
        db, tenant_row.id, "welcome", "email", VALUES
    )
    assert (parts.layout_slug, parts.layout_version) == ("brand", v.version)
    assert parts.layout_revision_id == v.id
    sample = service.render_preview(db, tenant_row.id, template.id, 1)
    assert sample.layout_version == v.version


def test_no_layout_render_records_none(db, tenant_row, template):
    _message(db, tenant_row, template)
    parts = service.render_published_parts(
        db, tenant_row.id, "welcome", "email", VALUES
    )
    assert (parts.layout_slug, parts.layout_version, parts.layout_revision_id) == (
        None,
        None,
        None,
    )


def test_layout_publish_audit_lists_dependents(db, tenant_row, template, author):
    from dotmac_template_studio import router as api

    t, _ = _make_layout(db, tenant_row)
    _message(db, tenant_row, template, layout_slug="brand")
    v2 = service.create_version(
        db, tenant_row.id, t.id, body="{content}!", html_body="<div>{content}</div>"
    )
    api.publish_version(t.id, v2.version, db, tenant_row, author)
    from dotmac_kernel.audit import AuditEvent
    from sqlalchemy import select

    event = (
        db.execute(
            select(AuditEvent).where(
                AuditEvent.action == "template_studio.version.publish"
            )
        )
        .scalars()
        .all()[-1]
    )
    assert event.details["layout_dependents"] == ["welcome/email"]
    assert service.layout_audit_users(db, tenant_row.id, template.id) == []


def test_error_messages_do_not_echo_values_or_exceptions(db, tenant_row):
    configure_brand(
        BrandSpec(
            variables=("brand_name", "logo_url"),
            url=("logo_url",),
            resolver=lambda d, t: {
                "brand_name": "A",
                "logo_url": "ftp-SECRETSCHEME://x.example.test/",
            },
        )
    )
    with pytest.raises(LayoutRefusedError) as err:
        layout.resolve_brand(db, tenant_row.id)
    assert "SECRETSCHEME" not in str(err.value).upper() and "logo_url" in str(err.value)

    def boom(d, t):
        raise LayoutRefusedError("leaky-detail")

    configure_brand(BrandSpec(variables=("brand_name",), resolver=boom))
    with pytest.raises(LayoutRefusedError) as err2:
        layout.resolve_brand(db, tenant_row.id)
    assert "leaky" not in str(err2.value) and "RuntimeError" not in str(err2.value)


def test_fragment_check_runs_at_save_time_with_a_layout(db, tenant_row, template):
    _make_layout(db, tenant_row)
    # the sanitizer accepts this; the fragment rule refuses it for layout use
    with pytest.raises(BadRequestError) as err:
        service.create_version(
            db,
            tenant_row.id,
            template.id,
            body=TEXT,
            html_body=(
                "<p>a</p><div>{customer_name} {portal_url}</div>" "<p><div>x</div></p>"
            ),
            layout_slug="brand",
        )
    assert "cannot be used with a layout" in str(err.value) or "html" in str(err.value)
    # the same html without a layout still saves where the sanitizer allows it
    v = service.create_version(
        db, tenant_row.id, template.id, body=TEXT, html_body=HTML
    )
    with pytest.raises(BadRequestError):
        service.update_version(
            db,
            tenant_row.id,
            template.id,
            v.version,
            html_body=(
                "<table><tbody><tr><td>{customer_name} {portal_url}</td></tr>"
                "</tbody></table><td>x</td>"
            ),
            layout_slug="brand",
        )
