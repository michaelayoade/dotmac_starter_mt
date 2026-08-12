"""The audit actor contract: the pair is canonical, the party is enrichment.

Measured across ERP and Sub production, 93-98% of audit rows have a non-party
actor, so these tests exist to keep a party-primary model from creeping back in
— and to keep the one temporary compatibility rule from becoming permanent by
accident.
"""

import uuid

import pytest
from dotmac_kernel.audit import (
    ACTOR_TYPES,
    MissingAuditActorError,
    UnknownAuditActorTypeError,
    resolve_audit_actor,
)


def _resolve(**kwargs):
    return resolve_audit_actor(
        **{"actor_type": None, "actor_id": None, "actor_party_id": None, **kwargs}
    )


def test_the_contract_defines_exactly_four_actor_kinds() -> None:
    assert ACTOR_TYPES == frozenset({"system", "user", "api_key", "service"})


@pytest.mark.parametrize("kind", sorted(ACTOR_TYPES))
def test_every_declared_kind_is_accepted_with_its_identifier(kind: str) -> None:
    assert _resolve(actor_type=kind, actor_id="worker-7") == (kind, "worker-7")


@pytest.mark.parametrize("kind", sorted(ACTOR_TYPES))
def test_a_kind_may_carry_no_identifier(kind: str) -> None:
    """`system` in particular often has nothing more specific to record."""
    assert _resolve(actor_type=kind) == (kind, None)


def test_an_undeclared_kind_is_refused() -> None:
    with pytest.raises(UnknownAuditActorTypeError) as exc:
        _resolve(actor_type="robot")
    assert "robot" in str(exc.value)


def test_a_party_alone_derives_the_legacy_user_actor() -> None:
    """The temporary shim for released dotmac-template-studio.

    It calls `write_audit_event` with only `actor_party_id`, at nine call sites,
    and a released artifact cannot be edited retroactively.
    """
    party = uuid.uuid4()

    assert _resolve(actor_party_id=party) == ("user", str(party))


def test_an_explicit_identifier_survives_the_legacy_derivation() -> None:
    """The shim supplies a fallback identifier; it does not overwrite one."""
    party = uuid.uuid4()

    assert _resolve(actor_party_id=party, actor_id="alice") == ("user", "alice")


def test_an_explicit_kind_wins_over_the_party_derivation() -> None:
    """A party may accompany an api_key actor; it must not relabel it `user`."""
    party = uuid.uuid4()

    kind, actor_id = _resolve(
        actor_type="api_key", actor_id="key-42", actor_party_id=party
    )

    assert (kind, actor_id) == ("api_key", "key-42")


def test_supplying_neither_fails_rather_than_defaulting_to_system() -> None:
    """The rule that keeps the trail honest.

    Defaulting a missing actor to `system` would be indistinguishable, forever,
    from a genuine system action — so a caller defect must not be recordable.
    """
    with pytest.raises(MissingAuditActorError) as exc:
        _resolve()

    message = str(exc.value)
    assert "system" in message, "the error must say why it will not default"
    assert "actor_type" in message and "actor_party_id" in message


def test_system_is_never_synthesised_from_an_empty_identifier() -> None:
    """An empty string is still not an actor."""
    with pytest.raises(MissingAuditActorError):
        _resolve(actor_id="")
