"""Attribution, exact scope, and rotation without a dropped call.

Three claims, and each is asserted by the behaviour that would be WRONG if the
claim were false rather than by reading the implementation:

1. a credential scoped to one capability cannot invoke another — and "cannot"
   means no prefix, no substring, no wildcard, no case fold;
2. nothing unattributed gets through: not a credential, not a command, not an
   audit row;
3. two secrets are live during a rotation, and only an explicit step retires
   one.

Every departure asserted here is from a REAL source behaviour recorded in
`docs/inventories/machine-credential-sources.md`; the test names say which.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from dotmac_kernel import (
    MACHINE_KEY_SECRET_NAME,
    MachineCredential,
    UnauthorizedError,
    authenticate_machine,
    begin_rotation,
    cancel_rotation,
    complete_rotation,
    hash_machine_key,
    issue_credential,
    revoke_credential,
)
from dotmac_kernel.audit import UnattributedAuditEventError, resolve_event_attribution
from dotmac_kernel.machine_rotation import RotationStateError
from dotmac_kernel.messaging import CommandEnvelope, UnattributedCommandError
from dotmac_kernel.messaging.inbox import process_once
from dotmac_kernel.secret_sources import clear_secret_source, install_secret_source
from dotmac_kernel.source_applications import (
    InvalidSourceApplicationError,
    SourceApplicationRegistry,
    UndeclaredSourceApplicationError,
    clear_host_application,
    install_source_applications,
)
from dotmac_kernel.testing import create_test_engine, isolated_session
from sqlalchemy.orm import Session

from tests.conftest import TEST_HOST_APPLICATION

TENANT = uuid.uuid4()
HELD_KEY = "a-dedicated-hmac-key"
PEER = "dotmac_sub"


class _Source:
    """A `SecretSource` is a protocol with `load()`, not a callable."""

    def __init__(self, value: str) -> None:
        self._value = value

    def load(self) -> dict[str, str]:
        return {MACHINE_KEY_SECRET_NAME: self._value}


@pytest.fixture(autouse=True)
def _held_key() -> Iterator[None]:
    install_secret_source(_Source(HELD_KEY))
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


def _issue(
    db: Session, *, scopes: list[str], label: str = "integrator"
) -> tuple[MachineCredential, str]:
    return issue_credential(
        db,
        tenant_id=TENANT,
        label=label,
        source_application=PEER,
        scopes=scopes,
    )


# ── (a) Scope enforcement is EXACT ──────────────────────────────────────────


def test_a_credential_scoped_to_one_capability_cannot_invoke_another(
    db: Session,
) -> None:
    """The headline claim, and the reason the facility exists."""
    _, raw = _issue(db, scopes=["licence.descriptor.read"])
    principal = authenticate_machine(db, raw)
    assert principal.has_scope("licence.descriptor.read")
    assert not principal.has_scope("licence.descriptor.write")
    assert not principal.has_scope("billing.invoice.create")


@pytest.mark.parametrize(
    "required",
    [
        # A LONGER requirement: the held scope is a prefix of it. A `startswith`
        # check in the wrong direction would grant this.
        "network.nas.write.force",
        # A SHORTER requirement: the held scope starts with it. A `startswith`
        # check in the other direction would grant this.
        "network.nas",
        "network",
        # A SUBSTRING of the held scope. An `in` test over the joined scopes
        # would grant this, which is how the mistake usually gets written.
        "nas",
        "write",
        # Sub's actual behaviour: `has_permission` expands the REQUIRED
        # permission through `_wildcard_ancestors`, so a key holding
        # `network:*` — or `*` — satisfies `network:nas:write` there. The
        # expansion does not exist here, in either direction.
        "network.*",
        "*",
        # Case. A `casefold()` on either side would grant this.
        "NETWORK.NAS.WRITE",
        "Network.Nas.Write",
        # Whitespace, which an untrimmed operator entry would produce.
        " network.nas.write",
        "network.nas.write ",
    ],
)
def test_a_near_miss_scope_is_not_a_match(db: Session, required: str) -> None:
    """SENSITIVITY PROOF for the exactness claim.

    Each parameter is a string that a prefix, substring, glob or case-folding
    implementation would have accepted. Asserting only the happy path would
    pass against every one of those wrong implementations, so this list is the
    test — `has_scope` is one line, and one line is exactly the kind of code a
    later "convenience" edit widens.
    """
    _, raw = _issue(db, scopes=["network.nas.write"])
    principal = authenticate_machine(db, raw)
    assert not principal.has_scope(required)


def test_the_wildcard_holder_is_the_one_who_gets_nothing(db: Session) -> None:
    """The mirror image, and the more dangerous direction.

    A key issued with `*` under Sub's rules invokes everything. Here `*` is a
    string, so a key holding it can invoke exactly one capability: the one
    literally named `*`, which no route declares.
    """
    _, raw = _issue(db, scopes=["*"])
    principal = authenticate_machine(db, raw)
    assert not principal.has_scope("network.nas.write")
    assert not principal.has_scope("anything")
    assert principal.has_scope("*")


def test_an_untrimmed_scope_cannot_be_issued(db: Session) -> None:
    """Refused at ISSUANCE, because matching is exact.

    `" billing.read"` would sit in the row looking granted and never authorize
    anything — a permission that reads as present and behaves as absent, which
    is worse than either.
    """
    with pytest.raises(ValueError, match="non-empty, trimmed"):
        _issue(db, scopes=[" billing.read"])


# ── (b) Nothing unattributed gets through ───────────────────────────────────


def test_the_principal_names_the_source_application(db: Session) -> None:
    _, raw = _issue(db, scopes=["licence.descriptor.read"])
    assert authenticate_machine(db, raw).application == PEER


def test_an_unattributed_credential_does_not_authenticate(db: Session) -> None:
    """SENSITIVITY PROOF for the nullable column.

    `source_application` is nullable at the schema for one release so an
    existing deployment can attribute its rows. That is only safe if an
    un-attributed row cannot be used meanwhile — so this writes the NULL
    directly, past `issue_credential`, exactly as a pre-migration row would
    arrive, and proves the runtime refuses it.
    """
    row = MachineCredential(
        tenant_id=TENANT,
        label="legacy",
        source_application=None,
        key_hash=hash_machine_key("dm_machine_live_legacy"),
        scopes=["licence.descriptor.read"],
        is_active=True,
    )
    db.add(row)
    db.flush()
    with pytest.raises(UnauthorizedError):
        authenticate_machine(db, "dm_machine_live_legacy")


def test_the_unattributed_refusal_is_indistinguishable_from_any_other(
    db: Session,
) -> None:
    """Which of a tenant's rows are still un-attributed is that tenant's
    business, so this refusal must not be its own message."""
    row = MachineCredential(
        tenant_id=TENANT,
        label="legacy",
        source_application=None,
        key_hash=hash_machine_key("dm_machine_live_legacy"),
        scopes=[],
        is_active=True,
    )
    db.add(row)
    db.flush()
    with pytest.raises(UnauthorizedError) as unattributed:
        authenticate_machine(db, "dm_machine_live_legacy")
    with pytest.raises(UnauthorizedError) as unknown:
        authenticate_machine(db, "dm_machine_live_never_minted")
    assert str(unattributed.value) == str(unknown.value)


def test_a_credential_cannot_be_issued_without_an_application(db: Session) -> None:
    with pytest.raises(TypeError):
        issue_credential(  # type: ignore[call-arg]
            db, tenant_id=TENANT, label="nameless", scopes=["a.b"]
        )


def test_a_credential_cannot_be_issued_for_an_undeclared_application(
    db: Session,
) -> None:
    with pytest.raises(UndeclaredSourceApplicationError):
        issue_credential(
            db,
            tenant_id=TENANT,
            label="stranger",
            source_application="dotmac_not_a_peer",
            scopes=["a.b"],
        )


def test_an_unattributed_command_is_refused(db: Session) -> None:
    """SENSITIVITY PROOF for command attribution.

    Refused at CONSTRUCTION, which is the last moment the answer is still
    knowable: once the envelope is queued, retried, or halfway through a
    handler, nobody can recover who sent it and every available remedy invents
    one. Sub's nearest equivalent — `CommandContext.system(actor=...)` — takes
    free text and would have accepted anything at all here.
    """
    with pytest.raises(UnattributedCommandError, match="does not say which"):
        CommandEnvelope(command_id="c1", command_type="do.thing", tenant_id=TENANT)


def test_there_is_no_anonymous_application_to_fall_back_to(db: Session) -> None:
    """SENSITIVITY PROOF that the refusal is not merely a `None` check.

    A guard that only rejected `None` would be satisfied by the first plausible
    string a caller reached for, and `"system"` is exactly that string. Absent
    and empty fail at construction; `"system"`, `"unknown"` and `"internal"`
    are well-FORMED codes, so nothing about their shape saves us — they fail at
    the acceptance boundary, for the honest reason that no deployment declares
    them, because they are not applications.
    """
    with pytest.raises(UnattributedCommandError):
        CommandEnvelope(command_id="c1", command_type="t", tenant_id=TENANT)
    with pytest.raises(InvalidSourceApplicationError):
        CommandEnvelope(
            command_id="c1",
            command_type="t",
            tenant_id=TENANT,
            source_application="",
        )

    def handler(session: Session, env: CommandEnvelope) -> None:
        raise AssertionError("an anonymous issuer must never reach the handler")

    for anonymous in ("system", "unknown", "internal"):
        envelope = CommandEnvelope(
            command_id=f"c-{anonymous}",
            command_type="t",
            tenant_id=TENANT,
            source_application=anonymous,
        )
        with pytest.raises(UndeclaredSourceApplicationError):
            process_once(db, envelope, handler)


def test_source_application_membership_is_exact() -> None:
    """SENSITIVITY PROOF for the registry, the same shape as the scope one.

    `dotmac_sub_staging` shares a prefix with a declared peer and `sub` is a
    substring of one. A `startswith` or `in` implementation would admit both,
    and the failure would be an entire second environment authenticating as
    production.
    """
    registry = SourceApplicationRegistry({"dotmac_sub"})
    assert registry.is_declared("dotmac_sub")
    for near_miss in ("dotmac_sub_staging", "dotmac", "sub", "dotmac_s", "*"):
        assert not registry.is_declared(near_miss)
        with pytest.raises(
            (UndeclaredSourceApplicationError, InvalidSourceApplicationError)
        ):
            registry.require(near_miss)


def test_an_audit_event_records_the_issuing_application() -> None:
    """An explicit attribution is recorded verbatim; the host identity fills in
    only for what this process originated itself."""
    assert resolve_event_attribution(PEER) == PEER
    assert resolve_event_attribution(None) == TEST_HOST_APPLICATION


def test_an_audit_event_with_no_attribution_at_all_is_refused() -> None:
    """SENSITIVITY PROOF for the audit half.

    The autouse fixture installs a host identity for every test, so a guard
    that never fired would still look correct. Clearing it is the only way to
    observe the refusal — and the refusal is what stops a NULL, or a plausible
    `"system"`, appearing in the one column meant to be trustworthy.
    """
    clear_host_application()
    with pytest.raises(UnattributedAuditEventError, match="never declared"):
        resolve_event_attribution(None)


def test_a_malformed_attribution_is_refused_before_it_can_truncate() -> None:
    """64 characters is the storage width. A longer code that validated here
    would be silently truncated in the column, which is a WRONG attribution
    rather than a missing one — the failure mode that is hardest to notice."""
    with pytest.raises(InvalidSourceApplicationError):
        resolve_event_attribution("d" * 65)
    for malformed in ("Dotmac_Sub", "dotmac sub", "dotmac-sub", "1sub", "s"):
        with pytest.raises(InvalidSourceApplicationError):
            resolve_event_attribution(malformed)


# ── (c) Rotation without a dropped call ─────────────────────────────────────


def test_both_keys_work_during_a_rotation_window(db: Session) -> None:
    """SENSITIVITY PROOF for the whole rotation model.

    Sub's `rotate_api_key` overwrites `key_hash` and its docstring says the old
    secret stops working immediately — so under Sub's model the FIRST assertion
    here fails. That is the point of asserting the old key, not just the new
    one: proving the new key works proves nothing Sub does not already do.
    """
    credential, old_raw = _issue(db, scopes=["licence.descriptor.read"])
    new_raw = begin_rotation(db, credential)

    assert new_raw != old_raw
    old_principal = authenticate_machine(db, old_raw)
    new_principal = authenticate_machine(db, new_raw)

    # The SAME principal, not two. Scopes, attribution and credential identity
    # are unchanged, so nothing downstream can tell which secret was used and
    # the trail does not fork mid-rotation.
    assert old_principal == new_principal
    assert new_principal.application == PEER
    assert new_principal.scopes == frozenset({"licence.descriptor.read"})


def test_nothing_closes_the_window_on_a_clock(db: Session) -> None:
    """SENSITIVITY PROOF that the rotation is explicit, not a TTL.

    Authentication takes `now`, so a time-based retirement is directly
    observable: a year after the window opened, the outgoing secret still
    works. A TTL would make the migration fail for whoever was slowest, which
    is precisely the caller a window is for.
    """
    credential, old_raw = _issue(db, scopes=["a.b"])
    begin_rotation(db, credential, now=datetime.now(UTC))
    much_later = datetime.now(UTC) + timedelta(days=365)
    assert authenticate_machine(db, old_raw, now=much_later)


def test_completing_the_rotation_is_what_retires_the_old_key(db: Session) -> None:
    credential, old_raw = _issue(db, scopes=["a.b"])
    new_raw = begin_rotation(db, credential)
    complete_rotation(db, credential)

    assert authenticate_machine(db, new_raw).application == PEER
    with pytest.raises(UnauthorizedError):
        authenticate_machine(db, old_raw)
    assert credential.next_key_hash is None
    assert credential.rotation_started_at is None
    assert credential.rotated_at is not None


def test_cancelling_retires_the_incoming_key_and_disturbs_nothing(
    db: Session,
) -> None:
    """The safe direction: cancelling stops the key nobody depends on yet."""
    credential, old_raw = _issue(db, scopes=["a.b"])
    new_raw = begin_rotation(db, credential)
    cancel_rotation(db, credential)

    assert authenticate_machine(db, old_raw).application == PEER
    with pytest.raises(UnauthorizedError):
        authenticate_machine(db, new_raw)


def test_a_second_rotation_cannot_open_while_one_is_open(db: Session) -> None:
    """Two incoming keys would make "which secret is next" unanswerable, and
    `complete_rotation` would then promote one of them by accident."""
    credential, _ = _issue(db, scopes=["a.b"])
    begin_rotation(db, credential)
    with pytest.raises(RotationStateError, match="already rotating"):
        begin_rotation(db, credential)


def test_completing_a_rotation_that_never_began_raises(db: Session) -> None:
    credential, _ = _issue(db, scopes=["a.b"])
    with pytest.raises(RotationStateError, match="no open rotation"):
        complete_rotation(db, credential)


def test_revoking_stops_both_secrets(db: Session) -> None:
    """A revoked credential must not leave a live incoming digest behind: an
    operator reading the row could not tell it was dead, and any later
    relaxation of the `is_active` predicate would resurrect it."""
    credential, old_raw = _issue(db, scopes=["a.b"])
    new_raw = begin_rotation(db, credential)
    revoke_credential(db, credential)

    assert credential.next_key_hash is None
    for raw in (old_raw, new_raw):
        with pytest.raises(UnauthorizedError):
            authenticate_machine(db, raw)


def test_a_revoked_credential_cannot_be_rotated(db: Session) -> None:
    credential, _ = _issue(db, scopes=["a.b"])
    revoke_credential(db, credential)
    with pytest.raises(RotationStateError, match="revoked or inactive"):
        begin_rotation(db, credential)


# ── No secret value in a log, an error, or a row ────────────────────────────


def test_no_secret_value_appears_in_a_log_or_an_error(
    db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """SENSITIVITY PROOF for the key-material claim.

    Exercised over the whole lifecycle — issue, rotate, complete, then a failed
    authentication — because a leak is a single stray f-string and the paths
    that handle raw material are exactly these. The HELD verification key is
    checked alongside the two raw keys: it never reaches any of these paths as
    a value, only as a lookup name.
    """
    caplog.set_level(logging.DEBUG)
    credential, old_raw = _issue(db, scopes=["a.b"])
    new_raw = begin_rotation(db, credential)
    complete_rotation(db, credential)

    with pytest.raises(UnauthorizedError) as refused:
        authenticate_machine(db, old_raw)
    with pytest.raises(RotationStateError) as state:
        complete_rotation(db, credential)

    secrets_that_must_not_appear = (old_raw, new_raw, HELD_KEY)
    logged = "\n".join(record.getMessage() for record in caplog.records)
    rendered = "\n".join(
        (
            logged,
            str(refused.value),
            str(state.value),
            repr(credential),
            repr(authenticate_machine(db, new_raw)),
        )
    )
    for secret in secrets_that_must_not_appear:
        assert secret not in rendered, "raw key material escaped"


def test_the_raw_key_is_never_what_is_stored(db: Session) -> None:
    credential, raw = _issue(db, scopes=["a.b"])
    assert credential.key_hash != raw
    assert raw not in credential.key_hash
    assert credential.key_hash.startswith("hmac-sha256:")


def test_the_checker_would_notice_a_leak(db: Session) -> None:
    """Sensitivity self-test for the leak check above.

    The assertion is a substring search, and a substring search over text that
    happens not to contain the secret passes for the wrong reason. Planting the
    value proves the search is capable of failing.
    """
    credential, raw = _issue(db, scopes=["a.b"])
    planted = f"boom {raw} boom"
    assert raw in planted
    assert raw not in repr(credential)


# ── The acceptance boundary refuses an undeclared issuer ────────────────────


def test_a_command_from_an_undeclared_application_is_refused(db: Session) -> None:
    """Checked BEFORE the idempotency ledger is touched.

    Recording it first would make the refusal permanent on replay and would let
    an unaccepted issuer consume a `command_id` — a denial-of-service against
    the real owner of that id, delivered by the guard meant to protect it.
    """
    install_source_applications(SourceApplicationRegistry({TEST_HOST_APPLICATION}))
    envelope = CommandEnvelope(
        command_id="cmd-stranger",
        command_type="do.thing",
        tenant_id=TENANT,
        source_application=PEER,
    )
    calls: list[str] = []

    def handler(session: Session, env: CommandEnvelope) -> None:
        calls.append(env.command_id)
        return None

    with pytest.raises(UndeclaredSourceApplicationError):
        process_once(db, envelope, handler)
    assert calls == [], "the handler ran for an undeclared issuer"
