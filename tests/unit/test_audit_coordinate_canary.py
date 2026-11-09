"""The audit coordinate canary: positions never enter the trail, at any depth.

The refusal is a typed error raised BEFORE anything is added to the session, so
a rejected write leaves nothing behind in the caller's transaction. The error
names key paths and never values.
"""

from __future__ import annotations

import pytest
from dotmac_kernel import (
    AuditActionRegistry,
    FeatureManifest,
    install_audit_actions,
    write_audit_event,
    write_platform_audit_event,
)
from dotmac_kernel.audit import (
    AUDIT_DETAILS_MAX_DEPTH,
    AUDIT_FORBIDDEN_DETAIL_KEYS,
    AuditDetailsCoordinateError,
    AuditEvent,
    refuse_coordinate_details,
)
from dotmac_kernel.models import Party, Tenant
from dotmac_kernel.models_platform import PlatformAuditEvent
from sqlalchemy import func, select
from sqlalchemy.orm import Session

_SENTINEL = "SENTINEL-6.5244-3.3792"


def test_the_documented_key_list_is_exactly_the_agreed_eight() -> None:
    assert AUDIT_FORBIDDEN_DETAIL_KEYS == frozenset(
        {"lat", "lng", "lon", "latitude", "longitude", "coordinates", "geo", "point"}
    )


@pytest.mark.parametrize("key", sorted(AUDIT_FORBIDDEN_DETAIL_KEYS))
def test_every_listed_key_is_refused_at_top_level(key: str) -> None:
    with pytest.raises(AuditDetailsCoordinateError) as exc:
        refuse_coordinate_details({key: _SENTINEL})
    assert exc.value.key_paths == (f"details.{key}",)


@pytest.mark.parametrize("key", ["LAT", "Latitude", "LnG", "COORDINATES", "Geo"])
def test_matching_is_case_insensitive(key: str) -> None:
    with pytest.raises(AuditDetailsCoordinateError):
        refuse_coordinate_details({key: 1})


@pytest.mark.parametrize(
    "details, path",
    [
        ({"a": {"b": {"lat": 1}}}, "details.a.b.lat"),
        ({"a": [{"ok": 1}, {"longitude": 2}]}, "details.a.1.longitude"),
        ({"a": ({"point": 1},)}, "details.a.0.point"),
        ({"a": [[{"geo": 1}]]}, "details.a.0.0.geo"),
        ({"outer": {"coordinates": [1, 2]}}, "details.outer.coordinates"),
    ],
)
def test_nested_keys_are_refused_with_their_path(
    details: dict[str, object], path: str
) -> None:
    with pytest.raises(AuditDetailsCoordinateError) as exc:
        refuse_coordinate_details(details)
    assert exc.value.key_paths == (path,)


def test_every_offending_path_is_reported_at_once() -> None:
    with pytest.raises(AuditDetailsCoordinateError) as exc:
        refuse_coordinate_details({"lat": 1, "x": {"lng": 2}})
    assert set(exc.value.key_paths) == {"details.lat", "details.x.lng"}


def test_the_error_names_paths_and_never_values() -> None:
    with pytest.raises(AuditDetailsCoordinateError) as exc:
        refuse_coordinate_details({"x": {"latitude": _SENTINEL}})
    assert _SENTINEL not in str(exc.value)
    assert _SENTINEL not in repr(exc.value)
    assert "details.x.latitude" in str(exc.value)


def test_error_is_a_value_error_subclass() -> None:
    assert issubclass(AuditDetailsCoordinateError, ValueError)


@pytest.mark.parametrize(
    "details",
    [
        None,
        {},
        {"latency_ms": 4, "geometry_version": 2, "pointer": "x", "longest": 1},
        {"a": [{"b": "lat"}], "note": "latitude 6.5"},  # values are not keys
        {"a": {"b": [1, 2, 3]}},
    ],
)
def test_clean_details_pass(details: dict[str, object] | None) -> None:
    refuse_coordinate_details(details)


def test_nesting_beyond_the_inspection_depth_is_refused_not_skipped() -> None:
    deep: dict[str, object] = {"leaf": 1}
    for _ in range(AUDIT_DETAILS_MAX_DEPTH + 2):
        deep = {"n": deep}
    with pytest.raises(AuditDetailsCoordinateError) as exc:
        refuse_coordinate_details(deep)
    assert "cannot inspect" in str(exc.value)


def test_a_self_referencing_structure_is_refused() -> None:
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic
    with pytest.raises(AuditDetailsCoordinateError):
        refuse_coordinate_details(cyclic)


def test_the_canary_does_not_mutate_its_input() -> None:
    details = {"a": [{"b": 1}]}
    refuse_coordinate_details(details)
    assert details == {"a": [{"b": 1}]}


# ── The writers refuse before anything reaches the session ─────────────────


def _install() -> None:
    install_audit_actions(
        AuditActionRegistry.from_manifests(
            [FeatureManifest(name="probe", audit_actions=("probe.ok",))]
        )
    )


def _count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(AuditEvent)) or 0


def _platform_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(PlatformAuditEvent)) or 0


def test_tenant_writer_refuses_nested_coordinates_and_writes_nothing(
    db: Session, tenant_row: Tenant, party_row: Party
) -> None:
    _install()
    before = _count(db)
    with pytest.raises(AuditDetailsCoordinateError):
        write_audit_event(
            db,
            tenant_id=tenant_row.id,
            actor_type="user",
            actor_id=str(party_row.id),
            action="probe.ok",
            entity_type="probe",
            details={"site": {"fix": [{"lat": 6.5, "lng": 3.3}]}},
        )
    assert _count(db) == before
    assert not db.new


def test_tenant_writer_still_writes_clean_details(
    db: Session, tenant_row: Tenant, party_row: Party
) -> None:
    _install()
    before = _count(db)
    write_audit_event(
        db,
        tenant_id=tenant_row.id,
        actor_type="user",
        actor_id=str(party_row.id),
        action="probe.ok",
        entity_type="probe",
        details={"site_ref": "site-1", "latency_ms": 12},
    )
    assert _count(db) == before + 1


def test_platform_writer_refuses_coordinates_and_writes_nothing(db: Session) -> None:
    _install()
    before = _platform_count(db)
    with pytest.raises(AuditDetailsCoordinateError):
        write_platform_audit_event(
            db,
            actor_admin_id=None,
            action="probe.ok",
            entity_type="probe",
            details={"x": [{"point": [1, 2]}]},
        )
    assert _platform_count(db) == before
    assert not db.new
