"""`dotmac-settings-admin`: the generic settings write path, JSON API and staff surface.

Replaces the coverage of the deleted `app/features/settings` (router, web, service)
and adds the new rules: actor + reason on every write, undeclared keys refused,
restore as a new audited write, secrets only set/unset, export/import with preview,
optional maker-checker (default OFF), and the ERP v2 AR composition proof.

SQLite unit lane (no RLS): tenancy isolation of the rows is the kernel's canary.
"""

from __future__ import annotations

import json
from collections.abc import Generator

import pytest
from cryptography.fernet import Fernet
from dotmac_kernel import settings_crypto as sc
from dotmac_kernel import settings_resolver as sr
from dotmac_kernel.audit import AuditEvent
from dotmac_kernel.deps import get_db, require_user_auth
from dotmac_kernel.errors import register_error_handlers
from dotmac_kernel.exceptions import BadRequestError, NotFoundError
from dotmac_kernel.models import (
    Party,
    PartyPerson,
    PartyRoleGrant,
    PartyType,
    Role,
    Tenant,
)
from dotmac_kernel.modules import ModuleManifest
from dotmac_kernel.setting_domains import (
    SettingDomainRegistry,
    install_setting_domains,
)
from dotmac_kernel.settings_models import (
    DomainSettingHistory,
    SettingDomain,
    SettingValueType,
)
from dotmac_settings_admin import module as settings_admin_module
from dotmac_settings_admin import service, transfer
from dotmac_settings_admin.router import router as api_router
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session


def _party(db: Session, tenant: Tenant, email: str, *, admin: bool = True) -> Party:
    party = Party(
        tenant_id=tenant.id,
        party_type=PartyType.person,
        display_name=email.split("@")[0],
        email=email,
    )
    db.add(party)
    db.flush()
    db.add(PartyPerson(party_id=party.id, first_name="A", last_name="B"))
    if admin:
        role = db.scalar(
            select(Role).where(Role.tenant_id == tenant.id, Role.slug == "admin")
        ) or Role(tenant_id=tenant.id, slug="admin", name="Admin")
        db.add(role)
        db.flush()
        db.add(PartyRoleGrant(tenant_id=tenant.id, party_id=party.id, role_id=role.id))
    db.flush()
    return party


@pytest.fixture()
def admin(db, tenant_row) -> Party:
    return _party(db, tenant_row, "admin@acme.test")


@pytest.fixture()
def actor(admin) -> service.Actor:
    return service.Actor(party_id=admin.id, request_id="req-1")


@pytest.fixture()
def secret_spec(monkeypatch):
    monkeypatch.setenv(sc.KEY_ENV_VAR, Fernet.generate_key().decode())
    spec = sr.SettingSpec(
        domain=SettingDomain.auth,
        key="test_secret_token",
        value_type=SettingValueType.string,
        default="unset",
        is_secret=True,
    )
    sr.register_specs([spec])
    yield spec
    del sr._REGISTRY[(spec.domain, spec.key)]


def _write(db, tenant, actor, domain, key, value, reason="because", **kw):
    kw.setdefault("may_approve", True)
    return service.write_setting(
        db,
        tenant.id,
        actor=actor,
        domain=domain,
        key=key,
        value=value,
        reason=reason,
        **kw,
    )


def _audit(db, tenant) -> list[AuditEvent]:
    return list(db.scalars(select(AuditEvent).where(AuditEvent.tenant_id == tenant.id)))


# ── Service: the write rules ────────────────────────────────────────────────


def test_write_records_actor_reason_history_and_audit(db, tenant_row, actor):
    out = _write(
        db, tenant_row, actor, "custom_fields", "max_per_entity", 5, "raise cap"
    )
    assert out.applied and out.setting.value == 5 and out.setting.source == "tenant"
    hist = db.scalars(select(DomainSettingHistory)).all()
    assert len(hist) == 1
    assert hist[0].changed_by_party_id == actor.party_id
    assert hist[0].change_reason == "raise cap"
    (event,) = _audit(db, tenant_row)
    assert event.action == "settings_admin.setting.update"
    assert event.actor_party_id == actor.party_id
    assert event.details["reason"] == "raise cap"
    assert "value" not in event.details


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_a_write_without_a_reason_is_refused_and_writes_nothing(
    db, tenant_row, actor, reason
):
    with pytest.raises(BadRequestError, match="reason"):
        _write(db, tenant_row, actor, "custom_fields", "max_per_entity", 5, reason)
    assert not db.scalars(select(DomainSettingHistory)).all()
    assert not _audit(db, tenant_row)
    assert (
        service.get_setting(db, tenant_row.id, "custom_fields", "max_per_entity").source
        == "default"
    )


def test_a_write_without_an_actor_is_refused(db, tenant_row):
    with pytest.raises(BadRequestError, match="actor"):
        _write(db, tenant_row, None, "custom_fields", "max_per_entity", 5)


def test_undeclared_domain_and_key_are_refused(db, tenant_row, actor):
    with pytest.raises(NotFoundError):
        _write(db, tenant_row, actor, "no_such_domain", "k", 1)
    with pytest.raises(NotFoundError):
        _write(db, tenant_row, actor, "custom_fields", "no_such_key", 1)
    assert not _audit(db, tenant_row)


def test_internal_keys_are_not_editable(db, tenant_row, actor):
    with pytest.raises(NotFoundError):
        _write(
            db, tenant_row, actor, "settings_admin", "pending_changes", {"items": {}}
        )
    assert all(
        s.key != "pending_changes"
        for s in service.list_settings(db, tenant_row.id, "settings_admin")
    )


def test_invalid_value_is_refused_before_anything_is_stored(db, tenant_row, actor):
    with pytest.raises(BadRequestError):
        _write(db, tenant_row, actor, "custom_fields", "max_per_entity", "not-a-number")
    with pytest.raises(BadRequestError):
        _write(db, tenant_row, actor, "display", "timezone", "Mars/Olympus")
    assert not db.scalars(select(DomainSettingHistory)).all()


def test_retired_branding_key_is_refused(db, tenant_row, actor):
    with pytest.raises(BadRequestError, match="no longer accepts"):
        _write(db, tenant_row, actor, "branding", "ui_branding", {"custom_css": "x{}"})


# ── History and restore ─────────────────────────────────────────────────────


def test_restore_is_a_new_audited_write_and_history_is_never_edited(
    db, tenant_row, actor
):
    _write(db, tenant_row, actor, "display", "timezone", "Europe/London", "first")
    _write(db, tenant_row, actor, "display", "timezone", "Asia/Tokyo", "second")
    entries = service.list_history(db, tenant_row.id, "display", "timezone")
    assert len(entries) == 2
    update = next(e for e in entries if e.action == "update")
    assert update.value_before is not None
    before_ids = {e.id for e in entries}

    out = service.restore_setting(
        db,
        tenant_row.id,
        actor=actor,
        history_id=update.id,
        reason="undo second",
        may_approve=True,
    )
    assert out.setting.value == "Europe/London"
    after = service.list_history(db, tenant_row.id, "display", "timezone")
    assert len(after) == 3 and before_ids < {e.id for e in after}
    assert {e.id: e.value_after for e in entries} == {
        e.id: e.value_after for e in after if e.id in before_ids
    }
    assert "settings_admin.setting.restore" in [
        e.action for e in _audit(db, tenant_row)
    ]


def test_restoring_a_create_clears_the_override_as_a_new_write(db, tenant_row, actor):
    _write(db, tenant_row, actor, "display", "timezone", "Europe/London")
    (create,) = service.list_history(db, tenant_row.id, "display", "timezone")
    out = service.restore_setting(
        db,
        tenant_row.id,
        actor=actor,
        history_id=create.id,
        reason="back to inherited",
        may_approve=True,
    )
    assert out.setting.has_override is False and out.setting.source != "tenant"
    assert len(service.list_history(db, tenant_row.id, "display", "timezone")) == 2


def test_restore_needs_a_reason(db, tenant_row, actor):
    _write(db, tenant_row, actor, "display", "timezone", "Europe/London")
    (create,) = service.list_history(db, tenant_row.id, "display", "timezone")
    with pytest.raises(BadRequestError, match="reason"):
        service.restore_setting(
            db,
            tenant_row.id,
            actor=actor,
            history_id=create.id,
            reason="",
            may_approve=True,
        )


def test_restore_of_a_json_setting_round_trips(db, tenant_row, actor):
    _write(db, tenant_row, actor, "branding", "ui_branding", {"name": "One"})
    _write(db, tenant_row, actor, "branding", "ui_branding", {"name": "Two"})
    upd = next(
        e
        for e in service.list_history(db, tenant_row.id, "branding", "ui_branding")
        if e.action == "update"
    )
    out = service.restore_setting(
        db,
        tenant_row.id,
        actor=actor,
        history_id=upd.id,
        reason="r",
        may_approve=True,
    )
    assert out.setting.value == {"name": "One"}


# ── Secrets ─────────────────────────────────────────────────────────────────


def test_a_secret_is_only_ever_set_or_unset(db, tenant_row, actor, secret_spec):
    before = service.get_setting(db, tenant_row.id, "auth", "test_secret_token")
    assert before.is_secret and before.value is None and before.is_set is False
    _write(db, tenant_row, actor, "auth", "test_secret_token", "sekret-value")
    after = service.get_setting(db, tenant_row.id, "auth", "test_secret_token")
    assert after.value is None and after.is_set is True
    (event,) = _audit(db, tenant_row)
    assert "sekret-value" not in json.dumps(event.details)
    (hist,) = db.scalars(select(DomainSettingHistory)).all()
    assert hist.value_after is None and hist.secret_changed is True


def test_a_secret_cannot_be_restored_and_is_never_exported(
    db, tenant_row, actor, secret_spec
):
    _write(db, tenant_row, actor, "auth", "test_secret_token", "one")
    _write(db, tenant_row, actor, "auth", "test_secret_token", "two")
    hist = service.list_history(db, tenant_row.id, "auth", "test_secret_token")
    assert all(not h.restorable for h in hist)
    with pytest.raises(BadRequestError, match="secret"):
        service.restore_setting(
            db,
            tenant_row.id,
            actor=actor,
            history_id=hist[0].id,
            reason="r",
            may_approve=True,
        )
    exported = transfer.export_settings(db, tenant_row.id)
    blob = json.dumps(exported)
    assert "one" not in blob.replace("secrets_omitted", "") and "two" not in blob
    assert "auth/test_secret_token" in exported["secrets_omitted"]
    assert all(e["key"] != "test_secret_token" for e in exported["settings"])


# ── Export / import ─────────────────────────────────────────────────────────


def test_export_import_round_trip_with_preview_and_drift_refusal(db, tenant_row, actor):
    _write(db, tenant_row, actor, "display", "timezone", "Europe/London")
    document = transfer.export_settings(db, tenant_row.id)
    assert document["format"] == transfer.EXPORT_FORMAT
    service.clear_setting(
        db,
        tenant_row.id,
        actor=actor,
        domain="display",
        key="timezone",
        reason="reset",
        may_approve=True,
    )
    plan = transfer.plan_import(db, tenant_row.id, document)
    assert plan.ok and [e.status for e in plan.entries] == ["change"]
    # Preview writes nothing.
    assert (
        service.get_setting(db, tenant_row.id, "display", "timezone").source != "tenant"
    )
    n = transfer.apply_import(
        db,
        tenant_row.id,
        actor=actor,
        raw=document,
        reason="restore backup",
        expected_digest=plan.digest,
        may_approve=True,
    )
    assert n == 1
    assert (
        service.get_setting(db, tenant_row.id, "display", "timezone").value
        == "Europe/London"
    )
    again = transfer.plan_import(db, tenant_row.id, document)
    assert [e.status for e in again.entries] == ["unchanged"]


def test_import_refuses_undeclared_secret_invalid_and_drift(
    db, tenant_row, actor, secret_spec
):
    doc = {
        "format": transfer.EXPORT_FORMAT,
        "settings": [
            {"domain": "display", "key": "nope", "value": 1},
            {"domain": "auth", "key": "test_secret_token", "value": "x"},
            {"domain": "display", "key": "timezone", "value": "Mars/Olympus"},
            {
                "domain": "settings_admin",
                "key": "pending_changes",
                "value": {"items": {}},
            },
        ],
    }
    plan = transfer.plan_import(db, tenant_row.id, doc)
    assert not plan.ok and len(plan.refused) == 4
    with pytest.raises(BadRequestError, match="refused"):
        transfer.apply_import(
            db,
            tenant_row.id,
            actor=actor,
            raw=doc,
            reason="r",
            expected_digest=plan.digest,
            may_approve=True,
        )
    good = {
        "format": transfer.EXPORT_FORMAT,
        "settings": [
            {"domain": "display", "key": "timezone", "value": "Europe/London"}
        ],
    }
    stale = transfer.plan_import(db, tenant_row.id, good)
    _write(db, tenant_row, actor, "display", "timezone", "Asia/Tokyo")
    from dotmac_kernel.exceptions import ConflictError

    with pytest.raises(ConflictError):
        transfer.apply_import(
            db,
            tenant_row.id,
            actor=actor,
            raw=good,
            reason="r",
            expected_digest=stale.digest,
            may_approve=True,
        )


def test_import_needs_a_reason_and_a_valid_document(db, tenant_row, actor):
    good = {"format": transfer.EXPORT_FORMAT, "settings": []}
    with pytest.raises(BadRequestError, match="reason"):
        transfer.apply_import(
            db,
            tenant_row.id,
            actor=actor,
            raw=good,
            reason="",
            expected_digest="x",
            may_approve=True,
        )
    with pytest.raises(BadRequestError):
        transfer.plan_import(db, tenant_row.id, {"format": "other", "settings": []})


# ── Maker-checker ───────────────────────────────────────────────────────────


def test_maker_checker_defaults_off_and_a_sole_operator_writes_directly(
    db, tenant_row, actor
):
    assert (
        sr.get_spec(SettingDomain("settings_admin"), "maker_checker").default is False
    )
    assert service.maker_checker_enabled(db, tenant_row.id) is False
    out = _write(
        db, tenant_row, actor, "display", "timezone", "Asia/Tokyo", may_approve=False
    )
    assert out.applied


def test_maker_checker_on_proposes_then_a_different_approver_applies(
    db, tenant_row, actor
):
    _write(db, tenant_row, actor, "settings_admin", "maker_checker", True)
    maker = _party(db, tenant_row, "maker@acme.test")
    checker = _party(db, tenant_row, "checker@acme.test")
    maker_actor = service.Actor(party_id=maker.id)
    out = _write(
        db,
        tenant_row,
        maker_actor,
        "display",
        "timezone",
        "Asia/Tokyo",
        "proposal",
        may_approve=False,
    )
    assert out.applied is False and out.proposal_id
    assert (
        service.get_setting(db, tenant_row.id, "display", "timezone").source != "tenant"
    )
    assert len(service.list_proposals(db, tenant_row.id)) == 1
    from dotmac_kernel.exceptions import ForbiddenError

    with pytest.raises(ForbiddenError, match="different person"):
        service.approve_proposal(
            db,
            tenant_row.id,
            actor=maker_actor,
            proposal_id=out.proposal_id,
            reason="self",
        )
    applied = service.approve_proposal(
        db,
        tenant_row.id,
        actor=service.Actor(party_id=checker.id),
        proposal_id=out.proposal_id,
        reason="ok",
    )
    assert applied.value == "Asia/Tokyo" and not service.list_proposals(
        db, tenant_row.id
    )
    actions = [e.action for e in _audit(db, tenant_row)]
    assert (
        "settings_admin.proposal.create" in actions
        and "settings_admin.proposal.approve" in actions
    )


def test_maker_checker_refuses_staged_secrets_and_supports_rejection(
    db, tenant_row, actor, secret_spec
):
    from dotmac_kernel.exceptions import ForbiddenError

    _write(db, tenant_row, actor, "settings_admin", "maker_checker", True)
    with pytest.raises(ForbiddenError, match="Secret"):
        _write(
            db, tenant_row, actor, "auth", "test_secret_token", "x", may_approve=False
        )
    out = _write(
        db, tenant_row, actor, "display", "timezone", "Asia/Tokyo", may_approve=False
    )
    service.reject_proposal(
        db, tenant_row.id, actor=actor, proposal_id=out.proposal_id, reason="no"
    )
    assert not service.list_proposals(db, tenant_row.id)


# ── Listing ─────────────────────────────────────────────────────────────────


def test_every_declared_spec_is_listed_generically_grouped_by_owner(db, tenant_row):
    domains = {d.domain: d for d in service.list_domains(db)}
    assert {"branding", "display", "organisation", "custom_fields"} <= set(domains)
    assert domains["display"].owner == "settings_admin"
    assert domains["custom_fields"].owner != "settings_admin"
    declared = {
        (str(s.domain), s.key)
        for s in sr.all_specs()
        if (str(s.domain), s.key) != ("settings_admin", "pending_changes")
    }
    listed = {
        (v.domain, v.key)
        for d in domains
        for v in service.list_settings(db, tenant_row.id, d)
    }
    assert declared <= listed


def test_organisation_profile_is_settable_and_readable(db, tenant_row, actor):
    _write(db, tenant_row, actor, "organisation", "legal_name", "Acme Ltd")
    assert service.organisation_profile(db, tenant_row.id)["legal_name"] == "Acme Ltd"


# ── JSON API ────────────────────────────────────────────────────────────────


@pytest.fixture()
def app_client(db, tenant_row, admin) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(api_router)

    @app.middleware("http")
    async def _inject_tenant(request: Request, call_next):
        request.state.tenant = tenant_row
        return await call_next(request)

    def _override() -> Generator[Session, None, None]:
        yield db

    app.dependency_overrides[get_db] = _override
    app.dependency_overrides[require_user_auth] = lambda: admin
    return TestClient(app)


def test_api_put_requires_a_reason_and_audits(app_client, db, tenant_row):
    bad = app_client.put("/settings/display/timezone", json={"value": "Asia/Tokyo"})
    assert bad.status_code == 422
    ok = app_client.put(
        "/settings/display/timezone",
        json={"value": "Asia/Tokyo", "reason": "move office"},
    )
    assert ok.status_code == 200 and ok.json()["setting"]["source"] == "tenant"
    assert [e.action for e in _audit(db, tenant_row)] == [
        "settings_admin.setting.update"
    ]


def test_api_undeclared_key_is_404_and_invalid_is_400(app_client):
    assert (
        app_client.put(
            "/settings/display/nope", json={"value": 1, "reason": "r"}
        ).status_code
        == 404
    )
    assert (
        app_client.put(
            "/settings/nope/nope", json={"value": 1, "reason": "r"}
        ).status_code
        == 404
    )
    assert app_client.get("/settings/nope").status_code == 404
    bad = app_client.put(
        "/settings/display/timezone", json={"value": "Mars/Olympus", "reason": "r"}
    )
    assert bad.status_code == 400


def test_api_secret_never_returns_a_value(app_client, secret_spec):
    put = app_client.put(
        "/settings/auth/test_secret_token",
        json={"value": "sekret-value", "reason": "rotate"},
    )
    assert put.status_code == 200
    assert "sekret-value" not in put.text and put.json()["setting"]["value"] is None
    assert put.json()["setting"]["is_set"] is True
    listing = app_client.get("/settings/auth")
    assert "sekret-value" not in listing.text


def test_api_history_restore_export_import(app_client):
    app_client.put(
        "/settings/display/timezone", json={"value": "Europe/London", "reason": "a"}
    )
    app_client.put(
        "/settings/display/timezone", json={"value": "Asia/Tokyo", "reason": "b"}
    )
    hist = app_client.get("/settings/display/timezone/history").json()
    upd = next(h for h in hist if h["action"] == "update")
    restored = app_client.post(
        f"/settings/history/{upd['id']}/restore", json={"reason": "undo"}
    )
    assert (
        restored.status_code == 200
        and restored.json()["setting"]["value"] == "Europe/London"
    )
    doc = app_client.get("/settings/export").json()
    prev = app_client.post("/settings/import/preview", json={"document": doc}).json()
    assert prev["ok"] is True
    applied = app_client.post(
        "/settings/import",
        json={"document": doc, "digest": prev["digest"], "reason": "r"},
    )
    assert applied.status_code == 200


def test_api_non_admin_is_forbidden(app_client, db, tenant_row):
    outsider = _party(db, tenant_row, "outsider@acme.test", admin=False)
    app_client.app.dependency_overrides[require_user_auth] = lambda: outsider
    assert app_client.get("/settings/display").status_code == 403
    assert (
        app_client.put(
            "/settings/display/timezone", json={"value": "Asia/Tokyo", "reason": "r"}
        ).status_code
        == 403
    )


# ── Composition: ERP v2 AR prerequisite keys ────────────────────────────────

_AR = SettingDomain("ar_producer")


def _ar_terms(value: object) -> None:
    if not isinstance(value, dict):
        raise ValueError("Use a JSON object mapping a terms code to a number of days")


def _ar_specs() -> list[sr.SettingSpec]:
    """The seven REQUIRED keys of `erp_v2/ar/settings.py`, by name and shape."""
    text = lambda key: sr.SettingSpec(  # noqa: E731
        domain=_AR,
        key=key,
        value_type=SettingValueType.string,
        default=None,
        label=key,
        inherits=False,
    )
    js = lambda key, validator: sr.SettingSpec(  # noqa: E731
        domain=_AR,
        key=key,
        value_type=SettingValueType.json,
        default={},
        label=key,
        inherits=False,
        validator=validator,
    )
    return [
        text("ar.series.invoice"),
        text("ar.series.credit_note"),
        text("ar.series.receipt"),
        js("ar.payment_terms", _ar_terms),
        text("ar.default_payment_terms_code"),
        js("ar.seller_snapshot", lambda v: None),
        js("ar.payment_instructions", lambda v: None),
    ]


@pytest.fixture()
def erp_ar_composition():
    specs = _ar_specs()
    sr.register_specs(specs)
    erp_module = ModuleManifest(
        code="erp_v2_ar", version="1", setting_domains=("ar_producer",)
    )
    install_setting_domains(
        SettingDomainRegistry.from_manifests([*[settings_admin_module], erp_module])
    )
    yield specs
    for spec in specs:
        del sr._REGISTRY[(spec.domain, spec.key)]


def test_erp_v2_ar_prerequisite_keys_are_settable_with_no_per_module_code(
    app_client, db, tenant_row, erp_ar_composition
):
    values = {
        "ar.series.invoice": "INV",
        "ar.series.credit_note": "CN",
        "ar.series.receipt": "RCT",
        "ar.payment_terms": {"NET30": 30},
        "ar.default_payment_terms_code": "NET30",
        "ar.seller_snapshot": {
            "legal_name": "Acme Ltd",
            "address_line": "1 High St",
            "country_code": "NG",
        },
        "ar.payment_instructions": {"method_code": "bank", "bank_name": "Example Bank"},
    }
    assert len(values) == 7
    listed = {s["key"] for s in app_client.get("/settings/ar_producer").json()}
    assert set(values) <= listed
    for key, value in values.items():
        resp = app_client.put(
            f"/settings/ar_producer/{key}", json={"value": value, "reason": "ar setup"}
        )
        assert resp.status_code == 200, (key, resp.text)
        assert resp.json()["setting"]["source"] == "tenant"
    for key, value in values.items():
        got = sr.resolve_value(db, _AR, key, tenant_id=tenant_row.id)
        assert got == value
    # Its own validator still guards the write (no per-module code needed).
    notdict = app_client.put(
        "/settings/ar_producer/ar.payment_terms", json={"value": [1], "reason": "r"}
    )
    assert notdict.status_code == 400


# ── Web surface smoke (full guard/CSRF coverage is the admin route sweep) ───


def test_the_staff_surface_is_contract_two_and_stateless():
    from dotmac_settings_admin import module

    assert module.contract_version == 2 and module.web_surfaces
    assert not module.web_routers and not module.nav
    assert (
        not module.tables and module.short_code is None and not module.migration_prefix
    )
    assert module.setting_domains and {
        "settings_admin.read",
        "settings_admin.manage",
        "settings_admin.approve",
    } == {p.code for p in module.permissions}


def test_without_a_bound_audit_writer_every_write_refuses_and_stores_nothing(
    db, tenant_row, actor
):
    from dotmac_settings_admin import audit_seam

    audit_seam.configure_audit_writer(None)
    with pytest.raises(audit_seam.AuditWriterNotConfiguredError):
        _write(db, tenant_row, actor, "display", "timezone", "Asia/Tokyo")
    assert not db.scalars(select(DomainSettingHistory)).all()


# ── Architect review: secret history, generic errors, approve refusal ───────


def test_history_shows_no_values_and_is_not_restorable_for_a_secret_spec(
    db, tenant_row, actor, secret_spec
):
    """A row written before a spec became secret may hold plaintext; never show it."""
    from datetime import UTC, datetime

    from dotmac_kernel.settings_models import SettingChangeAction

    db.add(
        DomainSettingHistory(
            tenant_id=tenant_row.id,
            domain=SettingDomain.auth,
            key="test_secret_token",
            action=SettingChangeAction.update,
            value_before="old-plaintext-credential",
            value_after="new-plaintext-credential",
            secret_changed=False,  # the row claims it is not a secret
            changed_at=datetime.now(UTC),
        )
    )
    db.flush()
    (entry,) = service.list_history(db, tenant_row.id, "auth", "test_secret_token")
    assert entry.value_before is None and entry.value_after is None
    assert entry.restorable is False and entry.secret_changed is True
    with pytest.raises(BadRequestError, match="secret"):
        service.restore_setting(
            db,
            tenant_row.id,
            actor=actor,
            history_id=entry.id,
            reason="r",
            may_approve=True,
        )


def test_a_validation_failure_on_a_secret_never_echoes_the_input(
    db, tenant_row, actor, monkeypatch
):
    monkeypatch.setenv(sc.KEY_ENV_VAR, Fernet.generate_key().decode())

    def echoing(value: object) -> None:
        if value != "unset":  # the spec default itself must stay valid
            raise ValueError(f"bad value {value!r}")

    spec = sr.SettingSpec(
        domain=SettingDomain.auth,
        key="echo_secret",
        value_type=SettingValueType.string,
        default="unset",
        is_secret=True,
        validator=echoing,
    )
    sr.register_specs([spec])
    try:
        with pytest.raises(BadRequestError) as caught:
            _write(db, tenant_row, actor, "auth", "echo_secret", "typed-secret-text")
        assert "typed-secret-text" not in str(caught.value)
        assert "auth/echo_secret" in str(caught.value)
    finally:
        del sr._REGISTRY[(spec.domain, spec.key)]


def test_approve_refuses_a_spec_that_became_secret_after_staging(
    db, tenant_row, actor, monkeypatch
):
    from dotmac_kernel.exceptions import ForbiddenError

    _write(db, tenant_row, actor, "settings_admin", "maker_checker", True)
    maker = _party(db, tenant_row, "maker2@acme.test")
    out = _write(
        db,
        tenant_row,
        service.Actor(party_id=maker.id),
        "display",
        "timezone",
        "Asia/Tokyo",
        may_approve=False,
    )
    real = sr.get_spec(SettingDomain.display, "timezone")
    now_secret = sr.SettingSpec(
        domain=real.domain,
        key=real.key,
        value_type=real.value_type,
        default=real.default,
        is_secret=True,
    )
    monkeypatch.setitem(sr._REGISTRY, (real.domain, real.key), now_secret)
    with pytest.raises(ForbiddenError, match="now secret"):
        service.approve_proposal(
            db, tenant_row.id, actor=actor, proposal_id=out.proposal_id, reason="ok"
        )
    (listed,) = service.list_proposals(db, tenant_row.id)
    assert listed["value"] is None  # a secret spec never shows a staged value


def test_proposals_show_the_proposed_value_beside_the_current_one(
    db, tenant_row, actor
):
    _write(db, tenant_row, actor, "settings_admin", "maker_checker", True)
    _write(db, tenant_row, actor, "display", "timezone", "Europe/London")
    maker = _party(db, tenant_row, "maker3@acme.test")
    _write(
        db,
        tenant_row,
        service.Actor(party_id=maker.id),
        "display",
        "timezone",
        "Asia/Tokyo",
        may_approve=False,
    )
    (listed,) = service.list_proposals(db, tenant_row.id)
    assert listed["value"] == "Asia/Tokyo" and listed["current"] == "Europe/London"


def test_may_approve_has_no_default_on_any_public_write():
    import inspect

    for fn in (
        service.write_setting,
        service.clear_setting,
        service.restore_setting,
        transfer.apply_import,
    ):
        param = inspect.signature(fn).parameters["may_approve"]
        assert param.default is inspect.Parameter.empty, fn.__name__
        assert param.kind is inspect.Parameter.KEYWORD_ONLY


def test_pending_changes_never_inherits_and_the_manifest_carries_no_seed():
    spec = sr.get_spec(SettingDomain("settings_admin"), "pending_changes")
    assert spec.inherits is False
    assert settings_admin_module.seed is None
