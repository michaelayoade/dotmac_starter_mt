"""Machine credentials: every refusal the inventory demanded, proven.

The happy path is one test. The rest are the behaviours `dotmac_sub` and
`dotmac_erp` get wrong, asserted so the extraction cannot quietly reacquire
them (`docs/inventories/machine-credential-sources.md`).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from dotmac_kernel import (
    API_KEY_HEADER,
    MACHINE_KEY_SECRET_NAME,
    MachineCredential,
    MachineKeyUnavailableError,
    MachinePrincipal,
    UnauthorizedError,
    authenticate_machine,
    hash_machine_key,
    require_machine_scope,
)
from dotmac_kernel.secret_sources import clear_secret_source, install_secret_source
from dotmac_kernel.testing import create_test_engine, isolated_session
from sqlalchemy.orm import Session

RAW = "dm_machine_live_abc123"
TENANT = uuid.uuid4()
OTHER_TENANT = uuid.uuid4()


class _Source:
    """A `SecretSource` is a protocol with `load()`, not a callable."""

    def __init__(self, value: str) -> None:
        self._value = value

    def load(self) -> dict[str, str]:
        return {MACHINE_KEY_SECRET_NAME: self._value}


@pytest.fixture(autouse=True)
def _held_key() -> Iterator[None]:
    install_secret_source(_Source("a-dedicated-hmac-key"))
    yield
    clear_secret_source()


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_test_engine()
    try:
        with isolated_session(engine) as session:
            yield session
    finally:
        engine.dispose()


def _credential(db: Session, **over: object) -> MachineCredential:
    fields: dict[str, object] = {
        "tenant_id": TENANT,
        "label": "integrator",
        # Every credential says which application it belongs to. An
        # un-attributed one does not authenticate at all — proven in
        # `test_machine_attribution.py`, which is why it is safe for this file
        # to treat attribution as background and keep asserting the refusals it
        # was written for.
        "source_application": "dotmac_sub",
        "key_hash": hash_machine_key(RAW),
        "scopes": ["licence.descriptor.read"],
        "is_active": True,
    }
    fields.update(over)
    row = MachineCredential(**fields)
    db.add(row)
    db.flush()
    return row


# ── The one happy path ──────────────────────────────────────────────────────


def test_a_scoped_credential_authenticates(db: Session) -> None:
    _credential(db)
    principal = authenticate_machine(db, RAW)
    assert isinstance(principal, MachinePrincipal)
    assert principal.label == "integrator"
    assert principal.has_scope("licence.descriptor.read")


# ── Empty scopes authorize NOTHING — the ERP inversion ──────────────────────


def test_an_unscoped_credential_authorizes_nothing(db: Session) -> None:
    """ERP's `has_scope` returns True for EVERY scope when the list is empty,
    documented as the grandfathered default. A credential that never said what
    it may do can do anything there. Here it can do nothing at all."""
    _credential(db, scopes=[])
    principal = authenticate_machine(db, RAW)
    assert principal.scopes == frozenset()
    assert not principal.has_scope("licence.descriptor.read")
    assert not principal.has_scope("anything.at.all")


def test_there_is_no_wildcard_scope(db: Session) -> None:
    """`*` is a string like any other, not a grant."""
    _credential(db, scopes=["*"])
    principal = authenticate_machine(db, RAW)
    assert not principal.has_scope("licence.descriptor.read")
    assert principal.has_scope("*")


def test_a_principal_carries_no_roles() -> None:
    """No admin shortcut: authority is the enumerated scopes and nothing else."""
    assert not hasattr(MachinePrincipal, "roles")
    assert "roles" not in MachinePrincipal.__dataclass_fields__


# ── Revoked, expired, inactive, unknown — one answer for all four ───────────


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("revoked_at", datetime.now(UTC)),
        ("is_active", False),
        ("expires_at", datetime.now(UTC) - timedelta(seconds=1)),
    ],
)
def test_a_credential_that_should_not_work_does_not(
    db: Session, field: str, value: object
) -> None:
    _credential(db, **{field: value})
    with pytest.raises(UnauthorizedError):
        authenticate_machine(db, RAW)


def test_an_unknown_key_is_refused(db: Session) -> None:
    _credential(db)
    with pytest.raises(UnauthorizedError):
        authenticate_machine(db, "dm_machine_live_not_this_one")


def test_every_refusal_says_the_same_thing(db: Session) -> None:
    """Revoked and never-existed must be indistinguishable to the caller.

    The difference between them is information about another party's key
    management, and an error message is a side channel like any other.
    """
    _credential(db, revoked_at=datetime.now(UTC))
    with pytest.raises(UnauthorizedError) as revoked:
        authenticate_machine(db, RAW)
    with pytest.raises(UnauthorizedError) as unknown:
        authenticate_machine(db, "dm_machine_live_never_minted")
    assert str(revoked.value) == str(unknown.value)


def test_expiry_is_evaluated_against_the_supplied_moment(db: Session) -> None:
    """A half-open window: valid up to `expires_at`, not through it."""
    moment = datetime.now(UTC)
    _credential(db, expires_at=moment)
    with pytest.raises(UnauthorizedError):
        authenticate_machine(db, RAW, now=moment)
    assert authenticate_machine(db, RAW, now=moment - timedelta(seconds=1))


# ── The held key ────────────────────────────────────────────────────────────


def test_no_installed_key_means_no_authentication_at_all() -> None:
    """Sub falls back to unsalted SHA-256 when no secret is configured, so a
    misconfigured deployment keeps authenticating with a weaker scheme. Here a
    missing key is a DEPLOYMENT fault and says so — reporting it as an invalid
    credential is how that fallback came to look reasonable."""
    clear_secret_source()
    with pytest.raises(MachineKeyUnavailableError, match="no unsalted fallback"):
        hash_machine_key(RAW)


def test_the_stored_form_is_always_hmac(db: Session) -> None:
    assert hash_machine_key(RAW).startswith("hmac-sha256:")


def test_the_hash_depends_on_the_dedicated_key() -> None:
    """Sub derives its subkey from the connector-credential encryption key, so
    rotating one invalidates the other. This one has its own."""
    first = hash_machine_key(RAW)
    clear_secret_source()
    install_secret_source(_Source("a-different-key"))
    assert hash_machine_key(RAW) != first


def test_no_secret_leaks_into_the_stored_form_or_an_error(db: Session) -> None:
    secret = "a-dedicated-hmac-key"
    stored = hash_machine_key(RAW)
    assert secret not in stored
    assert RAW not in stored

    # And nothing sensitive reaches the principal a handler receives.
    _credential(db, scopes=["other.scope"])
    principal = authenticate_machine(db, RAW)
    rendered = repr(principal)
    for sensitive in (secret, RAW, stored):
        assert sensitive not in rendered

    # A refusal names the LABEL, which is why the label must never be a hint.
    with pytest.raises(UnauthorizedError) as refused:
        authenticate_machine(db, "dm_machine_live_wrong")
    assert secret not in str(refused.value)
    assert RAW not in str(refused.value)


# ── Transaction boundary ────────────────────────────────────────────────────


def test_authentication_neither_commits_nor_writes(db: Session) -> None:
    """Sub commits `last_used_at` inside a GET and rehashes on use. Neither is
    possible here: the row has no usage column and the code issues one SELECT.

    Asserted by watching the session rather than by reading the source, so a
    future write shows up as a failure instead of a review miss.
    """
    row = _credential(db)
    commits: list[int] = []
    original = db.commit
    db.commit = lambda *a, **k: commits.append(1)  # type: ignore[method-assign]
    try:
        authenticate_machine(db, RAW)
    finally:
        db.commit = original  # type: ignore[method-assign]
    assert commits == []
    assert not db.dirty
    assert not db.new
    assert not hasattr(row, "last_used_at")


def test_the_row_has_no_usage_or_human_columns() -> None:
    """The four absences the inventory demanded, as a structural assertion."""
    columns = set(MachineCredential.__table__.columns.keys())
    for absent in ("last_used_at", "person_id", "subscriber_id", "roles"):
        assert absent not in columns, absent


# ── Scope declaration ───────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["", " ", "trailing ", " leading"])
def test_a_route_cannot_declare_a_malformed_scope(bad: str) -> None:
    with pytest.raises(ValueError, match="non-empty, trimmed"):
        require_machine_scope(bad)


def test_the_header_is_the_fleet_one() -> None:
    assert API_KEY_HEADER == "X-Api-Key"
