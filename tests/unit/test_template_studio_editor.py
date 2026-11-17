"""Template Studio editor (S3, TS-4/TS-6): service rules, web surface, deploy check.

The web tests mount the module's real staff router on a bare app (same pattern as
`test_admin_route_sweep.py`) with a real cookie session. Authorization is stubbed
at `dotmac_kernel.deps.authorize_party` (the role query is proven elsewhere); the
non-admitted path is covered by the route sweep, which enumerates new routes.
"""

from __future__ import annotations

import re
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from dotmac_kernel.capabilities import CapabilityCatalogue, install_capabilities
from dotmac_kernel.deps import get_db
from dotmac_kernel.errors import register_error_handlers
from dotmac_kernel.exceptions import BadRequestError, ConflictError
from dotmac_kernel.middleware.csrf import CSRFMiddleware
from dotmac_kernel.models import AuthSession, Party, PartyPerson, PartyType, Tenant
from dotmac_kernel.modules import ModuleManifest
from dotmac_kernel.permissions import (
    PermissionCatalogue,
    PermissionSpec,
    install_permissions,
)
from dotmac_kernel.security import hash_token, issue_access_token
from dotmac_kernel.settings_models import SettingDomain
from dotmac_kernel.settings_resolver import upsert_by_key
from dotmac_kernel.web_deps import TENANT_COOKIE_AUTHENTICATION
from dotmac_kernel.web_runtime import mount_web_surfaces
from dotmac_kernel.web_surfaces import (
    AuthenticationProfileBinding,
    BrowserCapabilityProvision,
    BrowserSessionPolicy,
    NavigationRegion,
    TemplateRef,
    WebFacetMount,
    WebRouteRef,
    WebSurfaceRegistry,
)
from dotmac_template_studio import delivery_seam, deploycheck, service, web
from dotmac_template_studio.contexts import RenderContext, _reset_for_tests
from dotmac_template_studio.models import Template, TemplateVersion
from dotmac_template_studio.preview import IFRAME_SANDBOX, PREVIEW_CSP, build_srcdoc
from dotmac_template_studio.rendering import (
    RenderRefusedError,
    render_part,
    subject_problem,
)
from dotmac_template_studio.settings import REQUIRE_SEPARATE_PUBLISHER
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.features.auth.web import router as auth_web_router

CTX = RenderContext(
    name="ed",
    variables=("customer_name",),
    required=("portal_url",),
    url=("portal_url",),
    sample={
        "customer_name": "Sample <Ada>",
        "portal_url": "https://portal.example.test/x",
    },
)
TEXT = "Hi {customer_name}, open {portal_url}"
HTML = '<p>Hi {customer_name}</p><a href="{portal_url}">Open</a>'


@pytest.fixture(autouse=True)
def _registry():
    previous = _reset_for_tests([CTX])
    delivery_seam.configure_test_send(None)
    web._test_send_log.clear()
    yield
    delivery_seam.configure_test_send(None)
    _reset_for_tests(previous.values())


def _party(db: Session, tenant: Tenant, email: str) -> Party:
    party = Party(
        tenant_id=tenant.id,
        party_type=PartyType.person,
        display_name=email.split("@")[0],
        email=email,
    )
    db.add(party)
    db.flush()
    db.add(PartyPerson(party_id=party.id, first_name="A", last_name="B"))
    db.flush()
    return party


@pytest.fixture
def author(db, tenant_row) -> Party:
    return _party(db, tenant_row, "author@example.test")


@pytest.fixture
def other(db, tenant_row) -> Party:
    return _party(db, tenant_row, "other@example.test")


@pytest.fixture
def template(db, tenant_row) -> Template:
    return service.create_template(
        db, tenant_row.id, slug="welcome", channel="email", context="ed", name="Welcome"
    )


def _draft(db, tenant, template, author=None, **kw) -> TemplateVersion:
    kw.setdefault("body", TEXT)
    kw.setdefault("subject", "Hello {customer_name}")
    return service.create_version(
        db,
        tenant.id,
        template.id,
        author_party_id=author.id if author else None,
        **kw,
    )


# ── Service: maker-checker, restore, preview, diff ──────────────────────────


def test_default_setting_is_off_and_sole_operator_can_author_and_publish(
    db, tenant_row, template, author
):
    """No second person is ever compulsory: same actor authors and publishes."""
    assert REQUIRE_SEPARATE_PUBLISHER.default is False
    assert service.maker_checker_enabled(db, tenant_row.id) is False
    v = _draft(db, tenant_row, template, author)
    published = service.publish_version(
        db, tenant_row.id, template.id, v.version, actor_party_id=author.id
    )
    assert published.published_at is not None
    assert template.published_version == v.version


def _turn_on(db, tenant):
    upsert_by_key(
        db,
        SettingDomain("template_studio"),
        "require_separate_publisher",
        True,
        tenant_id=tenant.id,
    )


def test_maker_checker_on_refuses_author_allows_other_and_rollback(
    db, tenant_row, template, author, other
):
    _turn_on(db, tenant_row)
    assert service.maker_checker_enabled(db, tenant_row.id) is True
    v1 = _draft(db, tenant_row, template, author)
    with pytest.raises(service.PublishRefusedError):
        service.publish_version(
            db, tenant_row.id, template.id, v1.version, actor_party_id=author.id
        )
    assert template.published_version is None
    service.publish_version(
        db, tenant_row.id, template.id, v1.version, actor_party_id=other.id
    )
    v2 = _draft(db, tenant_row, template, other)
    service.publish_version(
        db, tenant_row.id, template.id, v2.version, actor_party_id=author.id
    )
    # Rolling back to an already-published revision is never blocked.
    service.restore_version(db, tenant_row.id, template.id, v1.version)
    assert template.published_version == v1.version


def test_restore_refuses_never_published_and_current(db, tenant_row, template):
    v1 = _draft(db, tenant_row, template)
    with pytest.raises(ConflictError):
        service.restore_version(db, tenant_row.id, template.id, v1.version)
    service.publish_version(db, tenant_row.id, template.id, v1.version)
    with pytest.raises(ConflictError):
        service.restore_version(db, tenant_row.id, template.id, v1.version)


def test_preview_renders_unpublished_draft_with_sample(db, tenant_row, template):
    v = _draft(db, tenant_row, template, html_body=HTML)
    assert template.published_version is None
    out = service.render_preview(db, tenant_row.id, template.id, v.version)
    assert out.text == "Hi Sample <Ada>, open https://portal.example.test/x"
    assert out.subject == "Hello Sample <Ada>"
    assert "Sample &lt;Ada&gt;" in (out.html or "")


def test_diff_against_published_per_part(db, tenant_row, template):
    v1 = _draft(db, tenant_row, template)
    service.publish_version(db, tenant_row.id, template.id, v1.version)
    v2 = _draft(db, tenant_row, template, body=TEXT + "\nExtra <b>line</b>")
    pv, diffs = service.diff_against_published(
        db, tenant_row.id, template.id, v2.version
    )
    assert pv == v1.version
    by = {d.part: d for d in diffs}
    assert by["subject"].changed is False and by["text"].changed is True
    assert ("+", "Extra <b>line</b>") in by["text"].lines
    assert "html" not in by


def test_update_version_empty_subject_clears_it(db, tenant_row, template):
    v = _draft(db, tenant_row, template)
    service.update_version(db, tenant_row.id, template.id, v.version, subject="")
    assert v.subject is None


# ── Subject Unicode narrowing ───────────────────────────────────────────────


@pytest.mark.parametrize("ch", ["‌", "‍", "­", "‎", "‏", "؜"])
def test_subject_accepts_formatting_characters(ch, db, tenant_row, template):
    assert subject_problem(f"Ada{ch}Lovelace") is None
    v = _draft(db, tenant_row, template, subject=f"Hi{ch} {{customer_name}}")
    assert v.subject
    service.publish_version(db, tenant_row.id, template.id, v.version)
    subject, _ = service.render_published(
        db,
        tenant_row.id,
        "welcome",
        "email",
        {"customer_name": f"A{ch}B", "portal_url": "https://x.example.test"},
    )
    assert ch in subject


@pytest.mark.parametrize(
    "ch",
    [
        "‪",
        "‫",
        "‬",
        "‭",
        "‮",
        "⁦",
        "⁧",
        "⁨",
        "⁩",
        "\u200b",
        "\u2060",
        "\ufeff",
        "\x00",
        "\n",
        "\r",
        "\t",
        "\x7f",
        "\x85",
        "\u2028",
        "\u2029",
    ],
)
def test_subject_still_refuses_bidi_and_controls(ch, db, tenant_row, template):
    assert subject_problem(f"A{ch}B") is not None
    with pytest.raises(BadRequestError):  # at save
        _draft(db, tenant_row, template, subject=f"A{ch}B")
    with pytest.raises(RenderRefusedError):
        render_part(
            "{customer_name}", {"customer_name": f"A{ch}B"}, "subject", context=CTX
        )


# ── Preview document and static guards ──────────────────────────────────────


def test_srcdoc_carries_restrictive_csp_before_content():
    doc = build_srcdoc("<p>x</p>")
    assert f'content="{PREVIEW_CSP}"' in doc
    assert doc.index("Content-Security-Policy") < doc.index("<p>x</p>")
    assert "default-src 'none'" in PREVIEW_CSP
    assert "allow-scripts" not in IFRAME_SANDBOX
    assert "allow-same-origin" not in IFRAME_SANDBOX


def test_package_templates_never_use_safe_filter():
    root = Path(web.template_dir())
    offenders = [
        p.name
        for p in root.rglob("*.html")
        if re.search(
            r"\|\s*safe\b|autoescape\s+false|\bMarkup\(",
            re.sub(r"\{#.*?#\}", "", p.read_text(), flags=re.S),
        )
    ]
    assert not offenders, offenders
    assert "Markup" not in Path(web.__file__).read_text()
    assert "HTMLResponse(rendered" not in Path(web.__file__).read_text()


# ── Deploy check ────────────────────────────────────────────────────────────


@contextmanager
def _same_session(db):
    yield db


def test_deploy_check_reports_invalid_published_row_without_content(
    db, tenant_row, template
):
    secret = "TOPSECRET-BODY-TEXT"
    v = _draft(db, tenant_row, template)
    service.publish_version(db, tenant_row.id, template.id, v.version)
    ok = deploycheck.check_published_revisions(
        list_tenant_ids=lambda: [tenant_row.id],
        tenant_session=lambda _t: _same_session(db),
    )
    assert ok.ok and ok.revisions_checked == 1
    # A legacy/tightened-rule row: published, but no longer valid.
    v.body = f"{secret} {{nope}} {{{{double}}}}"
    v.html_body = "<script>alert(1)</script>"
    db.flush()
    report = deploycheck.check_published_revisions(
        list_tenant_ids=lambda: [tenant_row.id],
        tenant_session=lambda _t: _same_session(db),
    )
    assert not report.ok and len(report.failures) == 1
    failure = report.failures[0]
    assert (failure.slug, failure.channel, failure.version) == (
        "welcome",
        "email",
        v.version,
    )
    rendered = "\n".join(report.lines())
    assert "{nope}" in rendered  # names the variable, a problem not content
    assert secret not in rendered and "alert(1)" not in rendered


def test_deploy_check_flags_unregistered_context(db, tenant_row, template):
    v = _draft(db, tenant_row, template)
    service.publish_version(db, tenant_row.id, template.id, v.version)
    _reset_for_tests([])
    report = deploycheck.check_published_revisions(
        list_tenant_ids=lambda: [tenant_row.id],
        tenant_session=lambda _t: _same_session(db),
    )
    assert not report.ok and "not registered" in report.failures[0].problems[0]


def test_deploy_check_uses_one_scoped_session_per_tenant(db, tenant_row, template):
    other_tenant = Tenant(slug=f"t-{uuid4().hex[:6]}", name="Other")
    db.add(other_tenant)
    db.flush()
    opened: list = []

    @contextmanager
    def tenant_session(tid):
        opened.append(tid)
        yield db

    report = deploycheck.check_published_revisions(
        list_tenant_ids=lambda: [tenant_row.id, other_tenant.id],
        tenant_session=tenant_session,
    )
    assert opened == [tenant_row.id, other_tenant.id] and report.tenants_checked == 2


def test_deploy_check_source_has_no_unscoped_shortcuts():
    text = Path(deploycheck.__file__).read_text()
    assert "platform_session" not in text.replace("No BYPASSRLS", "")
    assert "BYPASSRLS" in text  # only in the prose saying none is used
    assert "tenant_session" in text and "resolver_session" in text


# ── Web surface ─────────────────────────────────────────────────────────────


def _build_client(db, tenant, *, csrf_enabled=False) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)
    app.add_middleware(
        CSRFMiddleware,
        enabled=csrf_enabled,
        secret="editor-test-csrf-not-a-deployment-secret",
    )
    from dotmac_template_studio import module as studio

    manifests = (
        ModuleManifest(
            code="portal_access",
            version="1",
            permissions=(PermissionSpec("web.portal.staff.access"),),
        ),
        ModuleManifest(
            code="auth", version="1", contract_version=1, web_routers=(auth_web_router,)
        ),
        studio,
    )
    install_permissions(PermissionCatalogue.from_manifests(manifests))
    install_capabilities(CapabilityCatalogue.from_manifests(manifests))
    registry = WebSurfaceRegistry(
        manifests=manifests,
        facets=(
            WebFacetMount(
                code="staff_admin",
                url_prefix="/admin",
                shell=TemplateRef("layouts/admin.html"),
                authentication_profile="staff_session",
                admission_permission="web.portal.staff.access",
                navigation_regions=(NavigationRegion("primary"),),
                entry_routes=(
                    WebRouteRef("auth", "legacy", "login_page"),
                    WebRouteRef("auth", "legacy", "login_submit"),
                    WebRouteRef("auth", "legacy", "logout"),
                ),
                login_route=WebRouteRef("auth", "legacy", "login_page"),
            ),
        ),
        authentication_profiles=(
            AuthenticationProfileBinding(
                code="staff_session",
                provider=TENANT_COOKIE_AUTHENTICATION,
                session=BrowserSessionPolicy(cookie_name="access_token"),
            ),
        ),
        browser_capabilities=(BrowserCapabilityProvision("htmx", 2),),
        ui_contract_version=1,
    )
    from dotmac_kernel.templating import compose_templates

    from app.assembly import assembly

    compose_templates(
        assembly_dir=assembly.assembly_template_dir,
        packaged_dirs=assembly.packaged_template_dirs,
        namespaced_dirs={"template_studio": web.template_dir()},
    )
    mount_web_surfaces(
        app, registry=registry, enabled_modules=frozenset(m.code for m in manifests)
    )

    @app.middleware("http")
    async def _inject_tenant(request: Request, call_next):
        request.state.tenant = tenant
        return await call_next(request)

    def _override() -> Generator[Session, None, None]:
        yield db

    app.dependency_overrides[get_db] = _override
    return TestClient(app, raise_server_exceptions=False)


def _login(db, tenant, party) -> dict[str, str]:
    token, expires_at = issue_access_token(party.id, tenant.id)
    db.add(
        AuthSession(
            tenant_id=tenant.id,
            party_id=party.id,
            token_hash=hash_token(token),
            expires_at=expires_at,
        )
    )
    db.flush()
    return {"access_token": token}


@pytest.fixture
def allow_all(monkeypatch):
    for target in ("dotmac_kernel.deps", "dotmac_kernel.web_runtime"):
        monkeypatch.setattr(f"{target}.authorize_party", lambda *a, **k: True)


@pytest.fixture
def client(db, tenant_row, allow_all):
    return _build_client(db, tenant_row)


@pytest.fixture
def cookies(db, tenant_row, author):
    return _login(db, tenant_row, author)


def _url(template, *tail):
    return "/admin/templates/" + str(template.id) + "".join(f"/{t}" for t in tail)


def _audit_actions(db) -> list[str]:
    from dotmac_kernel.audit import AuditEvent

    return list(db.execute(select(AuditEvent.action)).scalars())


def test_editor_saves_draft_through_validation_and_shows_refusal(
    client, cookies, db, tenant_row, template
):
    v = _draft(db, tenant_row, template)
    page = client.get(_url(template, "versions", v.version), cookies=cookies)
    assert page.status_code == 200 and "Save draft" in page.text
    ok = client.post(
        _url(template, "versions", v.version, "edit"),
        cookies=cookies,
        data={"subject": "S", "body": TEXT + " more", "html_body": HTML},
    )
    assert ok.status_code == 200 and "Draft saved." in ok.text
    db.refresh(v)
    assert v.body.endswith("more") and v.html_body
    bad = client.post(
        _url(template, "versions", v.version, "edit"),
        cookies=cookies,
        data={"subject": "S", "body": TEXT, "html_body": "<script>alert(1)</script>"},
    )
    assert bad.status_code == 200
    assert "html part" in bad.text.lower() or "html:" in bad.text
    assert "<script>alert(1)</script>" not in bad.text  # echoed escaped only
    db.refresh(v)
    assert "script" not in (v.html_body or "")


def test_published_revision_cannot_be_edited_in_the_ui(
    client, cookies, db, tenant_row, template
):
    v = _draft(db, tenant_row, template)
    service.publish_version(db, tenant_row.id, template.id, v.version)
    resp = client.post(
        _url(template, "versions", v.version, "edit"),
        cookies=cookies,
        data={"subject": "S", "body": TEXT + " x"},
    )
    assert resp.status_code == 200 and "published and cannot be edited" in resp.text


def test_preview_iframe_sandbox_csp_canary_and_hostile_html(
    client, cookies, db, tenant_row, template
):
    hostile = RenderContext(
        name="ed",
        variables=("customer_name",),
        required=("portal_url",),
        url=("portal_url",),
        sample={
            "customer_name": '"><script>alert(3)</script>',
            "portal_url": "https://portal.example.test/x",
        },
    )
    _reset_for_tests([hostile])
    v = _draft(db, tenant_row, template, html_body=HTML)
    # Simulate a legacy/hostile stored row that bypassed the save-time sanitizer.
    v.html_body = (
        '<img src="x" onerror="alert(2)"><p onclick="alert(4)">Hi {customer_name}</p>'
        '<a href="{portal_url}">x</a>'
    )
    db.flush()
    resp = client.get(_url(template, "versions", v.version), cookies=cookies)
    assert resp.status_code == 200
    page = resp.text
    iframe = re.search(r"<iframe\b[^>]*>", page)
    assert iframe, re.findall(r'role="alert"[^>]*>([^<]*)', page)
    tag = iframe.group(0)
    sandbox = re.search(r'sandbox="([^"]*)"', tag)
    assert sandbox is not None  # present, even if empty
    assert "allow-scripts" not in sandbox.group(1)
    assert "allow-same-origin" not in sandbox.group(1)
    assert "allow-scripts" not in page and "allow-same-origin" not in page
    srcdoc = re.search(r'srcdoc="([^"]*)"', tag)
    assert srcdoc is not None
    assert "Content-Security-Policy" in srcdoc.group(1)
    assert "default-src &#39;none&#39;" in srcdoc.group(1)
    # Hostile markup appears only attribute-escaped inside srcdoc, never raw in
    # the admin page itself.
    outside = page.replace(srcdoc.group(1), "")
    for needle in ("<script>alert", "<img", "<p onclick"):
        assert needle not in outside, needle
    assert "&lt;img" in srcdoc.group(1)
    assert "&lt;script&gt;alert(3)" not in srcdoc.group(1)  # value escaped twice over
    assert "&amp;lt;script&amp;gt;alert(3)" in srcdoc.group(1)
    assert page.count("<iframe") == 1


def test_stored_script_or_iframe_html_is_refused_not_previewed(
    client, cookies, db, tenant_row, template
):
    v = _draft(db, tenant_row, template, html_body=HTML)
    v.html_body = "<script>alert(1)</script><p>Hi {customer_name} {portal_url}</p>"
    db.flush()
    page = client.get(_url(template, "versions", v.version), cookies=cookies).text
    assert "<iframe" not in page and "<script>alert" not in page
    assert "not allowed in an html part" in page


def test_preview_text_part_is_escaped_not_raw(
    client, cookies, db, tenant_row, template
):
    v = _draft(db, tenant_row, template)
    resp = client.get(_url(template, "versions", v.version), cookies=cookies)
    assert "Sample &lt;Ada&gt;" in resp.text and "Sample <Ada>" not in resp.text


def test_diff_page_escapes_content(client, cookies, db, tenant_row, template):
    v1 = _draft(db, tenant_row, template)
    service.publish_version(db, tenant_row.id, template.id, v1.version)
    v2 = _draft(db, tenant_row, template, body=TEXT + "\n<img src=x onerror=alert(1)>")
    resp = client.get(_url(template, "versions", v2.version), cookies=cookies)
    assert "<img src=x onerror" not in resp.text and "&lt;img" in resp.text


def test_publish_requires_confirmation_then_audits(
    client, cookies, db, tenant_row, template, author
):
    v = _draft(db, tenant_row, template, author)
    # The sole operator authors and publishes their own version (setting off).
    confirm = client.get(
        _url(template, "versions", v.version, "confirm"), cookies=cookies
    )
    assert confirm.status_code == 200 and "Confirm publish" in confirm.text
    no = client.post(_url(template, "versions", v.version, "publish"), cookies=cookies)
    assert "Confirm to continue." in no.text
    db.refresh(template)
    assert template.published_version is None
    yes = client.post(
        _url(template, "versions", v.version, "publish"),
        cookies=cookies,
        data={"confirm": "yes"},
    )
    assert yes.status_code == 200 and "is now published" in yes.text
    db.refresh(template)
    assert template.published_version == v.version
    assert "template_studio.version.publish" in _audit_actions(db)


def test_restore_flow_audited_and_publish_of_old_version_redirected_to_restore(
    client, cookies, db, tenant_row, template
):
    v1 = _draft(db, tenant_row, template)
    service.publish_version(db, tenant_row.id, template.id, v1.version)
    v2 = _draft(db, tenant_row, template, body=TEXT + " v2")
    service.publish_version(db, tenant_row.id, template.id, v2.version)
    page = client.get(
        _url(template, "versions", v1.version, "confirm"), cookies=cookies
    )
    assert "Confirm restore" in page.text
    via_publish = client.post(
        _url(template, "versions", v1.version, "publish"),
        cookies=cookies,
        data={"confirm": "yes"},
    )
    assert "restore it instead" in via_publish.text
    done = client.post(
        _url(template, "versions", v1.version, "restore"),
        cookies=cookies,
        data={"confirm": "yes"},
    )
    assert "Restored version" in done.text
    db.refresh(template)
    assert template.published_version == v1.version
    assert "template_studio.version.restore" in _audit_actions(db)


def test_maker_checker_refusal_in_ui_is_audited(
    client, cookies, db, tenant_row, template, author
):
    _turn_on(db, tenant_row)
    v = _draft(db, tenant_row, template, author)
    resp = client.post(
        _url(template, "versions", v.version, "publish"),
        cookies=cookies,
        data={"confirm": "yes"},
    )
    assert "requires a different person" in resp.text
    db.refresh(template)
    assert template.published_version is None
    assert "template_studio.version.publish_refused" in _audit_actions(db)


def test_deactivate_and_reactivate(client, cookies, db, tenant_row, template):
    resp = client.post(_url(template, "status"), cookies=cookies, data={"active": "0"})
    assert "deactivated" in resp.text
    db.refresh(template)
    assert template.is_active is False
    client.post(_url(template, "status"), cookies=cookies, data={"active": "1"})
    db.refresh(template)
    assert template.is_active is True


class _Recorder:
    def __init__(self, fail=False):
        self.messages = []
        self.fail = fail

    def __call__(self, db, message):
        if self.fail:
            raise RuntimeError("provider secret detail")
        self.messages.append(message)


def test_test_send_hidden_without_seam_and_refused_if_posted(
    client, cookies, db, tenant_row, template
):
    v = _draft(db, tenant_row, template)
    page = client.get(_url(template, "versions", v.version), cookies=cookies)
    assert "Send test to me" not in page.text
    resp = client.post(
        _url(template, "versions", v.version, "test-send"), cookies=cookies
    )
    assert "not configured" in resp.text
    assert "template_studio.version.test_send" in _audit_actions(db)


def test_test_send_goes_only_to_the_acting_user(
    client, cookies, db, tenant_row, template, author
):
    rec = _Recorder()
    delivery_seam.configure_test_send(rec, channels=("email",))
    v = _draft(db, tenant_row, template, html_body=HTML)
    page = client.get(_url(template, "versions", v.version), cookies=cookies)
    assert "Send test to me" in page.text
    assert 'name="to"' not in page.text and 'name="recipient"' not in page.text
    resp = client.post(
        _url(template, "versions", v.version, "test-send"),
        cookies=cookies,
        data={
            "to": "victim@example.test",
            "recipient": "victim@example.test",
            "recipient_email": "victim@example.test",
        },
    )
    assert "sent to your own address" in resp.text
    (msg,) = rec.messages
    assert msg.recipient_email == "author@example.test"
    assert msg.recipient_party_id == author.id and msg.tenant_id == tenant_row.id
    assert msg.subject == "Hello Sample <Ada>" and msg.html
    assert "victim@example.test" not in resp.text


def test_test_send_failure_is_generic_audited_and_rate_limited(
    client, cookies, db, tenant_row, template
):
    delivery_seam.configure_test_send(_Recorder(fail=True))
    v = _draft(db, tenant_row, template)
    url = _url(template, "versions", v.version, "test-send")
    resp = client.post(url, cookies=cookies)
    assert "could not be sent" in resp.text and "provider secret" not in resp.text
    for _ in range(web._TEST_SEND_LIMIT):
        resp = client.post(url, cookies=cookies)
    assert "Too many test sends" in resp.text
    assert _audit_actions(db).count("template_studio.version.test_send") >= 2


def test_test_send_not_offered_for_other_channels(client, cookies, db, tenant_row):
    delivery_seam.configure_test_send(_Recorder(), channels=("email",))
    sms = service.create_template(
        db, tenant_row.id, slug="sms-welcome", channel="sms", context="ed", name="S"
    )
    v = service.create_version(db, tenant_row.id, sms.id, body=TEXT)
    page = client.get(_url(sms, "versions", v.version), cookies=cookies)
    assert "Send test to me" not in page.text


# ── CSRF and guards on every new state-changing route ───────────────────────


def _post_routes():
    return sorted(
        (r.path, r.name)
        for r in web.router.routes
        if "POST" in getattr(r, "methods", ())
    )


def test_every_post_route_in_the_editor_is_csrf_protected(
    db, tenant_row, allow_all, template, cookies
):
    guarded = _build_client(db, tenant_row, csrf_enabled=True)
    routes = _post_routes()
    new = {
        "update_version",
        "test_send_version",
        "restore_version",
        "set_status",
        "publish_version",
        "create_version",
    }
    assert new <= {name for _, name in routes}
    for path, _name in routes:
        concrete = "/admin" + path.replace("{template_id}", str(template.id)).replace(
            "{version}", "1"
        )
        resp = guarded.post(concrete, cookies=cookies, data={})
        assert resp.status_code == 403, (concrete, resp.status_code)


def test_state_changing_templates_embed_csrf_token():
    root = Path(web.template_dir())
    for name in ("version.html", "confirm.html", "detail.html"):
        text = (root / "admin" / "template_studio" / name).read_text()
        forms = re.findall(r"<form\b[^>]*method=\"post\"[^>]*>.*?</form>", text, re.S)
        assert forms, name
        assert all('name="csrf_token"' in f for f in forms), name


def test_api_publish_refusal_is_409_and_audited(db, tenant_row, template, author):
    from dotmac_template_studio import router as api

    _turn_on(db, tenant_row)
    v = _draft(db, tenant_row, template, author)
    resp = api.publish_version(template.id, v.version, db, tenant_row, author)
    assert resp.status_code == 409
    assert "template_studio.version.publish_refused" in _audit_actions(db)
    db.refresh(template)
    assert template.published_version is None


def test_api_publish_by_sole_operator_with_default_setting(
    db, tenant_row, template, author
):
    from dotmac_template_studio import router as api

    v = _draft(db, tenant_row, template, author)
    out = api.publish_version(template.id, v.version, db, tenant_row, author)
    assert (
        out.version == v.version
        and "template_studio.version.publish" in _audit_actions(db)
    )


def test_public_surface_exposes_the_seam():
    import dotmac_template_studio as studio

    assert studio.configure_test_send is delivery_seam.configure_test_send
