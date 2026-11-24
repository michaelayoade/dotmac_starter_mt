"""Settings administration: one generic, stateless write path.

Everything an operator can do to a setting goes through `write_setting` (set),
`restore_setting` (a NEW write that re-applies a recorded earlier value),
`apply_import` (many writes) or `approve_proposal` (a deferred write). Each one:

- refuses an undeclared domain or key (the registries are the authority, never
  the request);
- requires an actor and a non-blank reason, and records both on the setting's
  history row (`SettingChangeContext`) and on an audit event;
- validates through the kernel's `validate_spec_value` before anything is stored;
- NEVER puts a value in an audit event, a log line, an export or a history row
  when the spec is secret (the kernel's history writer redacts; this module only
  ever reports a secret as set or unset).

The module is STATELESS: it owns no table. Its data is the kernel's
`domain_settings` / `domain_setting_history` rows and audit events. The optional
maker-checker (default OFF) keeps its pending proposals in one declared JSON
setting (`settings_admin/pending_changes`) so that, too, needs no schema.

Nothing here commits: the caller owns the transaction (a request-scoped session).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from dotmac_kernel.branding import reject_retired_brand_keys
from dotmac_kernel.exceptions import BadRequestError, ForbiddenError, NotFoundError
from dotmac_kernel.setting_domains import (
    UndeclaredSettingDomainError,
    active_setting_domains,
)
from dotmac_kernel.setting_scopes import SettingScope
from dotmac_kernel.setting_value_types import active_setting_value_types
from dotmac_kernel.settings_admin import (
    all_specs,
    get_spec,
    resolve_with_source,
    upsert_by_key,
    validate_spec_value,
)
from dotmac_kernel.settings_models import (
    DomainSettingHistory,
    SettingChangeAction,
    SettingDomain,
)
from dotmac_kernel.settings_resolver import (
    SettingChangeContext,
    SettingSpec,
    clear_by_key,
    stored_at,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_settings_admin.audit_seam import require_audit_writer
from dotmac_settings_admin.specs import ADMIN_DOMAIN, INTERNAL_KEYS

MAX_REASON_LENGTH = 500
AUDIT_UPDATE = "settings_admin.setting.update"
AUDIT_CLEAR = "settings_admin.setting.clear"
AUDIT_RESTORE = "settings_admin.setting.restore"
AUDIT_IMPORT = "settings_admin.import.apply"
AUDIT_PROPOSE = "settings_admin.proposal.create"
AUDIT_APPROVE = "settings_admin.proposal.approve"
AUDIT_REJECT = "settings_admin.proposal.reject"
AUDIT_ACTIONS = (
    AUDIT_UPDATE,
    AUDIT_CLEAR,
    AUDIT_RESTORE,
    AUDIT_IMPORT,
    AUDIT_PROPOSE,
    AUDIT_APPROVE,
    AUDIT_REJECT,
)

#: Fixed display mask used where a UI needs a placeholder; never a credential.
SET_LABEL = "set"
UNSET_LABEL = "unset"


@dataclass(frozen=True, slots=True)
class Actor:
    """Who is acting, and on which request. `party_id` is mandatory."""

    party_id: UUID
    ip_address: str | None = None
    user_agent: str | None = None
    request_id: str | None = None

    def change_context(self, reason: str) -> SettingChangeContext:
        return SettingChangeContext(
            actor_party_id=self.party_id,
            reason=reason,
            ip_address=self.ip_address,
            user_agent=(self.user_agent or None) and self.user_agent[:500],
            request_id=self.request_id,
        )


@dataclass(frozen=True, slots=True)
class SettingView:
    """One declared setting as an editor shows it. A secret has NO value here."""

    domain: str
    key: str
    value: object | None
    value_type: str
    label: str | None
    description: str | None
    is_secret: bool
    is_set: bool
    source: str
    has_override: bool
    owner: str | None


@dataclass(frozen=True, slots=True)
class DomainSummary:
    domain: str
    owner: str | None
    count: int


@dataclass(frozen=True, slots=True)
class WriteOutcome:
    """`applied` is False only when maker-checker turned the write into a proposal."""

    setting: SettingView
    applied: bool
    proposal_id: str | None = None


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    id: UUID
    action: str
    changed_at: datetime
    changed_by_party_id: UUID | None
    reason: str | None
    value_before: str | None
    value_after: str | None
    secret_changed: bool
    restorable: bool


# ── Validation helpers ──────────────────────────────────────────────────────


def clean_reason(reason: object) -> str:
    text = str(reason or "").strip()
    if not text:
        raise BadRequestError("A reason is required for every settings change.")
    if len(text) > MAX_REASON_LENGTH:
        raise BadRequestError(
            f"The reason must be at most {MAX_REASON_LENGTH} characters."
        )
    return text


def _require_actor(actor: Actor | None) -> Actor:
    if actor is None or actor.party_id is None:
        raise BadRequestError("An actor is required for every settings change.")
    require_audit_writer()  # fail closed before anything is stored
    return actor


def declared_domain(domain: str) -> SettingDomain:
    """The registered domain, or 404. The registry makes a string a real domain."""
    try:
        return active_setting_domains().require(domain)
    except UndeclaredSettingDomainError:
        raise NotFoundError(f"Unknown settings domain: {domain}") from None


def declared_spec(domain: str, key: str) -> SettingSpec[Any]:
    """The declared spec for an editable setting; undeclared/internal is refused."""
    domain_enum = declared_domain(domain)
    if (str(domain_enum), key) in INTERNAL_KEYS:
        raise NotFoundError(f"Unknown setting: {domain}/{key}")
    try:
        return get_spec(domain_enum, key)
    except KeyError:
        raise NotFoundError(f"Unknown setting: {domain}/{key}") from None


def _guard_value(spec: SettingSpec[Any], value: object) -> object:
    """Write-time rules that are not part of the spec's read-path validator."""
    # `==`, never `is`: SettingDomain is an open str subclass (ADR-0008).
    if spec.domain == SettingDomain.branding and spec.key == "ui_branding":
        reject_retired_brand_keys(value)
    return validate_spec_value(spec, value)


# ── Reading ─────────────────────────────────────────────────────────────────


def _view(db: Session, tenant_id: UUID, spec: SettingSpec[Any]) -> SettingView:
    value, source = resolve_with_source(db, spec.domain, spec.key, tenant_id=tenant_id)
    override = stored_at(
        db, spec.domain, spec.key, scope=SettingScope.tenant(tenant_id)
    )
    return SettingView(
        domain=str(spec.domain),
        key=spec.key,
        value=None if spec.is_secret else value,
        value_type=str(spec.value_type),
        label=spec.label,
        description=spec.description,
        is_secret=spec.is_secret,
        is_set=(source != "default") if spec.is_secret else True,
        source=source,
        has_override=override is not None,
        owner=active_setting_domains().owner(str(spec.domain)),
    )


def _editable_specs(domain: str | None = None) -> list[SettingSpec[Any]]:
    specs = [
        s
        for s in all_specs()
        if (str(s.domain), s.key) not in INTERNAL_KEYS
        and active_setting_domains().is_declared(str(s.domain))
    ]
    if domain is not None:
        specs = [s for s in specs if str(s.domain) == str(domain)]
    return sorted(specs, key=lambda s: (str(s.domain), s.key))


def list_domains(db: Session) -> list[DomainSummary]:
    """Every declared domain that has editable specs, with its owning module."""
    counts: dict[str, int] = {}
    for spec in _editable_specs():
        counts[str(spec.domain)] = counts.get(str(spec.domain), 0) + 1
    registry = active_setting_domains()
    return sorted(
        (DomainSummary(d, registry.owner(d), n) for d, n in counts.items()),
        key=lambda s: (s.owner or "", s.domain),
    )


def list_settings(db: Session, tenant_id: UUID, domain: str) -> list[SettingView]:
    declared_domain(domain)
    return [_view(db, tenant_id, s) for s in _editable_specs(domain)]


def get_setting(db: Session, tenant_id: UUID, domain: str, key: str) -> SettingView:
    return _view(db, tenant_id, declared_spec(domain, key))


# ── Audit ───────────────────────────────────────────────────────────────────


def _audit(
    db: Session,
    tenant_id: UUID,
    actor: Actor,
    *,
    action: str,
    entity_id: str,
    reason: str,
    details: dict[str, object],
) -> None:
    # The value is NEVER in `details`: the history row holds it (redacted for a
    # secret), and an audit trail must not become a second copy of a credential.
    require_audit_writer()(
        db,
        tenant_id=tenant_id,
        actor_party_id=actor.party_id,
        actor_type="user",
        actor_id=str(actor.party_id),
        action=action,
        entity_type="setting",
        entity_id=entity_id,
        request_id=actor.request_id,
        ip_address=actor.ip_address,
        user_agent=actor.user_agent,
        details={**details, "reason": reason},
    )


# ── Organisation profile ────────────────────────────────────────────────────

_ORGANISATION_KEYS = (
    "legal_name",
    "trading_name",
    "registered_address",
    "country_code",
    "contact_email",
    "contact_phone",
)


def organisation_profile(db: Session, tenant_id: UUID) -> dict[str, str]:
    """The tenant's organisation profile, for any caller (documents, e-mail footers)."""
    return {
        key: str(
            resolve_with_source(
                db, SettingDomain("organisation"), key, tenant_id=tenant_id
            )[0]
            or ""
        )
        for key in _ORGANISATION_KEYS
    }


# ── Maker-checker (optional, default OFF) ───────────────────────────────────


def maker_checker_enabled(db: Session, tenant_id: UUID) -> bool:
    value, _ = resolve_with_source(
        db, ADMIN_DOMAIN, "maker_checker", tenant_id=tenant_id
    )
    return value is True


def _pending(db: Session, tenant_id: UUID) -> dict[str, dict[str, Any]]:
    value, _ = resolve_with_source(
        db, ADMIN_DOMAIN, "pending_changes", tenant_id=tenant_id
    )
    items = value.get("items", {}) if isinstance(value, dict) else {}
    return {str(k): dict(v) for k, v in items.items()}


def _store_pending(
    db: Session,
    tenant_id: UUID,
    items: dict[str, dict[str, Any]],
    actor: Actor,
    reason: str,
) -> None:
    upsert_by_key(
        db,
        ADMIN_DOMAIN,
        "pending_changes",
        {"items": items},
        tenant_id=tenant_id,
        changed_by=actor.change_context(reason),
    )


def list_proposals(db: Session, tenant_id: UUID) -> list[dict[str, Any]]:
    return sorted(
        ({"id": pid, **item} for pid, item in _pending(db, tenant_id).items()),
        key=lambda p: p.get("proposed_at", ""),
    )


# ── Writing ─────────────────────────────────────────────────────────────────


def _apply(
    db: Session,
    tenant_id: UUID,
    actor: Actor,
    spec: SettingSpec[Any],
    value: object,
    reason: str,
    *,
    action: str,
    extra: dict[str, object] | None = None,
) -> None:
    coerced = _guard_value(spec, value)
    upsert_by_key(
        db,
        spec.domain,
        spec.key,
        coerced,
        tenant_id=tenant_id,
        changed_by=actor.change_context(reason),
    )
    _audit(
        db,
        tenant_id,
        actor,
        action=action,
        entity_id=spec.key,
        reason=reason,
        details={
            "domain": str(spec.domain),
            "key": spec.key,
            "is_secret": spec.is_secret,
            **(extra or {}),
        },
    )


def write_setting(
    db: Session,
    tenant_id: UUID,
    *,
    actor: Actor | None,
    domain: str,
    key: str,
    value: object,
    reason: object,
    may_approve: bool = True,
) -> WriteOutcome:
    """Set one tenant override. Actor and reason are mandatory; audited in-transaction.

    `may_approve` says whether the actor holds the approve permission. It matters
    only when the tenant has turned the optional maker-checker ON: a writer
    without it can then only propose.
    """
    actor = _require_actor(actor)
    reason_text = clean_reason(reason)
    spec = declared_spec(domain, key)
    coerced = _guard_value(spec, value)  # refuse before anything is staged or stored

    if maker_checker_enabled(db, tenant_id) and not may_approve:
        if spec.is_secret:
            raise ForbiddenError(
                "Secret values cannot be proposed for approval; "
                "ask a person with the approve permission to set it."
            )
        proposal_id = uuid.uuid4().hex
        items = _pending(db, tenant_id)
        items[proposal_id] = {
            "domain": str(spec.domain),
            "key": spec.key,
            "value": coerced if not hasattr(coerced, "model_dump") else str(coerced),
            "reason": reason_text,
            "proposer": str(actor.party_id),
            "proposed_at": datetime.now(UTC).isoformat(),
        }
        _store_pending(db, tenant_id, items, actor, f"proposal {proposal_id}")
        _audit(
            db,
            tenant_id,
            actor,
            action=AUDIT_PROPOSE,
            entity_id=spec.key,
            reason=reason_text,
            details={
                "domain": str(spec.domain),
                "key": spec.key,
                "proposal_id": proposal_id,
            },
        )
        return WriteOutcome(
            _view(db, tenant_id, spec), applied=False, proposal_id=proposal_id
        )

    _apply(db, tenant_id, actor, spec, coerced, reason_text, action=AUDIT_UPDATE)
    return WriteOutcome(_view(db, tenant_id, spec), applied=True)


def clear_setting(
    db: Session,
    tenant_id: UUID,
    *,
    actor: Actor | None,
    domain: str,
    key: str,
    reason: object,
    may_approve: bool = True,
) -> SettingView:
    """Remove the tenant override so the setting inherits again (an audited write)."""
    actor = _require_actor(actor)
    reason_text = clean_reason(reason)
    spec = declared_spec(domain, key)
    if maker_checker_enabled(db, tenant_id) and not may_approve:
        raise ForbiddenError(
            "With maker-checker on, only an approver can clear an override."
        )
    clear_by_key(
        db,
        spec.domain,
        spec.key,
        tenant_id=tenant_id,
        changed_by=actor.change_context(reason_text),
    )
    _audit(
        db,
        tenant_id,
        actor,
        action=AUDIT_CLEAR,
        entity_id=spec.key,
        reason=reason_text,
        details={
            "domain": str(spec.domain),
            "key": spec.key,
            "is_secret": spec.is_secret,
        },
    )
    return _view(db, tenant_id, spec)


def approve_proposal(
    db: Session,
    tenant_id: UUID,
    *,
    actor: Actor | None,
    proposal_id: str,
    reason: object,
) -> SettingView:
    """Apply a staged proposal. The proposer can never approve their own."""
    actor = _require_actor(actor)
    reason_text = clean_reason(reason)
    items = _pending(db, tenant_id)
    item = items.get(proposal_id)
    if item is None:
        raise NotFoundError("Unknown proposal.")
    if item.get("proposer") == str(actor.party_id):
        raise ForbiddenError("A proposal must be approved by a different person.")
    spec = declared_spec(item["domain"], item["key"])
    del items[proposal_id]
    _apply(
        db,
        tenant_id,
        actor,
        spec,
        item["value"],
        f"{reason_text} (proposal {proposal_id})",
        action=AUDIT_APPROVE,
        extra={"proposal_id": proposal_id, "proposer": item.get("proposer")},
    )
    _store_pending(db, tenant_id, items, actor, f"approved {proposal_id}")
    return _view(db, tenant_id, spec)


def reject_proposal(
    db: Session,
    tenant_id: UUID,
    *,
    actor: Actor | None,
    proposal_id: str,
    reason: object,
) -> None:
    actor = _require_actor(actor)
    reason_text = clean_reason(reason)
    items = _pending(db, tenant_id)
    item = items.pop(proposal_id, None)
    if item is None:
        raise NotFoundError("Unknown proposal.")
    _store_pending(db, tenant_id, items, actor, f"rejected {proposal_id}")
    _audit(
        db,
        tenant_id,
        actor,
        action=AUDIT_REJECT,
        entity_id=item["key"],
        reason=reason_text,
        details={
            "domain": item["domain"],
            "key": item["key"],
            "proposal_id": proposal_id,
        },
    )


# ── History and restore ─────────────────────────────────────────────────────


def list_history(
    db: Session, tenant_id: UUID, domain: str, key: str, *, limit: int = 50
) -> list[HistoryEntry]:
    spec = declared_spec(domain, key)
    rows = db.scalars(
        select(DomainSettingHistory)
        .where(
            DomainSettingHistory.tenant_id == tenant_id,
            DomainSettingHistory.domain == str(spec.domain),
            DomainSettingHistory.key == spec.key,
        )
        .order_by(DomainSettingHistory.changed_at.desc(), DomainSettingHistory.id)
        .limit(max(1, min(limit, 200)))
    ).all()
    return [
        HistoryEntry(
            id=r.id,
            action=r.action.value if hasattr(r.action, "value") else str(r.action),
            changed_at=r.changed_at,
            changed_by_party_id=r.changed_by_party_id,
            reason=r.change_reason,
            value_before=r.value_before,
            value_after=r.value_after,
            secret_changed=r.secret_changed,
            # A secret's past value is never recorded, so it cannot be restored.
            restorable=not r.secret_changed,
        )
        for r in rows
    ]


def _typed_from_text(spec: SettingSpec[Any], text: str) -> object:
    """History holds text (JSON dumped); read it back by the type's storage."""
    if active_setting_value_types().require(spec.value_type).storage == "json":
        try:
            return json.loads(text)
        except ValueError as exc:
            raise BadRequestError(
                "The recorded value is not readable as JSON."
            ) from exc
    return text


def restore_setting(
    db: Session,
    tenant_id: UUID,
    *,
    actor: Actor | None,
    history_id: UUID,
    reason: object,
    may_approve: bool = True,
) -> WriteOutcome:
    """Undo one recorded change: a NEW audited write of the value that preceded it.

    History is append-only and is never edited. Restoring a `create` (nothing
    preceded it) clears the override, which is also a new audited write. A
    secret is never restorable: its past values are not recorded.
    """
    actor = _require_actor(actor)
    reason_text = clean_reason(reason)
    row = db.scalar(
        select(DomainSettingHistory).where(
            DomainSettingHistory.id == history_id,
            DomainSettingHistory.tenant_id == tenant_id,
        )
    )
    if row is None:
        raise NotFoundError("Unknown history entry.")
    spec = declared_spec(str(row.domain), row.key)
    if spec.is_secret or row.secret_changed:
        raise BadRequestError(
            "A secret value is never recorded, so it cannot be restored."
        )
    if maker_checker_enabled(db, tenant_id) and not may_approve:
        raise ForbiddenError(
            "With maker-checker on, only an approver can restore a value."
        )
    if row.value_before is None:
        if row.action != SettingChangeAction.create:
            raise BadRequestError(
                "This history entry holds no earlier value to restore."
            )
        clear_by_key(
            db,
            spec.domain,
            spec.key,
            tenant_id=tenant_id,
            changed_by=actor.change_context(reason_text),
        )
        _audit(
            db,
            tenant_id,
            actor,
            action=AUDIT_RESTORE,
            entity_id=spec.key,
            reason=reason_text,
            details={
                "domain": str(spec.domain),
                "key": spec.key,
                "restored_from": str(history_id),
                "result": "override_cleared",
            },
        )
    else:
        _apply(
            db,
            tenant_id,
            actor,
            spec,
            _typed_from_text(spec, row.value_before),
            reason_text,
            action=AUDIT_RESTORE,
            extra={"restored_from": str(history_id)},
        )
    return WriteOutcome(_view(db, tenant_id, spec), applied=True)
