"""Validated operator settings over the kernel ``domain_settings`` store.

Extracted from Academy's ``app/services/settings_store.py`` (see
EXTRACTION.toml). The kernel owns storage; this package owns the policy
layer every consumer otherwise rewrites:

- a declared key registry (unknown keys refused on write, ignored on
  read) with per-key defaults and validators;
- strict reads: ``effective(require_store=True)`` never authorizes
  from defaults when the store cannot be read;
- stored NULLs are malformed values, retained only for keys declared
  ``retain_null``;
- whole-object override keys expand onto member keys, refuse split
  member rows, and write/delete under compare-and-swap (exactly one
  concurrent writer wins, the loser is refused before writing);
- canonical statements address ``public.domain_settings`` with literal
  schema-qualified SQL, immune to caller search_path and schema
  translation, with ORM autoflush semantics preserved for Sessions.

The package owns no tables and no product vocabulary: keys, defaults,
validators, relation rules and any environment overlay are injected by
the consuming assembly.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

__all__ = [
    "KeySpec",
    "OverrideSpec",
    "SettingsStore",
    "WholeObjectConflict",
]


class WholeObjectConflict(RuntimeError):
    """A concurrent whole-object write won; this one lost.

    Raised before any write of the losing call, with the caller's
    transaction left alive (no hidden abort, commit, or retry).
    """


@dataclass(frozen=True)
class KeySpec:
    """One declared setting key.

    ``default`` is the environment/product default used when no row is
    stored (a zero-argument callable is invoked per read). ``validate``
    coerces a raw stored or provided value to its typed form and raises
    ``ValueError`` for malformed values. ``retain_null`` keeps a stored
    SQL NULL visible as ``None`` instead of treating it as absent.
    """

    default: Any = None
    validate: Callable[[Any], Any] | None = None
    retain_null: bool = False


@dataclass(frozen=True)
class OverrideSpec:
    """A whole-object override key expanding onto member keys.

    ``members`` names every setting key the object may expand onto;
    any of them stored as its own row is refused as split state, even
    when no override object is stored. ``validate`` turns the
    stored/provided object into the member updates it implies
    (raising ``ValueError`` when malformed).
    """

    members: tuple[str, ...]
    validate: Callable[[Any], Mapping[str, Any]]


_SELECT = (
    "SELECT key, value_text, value_json FROM public.domain_settings "
    "WHERE domain = :domain AND scope_kind = :scope_kind AND tenant_id IS NULL"
)
_ABSENT = object()


def _flush_if_autoflush(db: Session | Connection) -> None:
    """Text statements skip ORM autoflush; preserve pending semantics."""
    if isinstance(db, Session) and db.autoflush:
        db.flush()


def _as_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


class SettingsStore:
    """Operator settings for one kernel settings domain (platform scope)."""

    def __init__(
        self,
        *,
        domain: str,
        keys: Mapping[str, KeySpec],
        overrides: Mapping[str, OverrideSpec] | None = None,
        validate_relations: Callable[[Mapping[str, Any]], None] | None = None,
        env_overlay: Callable[[dict[str, Any]], Mapping[str, Any]] | None = None,
    ) -> None:
        self.domain = domain
        self.keys = dict(keys)
        self.overrides = dict(overrides or {})
        self._validate_relations_hook = validate_relations
        self._env_overlay = env_overlay
        undeclared = sorted(set(self.overrides) - set(self.keys))
        if undeclared:
            raise ValueError(f"override keys must be declared setting keys: {', '.join(undeclared)}")

    # -- internals -----------------------------------------------------

    def _defaults(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, spec in self.keys.items():
            out[key] = spec.default() if callable(spec.default) else spec.default
        return out

    def _coerce(self, key: str, raw: Any) -> Any:
        validate = self.keys[key].validate
        return validate(raw) if validate is not None else raw

    def _expand_overrides(self, merged: dict[str, Any], stored_keys: Iterable[str] = ()) -> None:
        stored = set(stored_keys)
        for override_key, spec in self.overrides.items():
            split = sorted(stored & set(spec.members))
            if split:
                raise ValueError(
                    f"split override member rows are refused ({', '.join(split)}); "
                    f"use the whole-object key {override_key}"
                )
            if merged.get(override_key) is not None:
                merged.update(spec.validate(merged[override_key]))

    def _validate_relations(self, merged: Mapping[str, Any]) -> None:
        if self._validate_relations_hook is not None:
            self._validate_relations_hook(merged)

    def _proposal(self, db: Session, key: str) -> Any:
        return self.get_all(db).get(key, _ABSENT)

    def _cas_guard(self, db: Session, key: str, proposal: Any) -> None:
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))").bindparams(
                lock_key=f"dotmac-settings:{self.domain}:{key}"
            )
        )
        db.expire_all()
        current = self.get_all(db).get(key, _ABSENT)
        if current is _ABSENT or proposal is _ABSENT:
            if current is not proposal:
                raise WholeObjectConflict(f"setting {key} changed concurrently")
            return
        if json.dumps(current, sort_keys=True, default=str) != json.dumps(
            proposal, sort_keys=True, default=str
        ):
            raise WholeObjectConflict(f"setting {key} changed concurrently")

    # -- reads ----------------------------------------------------------

    def get_all(self, db: Session | Connection) -> dict[str, Any]:
        """Stored platform rows as ``{key: raw value}``.

        Stored NULLs surface only for ``retain_null`` keys; every other
        NULL is omitted here and refused as malformed by ``effective``.
        """
        _flush_if_autoflush(db)
        rows = db.execute(text(_SELECT).bindparams(domain=self.domain, scope_kind="platform")).all()
        result: dict[str, Any] = {}
        for row in rows:
            raw_json = getattr(row, "value_json", None)
            value_json = json.loads(raw_json) if isinstance(raw_json, str) else raw_json
            value_text = getattr(row, "value_text", getattr(row, "value", None))
            value = value_json if value_json is not None else value_text
            spec = self.keys.get(row.key)
            if value is None and not (spec is not None and spec.retain_null):
                continue
            result[row.key] = value
        return result

    def effective(
        self, db: Session | Connection | None = None, *, require_store: bool = False
    ) -> dict[str, Any]:
        """Effective settings: defaults overlaid by validated stored rows.

        With ``require_store`` a missing session is a ``RuntimeError``
        and read failures propagate instead of authorizing from
        defaults. Override expansion and relations run over the merged
        result; the injected environment overlay applies last.
        """
        merged = self._defaults()
        if db is not None:
            stored = self.get_all(db)
            for key, raw in stored.items():
                if key in merged:
                    merged[key] = self._coerce(key, raw)
            self._expand_overrides(merged, stored.keys())
        elif require_store:
            raise RuntimeError("settings store session is required")
        else:
            self._expand_overrides(merged)
        self._validate_relations(merged)
        if self._env_overlay is not None:
            merged = dict(self._env_overlay(merged))
        return merged

    # -- writes ----------------------------------------------------------

    def set_many(self, db: Session, values: Mapping[str, Any]) -> None:
        """Validate, relation-check, then upsert. Flushes; never commits."""
        unknown = sorted(set(values) - set(self.keys))
        if unknown:
            raise ValueError(f"unknown setting keys: {', '.join(unknown)}")
        for key in values:
            if key in self.overrides:
                self._cas_guard(db, key, self._proposal(db, key))
        coerced = {key: self._coerce(key, raw) for key, raw in values.items()}
        candidate = self._defaults()
        candidate.update(coerced)
        self._expand_overrides(candidate, self.get_all(db).keys())
        self._validate_relations(candidate)
        _flush_if_autoflush(db)
        for key, value in coerced.items():
            json_value = list(value) if isinstance(value, tuple) else (
                value if isinstance(value, (dict, list)) else None
            )
            text_value = None if json_value is not None else _as_text(value)
            row_id = db.execute(
                text(
                    "SELECT id FROM public.domain_settings WHERE domain = :domain "
                    "AND scope_kind = :scope_kind AND tenant_id IS NULL AND key = :key"
                ).bindparams(domain=self.domain, scope_kind="platform", key=key)
            ).scalar_one_or_none()
            json_text = None if json_value is None else json.dumps(json_value)
            if row_id is None:
                db.execute(
                    text(
                        "INSERT INTO public.domain_settings "
                        "(id, tenant_id, scope_kind, scope_id, domain, key, value_type, value_text, "
                        "value_json, is_secret, is_active) VALUES "
                        "(:id, NULL, 'platform', NULL, :domain, :key, 'string', :value_text, "
                        "CAST(:value_json AS json), false, true)"
                    ).bindparams(
                        id=uuid4(), domain=self.domain, key=key, value_text=text_value, value_json=json_text
                    )
                )
            else:
                db.execute(
                    text(
                        "UPDATE public.domain_settings SET value_text = :value_text, "
                        "value_json = CAST(:value_json AS json), updated_at = now() WHERE id = :id"
                    ).bindparams(id=row_id, value_text=text_value, value_json=json_text)
                )
        db.flush()

    def delete(self, db: Session, key: str) -> None:
        """Delete a stored key if present. Flushes; never commits.

        Refuses when removing the key would leave relations invalid.
        """
        _flush_if_autoflush(db)
        existing = {
            row_key: row_id
            for row_id, row_key in db.execute(
                text(
                    "SELECT id, key FROM public.domain_settings WHERE domain = :domain "
                    "AND scope_kind = :scope_kind AND tenant_id IS NULL"
                ).bindparams(domain=self.domain, scope_kind="platform")
            )
        }
        if key in self.overrides:
            self._cas_guard(db, key, self._proposal(db, key))
        row = existing.get(key)
        if row is None:
            return
        candidate = self._defaults()
        for other, raw in self.get_all(db).items():
            if other != key and other in candidate:
                candidate[other] = self._coerce(other, raw)
        if key in self.overrides:
            candidate.pop(key, None)
        else:
            self._expand_overrides(candidate, self.get_all(db).keys())
        self._validate_relations(candidate)
        db.execute(text("DELETE FROM public.domain_settings WHERE id = :id").bindparams(id=row))
        db.flush()
