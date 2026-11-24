"""JSON export and import (with preview) of tenant setting overrides.

- **Export** carries tenant OVERRIDES of declared, non-secret, non-internal
  settings. A secret-typed value is never exported, not even encrypted; its
  NAME is listed under `secrets_omitted` so an operator knows to re-enter it.
- **Import** is two steps. `plan_import` is a pure preview: every entry is
  `change`, `unchanged` or `refused` (undeclared, internal, secret, invalid).
  `apply_import` re-plans from the posted payload, refuses on any `refused`
  entry (all-or-nothing) and on drift (the plan digest covers the CURRENT
  values, so a setting that moved since the preview is a conflict), then
  applies each change as an ordinary audited write with the operator's reason.

Stateless: the preview is recomputed, never stored.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from dotmac_kernel.exceptions import BadRequestError, ConflictError, ForbiddenError
from dotmac_kernel.setting_scopes import SettingScope
from dotmac_kernel.settings_admin import resolve_with_source
from dotmac_kernel.settings_resolver import stored_at
from sqlalchemy.orm import Session

from dotmac_settings_admin import service
from dotmac_settings_admin.specs import INTERNAL_KEYS

EXPORT_FORMAT = "dotmac.settings-export.v1"
MAX_ENTRIES = 500


@dataclass(frozen=True, slots=True)
class PlanEntry:
    domain: str
    key: str
    status: str  # change | unchanged | refused
    detail: str
    current: object | None
    new: object | None


@dataclass(frozen=True, slots=True)
class ImportPlan:
    entries: tuple[PlanEntry, ...]
    digest: str

    @property
    def refused(self) -> tuple[PlanEntry, ...]:
        return tuple(e for e in self.entries if e.status == "refused")

    @property
    def changes(self) -> tuple[PlanEntry, ...]:
        return tuple(e for e in self.entries if e.status == "change")

    @property
    def ok(self) -> bool:
        return not self.refused


def _canon(value: object) -> str:
    return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))


def export_settings(db: Session, tenant_id: UUID) -> dict[str, Any]:
    scope = SettingScope.tenant(tenant_id)
    settings: list[dict[str, Any]] = []
    omitted: list[str] = []
    for spec in service._editable_specs():
        name = f"{spec.domain}/{spec.key}"
        if spec.is_secret:
            omitted.append(name)  # name only; never a value, never even set/unset
            continue
        stored = stored_at(db, spec.domain, spec.key, scope=scope)
        if stored is None or not stored.valid:
            continue
        value, _ = resolve_with_source(db, spec.domain, spec.key, tenant_id=tenant_id)
        settings.append({"domain": str(spec.domain), "key": spec.key, "value": value})
    return {
        "format": EXPORT_FORMAT,
        "settings": settings,
        "secrets_omitted": sorted(omitted),
    }


def parse_payload(raw: object) -> list[dict[str, Any]]:
    if isinstance(raw, str | bytes):
        try:
            raw = json.loads(raw)
        except ValueError as exc:
            raise BadRequestError("The import is not valid JSON.") from exc
    if not isinstance(raw, dict) or raw.get("format") != EXPORT_FORMAT:
        raise BadRequestError(f"The import must be a {EXPORT_FORMAT} document.")
    items = raw.get("settings")
    if not isinstance(items, list) or len(items) > MAX_ENTRIES:
        raise BadRequestError(
            f"`settings` must be a list of at most {MAX_ENTRIES} entries."
        )
    for item in items:
        if not isinstance(item, dict) or not {"domain", "key", "value"} <= set(item):
            raise BadRequestError("Every entry needs domain, key and value.")
    return items


def plan_import(db: Session, tenant_id: UUID, raw: object) -> ImportPlan:
    entries: list[PlanEntry] = []
    seen: set[tuple[str, str]] = set()
    for item in parse_payload(raw):
        domain, key, value = str(item["domain"]), str(item["key"]), item["value"]
        ident = (domain, key)
        if ident in seen:
            entries.append(
                PlanEntry(domain, key, "refused", "duplicate entry", None, None)
            )
            continue
        seen.add(ident)
        if ident in INTERNAL_KEYS:
            entries.append(
                PlanEntry(domain, key, "refused", "internal key", None, None)
            )
            continue
        try:
            spec = service.declared_spec(domain, key)
        except Exception:
            entries.append(
                PlanEntry(domain, key, "refused", "undeclared setting", None, None)
            )
            continue
        if spec.is_secret:
            entries.append(
                PlanEntry(
                    domain,
                    key,
                    "refused",
                    "secret values are never imported",
                    None,
                    None,
                )
            )
            continue
        try:
            coerced = service._guard_value(spec, value)
        except BadRequestError as exc:
            entries.append(PlanEntry(domain, key, "refused", str(exc), None, None))
            continue
        stored = stored_at(
            db, spec.domain, spec.key, scope=SettingScope.tenant(tenant_id)
        )
        current, _ = resolve_with_source(db, spec.domain, spec.key, tenant_id=tenant_id)
        has = stored is not None
        same = has and _canon(current) == _canon(coerced)
        entries.append(
            PlanEntry(
                domain,
                key,
                "unchanged" if same else "change",
                "",
                current if has else None,
                coerced,
            )
        )
    digest = hashlib.sha256(
        _canon(
            [[e.domain, e.key, e.status, e.current, e.new] for e in entries]
        ).encode()
    ).hexdigest()
    return ImportPlan(tuple(entries), digest)


def apply_import(
    db: Session,
    tenant_id: UUID,
    *,
    actor: service.Actor | None,
    raw: object,
    reason: object,
    expected_digest: str,
    may_approve: bool = True,
) -> int:
    """Apply a previewed import. Returns the number of settings changed."""
    actor = service._require_actor(actor)
    reason_text = service.clean_reason(reason)
    if service.maker_checker_enabled(db, tenant_id) and not may_approve:
        raise ForbiddenError(
            "With maker-checker on, only an approver can apply an import."
        )
    plan = plan_import(db, tenant_id, raw)
    if not plan.ok:
        raise BadRequestError(
            "The import has refused entries: "
            + ", ".join(f"{e.domain}/{e.key} ({e.detail})" for e in plan.refused)
        )
    if plan.digest != expected_digest:
        raise ConflictError(
            "The settings changed since the preview; review the import again."
        )
    for entry in plan.changes:
        spec = service.declared_spec(entry.domain, entry.key)
        service._apply(
            db,
            tenant_id,
            actor,
            spec,
            entry.new,
            reason_text,
            action=service.AUDIT_IMPORT,
            extra={"import_digest": plan.digest},
        )
    return len(plan.changes)
