# ruff: noqa: F811
"""CP-6 v2: the presentation-credential block (parser) and joins J1-J7.

One test per parse code and one per join. Synthetic keys only.
"""

from __future__ import annotations

import pathlib
import sys
from datetime import timedelta
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests.unit.test_lane3_offline_orchestrator import (
    T,
    World,
    b64url,
    canonical,
    digest,
    public_raw,
    ts,
    world,  # noqa: F401  (fixture)
)

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import lane3_cp6_export as cp6  # noqa: E402
import lane3_offline_orchestrator as orchestrator  # noqa: E402

OTHER_UUID = "11111111-2222-4333-8444-555555555555"


def block(world: World) -> dict[str, Any]:
    return world.export["control"]["presentation_credential"]


def parse_refused(world: World, code: str) -> None:
    with pytest.raises(cp6.Cp6ExportRefused) as caught:
        cp6.parse_cp6_export(canonical(world.export))
    assert str(caught.value) == f"cp6-export-{code}"


def join_refused(world: World, code: str, **overrides: Any) -> None:
    with pytest.raises(orchestrator.JoinRefused) as caught:
        world.run(**overrides)
    assert str(caught.value) == f"offline-join-presentation-credential-{code}"


# --- the parser: v2 only -----------------------------------------------------------


def test_the_schema_is_v2_and_the_block_parses_with_public_material_only(
    world: World,
) -> None:
    assert cp6.EXPORT_SCHEMA == "lane3.cp6-export.v2"
    export = cp6.parse_cp6_export(canonical(world.export))
    credential = export.presentation_credential
    assert credential.public_key == public_raw(world.pres_key)
    assert credential.status == "revoked"
    assert credential.retired_at is None
    assert credential.revoked_at is not None
    assert "private" not in " ".join(cp6.PresentationCredentialBlock.__slots__)


def test_a_v1_export_is_refused_on_the_schema_string(world: World) -> None:
    world.export["schema"] = "lane3.cp6-export.v1"
    parse_refused(world, "schema")


def test_a_v1_shape_under_the_v2_string_is_refused_on_the_closed_control(
    world: World,
) -> None:
    del world.export["control"]["presentation_credential"]
    parse_refused(world, "schema")


def test_an_extra_key_in_the_block_is_refused(world: World) -> None:
    block(world)["private_key_b64url"] = "AAAA"
    parse_refused(world, "schema")


def test_a_missing_key_in_the_block_is_refused(world: World) -> None:
    del block(world)["status"]
    parse_refused(world, "schema")


@pytest.mark.parametrize("key", ["AAAA", "=", "", None, 5])
def test_a_noncanonical_public_key_is_refused(world: World, key: Any) -> None:
    block(world)["public_key_b64url"] = key
    parse_refused(world, "key")


def test_a_stored_fingerprint_that_is_not_the_keys_is_refused(world: World) -> None:
    block(world)["public_key_fingerprint"] = digest(b"another key")
    parse_refused(world, "presentation-fingerprint")


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("status", "unknown"),
        ("status", "ACTIVE"),
        ("status", None),
        ("target_id", "9A8B7C6D-5E4F-4A3B-8C2D-1E0F9A8B7C6D"),
        ("target_id", "not-a-uuid"),
        ("key_id", ""),
        ("key_id", "has space"),
        ("algorithm", ""),
        ("purpose", ""),
        ("public_key_fingerprint", "sha256:short"),
        ("public_key_fingerprint", None),
    ],
)
def test_a_malformed_field_is_refused(world: World, name: str, value: Any) -> None:
    block(world)[name] = value
    parse_refused(world, "field")


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("activated_at", None),
        ("activated_at", "2026-11-19T09:00:00"),
        ("retired_at", "not-a-time"),
        ("revoked_at", 5),
    ],
)
def test_a_malformed_or_null_time_is_refused(
    world: World, name: str, value: Any
) -> None:
    block(world)[name] = value
    parse_refused(world, "time")


def test_a_null_revocation_parses_and_is_refused_by_the_join_not_the_parser(
    world: World,
) -> None:
    block(world)["revoked_at"] = None
    cp6.parse_cp6_export(canonical(world.export))
    join_refused(world, "not-revoked")


# --- the joins ------------------------------------------------------------------------


def test_a_credential_revoked_after_consumption_is_the_genuine_shape(
    world: World,
) -> None:
    """Active at the ledger instant, revoked afterwards: never 'ACTIVE now'."""
    assert block(world)["status"] == "revoked"
    world.run()


def test_j1_the_credential_is_for_the_signed_target(world: World) -> None:
    block(world)["target_id"] = OTHER_UUID
    join_refused(world, "target-mismatch")


def test_j2_the_purpose_is_the_presentation_purpose(world: World) -> None:
    block(world)["purpose"] = "dotmac.control.another-purpose.v1"
    join_refused(world, "purpose-mismatch")


def test_j3_the_algorithm_is_ed25519(world: World) -> None:
    block(world)["algorithm"] = "rsa"
    join_refused(world, "algorithm-mismatch")


def test_j4_the_key_is_the_callers_pinned_key_not_one_the_export_supplies(
    world: World,
) -> None:
    other = public_raw(Ed25519PrivateKey.generate())
    block(world)["public_key_b64url"] = b64url(other)
    block(world)["public_key_fingerprint"] = digest(other)
    join_refused(world, "key-mismatch")


def test_j5_the_key_id_is_the_signed_presentations(world: World) -> None:
    block(world)["key_id"] = "presentation-key-2"
    join_refused(world, "key-id-mismatch")


def _ledger_instant(world: World):
    return T + timedelta(minutes=5, microseconds=123456)


def test_j6_activated_after_the_consumption_is_refused(world: World) -> None:
    block(world)["activated_at"] = ts(_ledger_instant(world) + timedelta(seconds=1))
    join_refused(world, "not-active-at-consumption")


def test_j6_retired_at_or_before_the_consumption_is_refused(world: World) -> None:
    block(world)["retired_at"] = ts(_ledger_instant(world))
    join_refused(world, "not-active-at-consumption")


def test_j6_a_rotated_out_key_is_refused_even_if_revocation_is_later(
    world: World,
) -> None:
    block(world)["retired_at"] = ts(_ledger_instant(world) - timedelta(minutes=1))
    join_refused(world, "not-active-at-consumption")


def test_j6_revoked_at_or_before_the_consumption_is_refused(world: World) -> None:
    block(world)["revoked_at"] = ts(_ledger_instant(world))
    join_refused(world, "not-active-at-consumption")


def test_j6_activation_at_the_consumption_instant_is_accepted(world: World) -> None:
    block(world)["activated_at"] = ts(_ledger_instant(world))
    world.run()


def test_j7_a_credential_never_revoked_is_refused(world: World) -> None:
    block(world)["revoked_at"] = None
    block(world)["status"] = "active"
    join_refused(world, "not-revoked")


@pytest.mark.parametrize("status", ["active", "retired", "pending"])
def test_j7_the_status_must_say_revoked_even_with_a_revocation_time(
    world: World,
    status: str,
) -> None:
    block(world)["status"] = status
    join_refused(world, "not-revoked")


def test_j7_is_not_behind_a_switch() -> None:
    import inspect

    source = inspect.getsource(orchestrator._join_presentation_credential)
    assert "not-revoked" in source
    parameters = inspect.signature(orchestrator.verify_offline).parameters
    assert not [name for name in parameters if "revoc" in name or "credential" in name]


# --- the negative target passes through the same join ------------------------


@pytest.mark.parametrize(
    ("name", "value", "code"),
    [
        ("target_id", OTHER_UUID, "target-mismatch"),
        ("purpose", "dotmac.control.another-purpose.v1", "purpose-mismatch"),
        ("algorithm", "rsa", "algorithm-mismatch"),
        ("key_id", "presentation-key-2", "key-id-mismatch"),
        ("revoked_at", None, "not-revoked"),
    ],
)
def test_the_negative_targets_own_export_is_joined_too(
    world: World,
    name: str,
    value: Any,
    code: str,
) -> None:
    world.neg.export["control"]["presentation_credential"][name] = value
    join_refused(world, code)


def test_the_gate_2_anchor_stays_mandatory(world: World) -> None:
    values = world.verify_values()
    del values["anchor"]
    with pytest.raises(TypeError):
        orchestrator.verify_offline(**values)
