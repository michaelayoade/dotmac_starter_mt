"""Tenant-scoped auth service — register/login flows + the web (cookie)
login/logout surface.

Auth orchestration lives here; exact session revocation DML delegates to the
Kernel's public identity storage adapter. `app/features/auth/router.py` and
`app/features/auth/web.py` resolve dependencies, delegate and shape responses.

Registration creates a `party_type == person` `Party` plus its `PartyPerson`
subtype row in the same transaction — `Party` replaced `Person` (Task 6); the
`/auth/register` request/response shape is unchanged, kept mechanically
person-shaped (Task 7 owns any schema redesign).

`web_login`/`web_logout` (Task 3, relocated here per Task 3 review — see
`.superpowers/sdd/task-3-report.md`'s fix note) are the admin-portal's
cookie-based counterparts to `login()`: `web_login` calls `login()` directly,
in the SAME module, so there is no cross-feature import at all (the prior
`app.features.web.service -> app.features.auth.service` edge, and its
`pyproject.toml` `ignore_imports` carve-out, are both gone). `web_logout`
revokes an `AuthSession` — a CORE model — same pattern `login()` itself uses
to create one.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from sys import exc_info
from typing import NoReturn, cast
from uuid import UUID

from dotmac_kernel.exceptions import ConflictError, ForbiddenError, UnauthorizedError
from dotmac_kernel.identity import normalize_email, person_display_name
from dotmac_kernel.identity_storage import revoke_party_sessions
from dotmac_kernel.models import (
    AuthSession,
    Party,
    PartyPerson,
    PartyType,
    Tenant,
    UserCredential,
)
from dotmac_kernel.security import (
    hash_password,
    hash_token,
    issue_access_token,
    password_needs_rehash,
    verify_password,
)
from dotmac_kernel.settings_resolver import resolve
from dotmac_kernel.transactions import (
    ConflictSavepointPhase,
    ConflictSavepointProtocolError,
    conflict_savepoint,
    current_conflict_savepoint,
)
from sqlalchemy import Table, event, func, insert, select, update
from sqlalchemy.engine import Connection, NestedTransaction, RootTransaction
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, SessionTransaction

from app.features.auth.schemas import LoginRequest, RegisterRequest
from app.features.auth.spec import REGISTRATION_POLICY

# Constant-work login (Task 5): the no-such-party / org-party / no-credential
# miss paths verify against this pre-computed dummy hash so every failure
# path runs exactly one password verification — no timing oracle
# distinguishing "unknown email" from "wrong password".
_DUMMY_HASH = hash_password("login-dummy-password-for-constant-work")


@dataclass(frozen=True)
class LoginResult:
    """`TokenResponse`-shaped result — router wraps this into the response schema."""

    access_token: str
    token_type: str = "bearer"


@dataclass(frozen=True)
class PersonView:
    """`CurrentUserResponse`-shaped combination of `Party` + its `PartyPerson`
    subtype row — the API keeps returning person-shaped payloads this task
    (Task 7 owns schema redesign), but the fields now come from two tables.
    """

    id: UUID
    email: str
    first_name: str
    last_name: str
    tenant_id: UUID


def register(db: Session, tenant: Tenant, payload: RegisterRequest) -> PersonView:
    # Registration policy (control-plane security Task 2): self-registration
    # is an explicit, tenant-scoped policy decision — default CLOSED. Owner
    # accounts come from platform provisioning
    # (`app.features.tenants.service.provision_tenant`), which is the only
    # owner-creation path; the race-prone first-registrant-becomes-admin
    # bootstrap is deleted outright. A registered user under an `open`
    # policy is a PLAIN user — roles are granted explicitly via rbac.
    policy = resolve(db, REGISTRATION_POLICY, tenant_id=tenant.id)
    if policy != "open":
        raise ForbiddenError("registration_closed")
    # Normalize to lowercase at this boundary so a later login lookup
    # matches the parties CI-unique semantics (the `parties` table's email
    # uniqueness index is `lower(email)`-based — `Party.email` is the ONLY
    # place an email is stored now, F2/Task 3: the credential row carries no
    # email of its own, so there is nothing left to keep in sync).
    # `normalize_email`/`person_display_name` are the single-owner
    # implementations of these invariants (dotmac_kernel.identity) — the parties
    # service's create/update paths call the same two functions, so the two
    # writers can never independently drift (see docs/ARCHITECTURE.md's
    # "Known dual-writer: Parties" section).
    email = normalize_email(payload.email)
    party = Party(
        tenant_id=tenant.id,
        party_type=PartyType.person,
        display_name=person_display_name(payload.first_name, payload.last_name),
        email=email,
    )
    # `db.add(party)` happens INSIDE the savepoint, not before it: `Session.
    # begin_nested()` auto-flushes any already-pending changes as part of
    # establishing the SAVEPOINT (`_take_snapshot`) — adding `party` before
    # entering `conflict_savepoint` would let that pre-flush emit the
    # conflicting INSERT with no savepoint yet in place to protect the
    # outer transaction's `SET LOCAL` if it fails. See
    # `.superpowers/sdd/task-2-report.md`'s harness-interplay notes.
    try:
        with conflict_savepoint(db):
            db.add(party)
            db.flush()
            party_person = PartyPerson(
                party_id=party.id,
                first_name=payload.first_name,
                last_name=payload.last_name,
            )
            db.add(party_person)
            credential = UserCredential(
                tenant_id=tenant.id,
                party_id=party.id,
                password_hash=hash_password(payload.password),
            )
            db.add(credential)
            db.flush()
            db.refresh(party)
    except IntegrityError as exc:
        raise ConflictError("Email already registered") from exc
    return PersonView(
        id=party.id,
        email=email,
        first_name=party_person.first_name,
        last_name=party_person.last_name,
        tenant_id=party.tenant_id,
    )


def login(db: Session, tenant: Tenant, payload: LoginRequest) -> LoginResult:
    """Resolve the login identity via `Party.email` (F2/Task 3 — the single
    email authority), then find the credential row by `party_id`.

    Two-step resolution, both tenant-scoped: (1) `Party` by `(tenant_id,
    normalize_email(email), party_type == person)` — organization parties
    have no login of their own; (2) `UserCredential` by `(tenant_id,
    party_id)`. Either miss raises the SAME `UnauthorizedError("Invalid
    credentials")` as a wrong password, AND (control-plane security Task 5)
    burns one real `verify_password` against the module-level `_DUMMY_HASH`
    first — constant-work: the historical timing oracle (a "no such
    credential" reply returning measurably faster than a "wrong password"
    reply) is closed at this seam.

    Intended consequence (documented, not a bug): NULLing a person party's
    `email` (`app.features.parties.service.update_person_party`) disables
    login for that party outright — the query is `func.lower(Party.email) ==
    normalize_email(email)`, and a NULL column matches no string. There is
    no separate identity to fall back to once the single email column is
    cleared; see `docs/ARCHITECTURE.md`'s "Auth credentials" ownership row
    and `docs/superpowers/phase2-backlog.md`'s (resolved) F2 entry.
    """
    with _operation(db) as owner:
        email = normalize_email(payload.email)
        owner.check()
        tid = tenant.id
        owner.check()
        party = db.scalars(
            select(Party)
            .where(Party.tenant_id == tid)
            .where(Party.party_type == PartyType.person)
            # `email` is already lowercased by `normalize_email` above, so this
            # predicate is defense-in-depth, not the normalization itself — but
            # it must be `func.lower(Party.email) == email`, not a bare
            # `Party.email == email`, because that's *exactly* the expression
            # the partial functional index `uq_parties_tenant_lower_email`
            # (`(tenant_id, lower(email)) WHERE email IS NOT NULL`, see
            # `dotmac_kernel/models.py::Party.__table_args__`) is built on. Postgres
            # only matches a functional index when the query's WHERE clause is
            # syntactically the same expression the index was created with — a
            # bare `Party.email == email` would silently fall back to a seq
            # scan on this column.
            .where(func.lower(Party.email) == email)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        credential = (
            db.scalars(
                select(UserCredential)
                .where(UserCredential.tenant_id == tid)
                .where(UserCredential.party_id == party.id)
                .execution_options(populate_existing=True)
            ).first()
            if party is not None
            else None
        )
        owner.check()  # includes ORM population/refresh callbacks
        if credential is None:
            # Constant-work miss path (Task 5): burn one real verification
            # against the dummy hash so "unknown email"/"no credential" costs
            # the same as "wrong password" — closing the 2c-auth backlog's
            # timing-oracle line.
            verify_password(payload.password, _DUMMY_HASH)
            owner.check()
            raise UnauthorizedError("Invalid credentials")
        credential_hash = credential.password_hash
        owner.check()
        verified = verify_password(payload.password, credential_hash)
        owner.check()
        if not verified:
            raise UnauthorizedError("Invalid credentials")

        # Upgrade-on-login (Task 5): a successfully verified legacy PBKDF2 (or
        # outdated-parameter Argon2id) hash is re-written as current Argon2id —
        # the only moment the plaintext is available to re-hash.
        needs_rehash = password_needs_rehash(credential_hash)
        owner.check()
        if needs_rehash:
            owner.track(credential, ("password_hash", "updated_at"))
            db.execute(
                update(cast(Table, UserCredential.__table__))
                .where(
                    UserCredential.tenant_id == tid,
                    UserCredential.id == credential.id,
                    UserCredential.party_id == credential.party_id,
                )
                .values(password_hash=hash_password(payload.password))
            )
            db.refresh(credential, attribute_names=["password_hash", "updated_at"])
            owner.check()

        pid = credential.party_id
        owner.check()
        token, expires_at = issue_access_token(pid, tid)
        owner.check()
        digest = hash_token(token)
        owner.check()
        db.execute(
            insert(cast(Table, AuthSession.__table__)).values(
                tenant_id=tid,
                party_id=pid,
                token_hash=digest,
                expires_at=expires_at,
            )
        )
        return LoginResult(access_token=token)


def get_current_user_view(db: Session, party: Party) -> PersonView:
    """Combine `party` with its `PartyPerson` subtype row for `/auth/me`."""
    party_person = db.get(PartyPerson, party.id)
    if party_person is None or party.email is None:
        raise UnauthorizedError("Invalid credentials")
    return PersonView(
        id=party.id,
        email=party.email,
        first_name=party_person.first_name,
        last_name=party_person.last_name,
        tenant_id=party.tenant_id,
    )


def web_login(db: Session, tenant: Tenant, username: str, password: str) -> str | None:
    """Attempt login via `login()` UNCHANGED — same module, no cross-feature
    import needed (this used to be `app.features.web.service.web_login`
    calling `auth_service.login`; Task 3 review's required fix moved it here
    so the call is same-module instead of cross-feature).

    Returns the access token on success, `None` on invalid credentials —
    never raises, so the web route can re-render the login form with a
    generic error instead of leaking which part of the credential pair was
    wrong.
    """
    try:
        result = login(db, tenant, LoginRequest(email=username, password=password))
    except UnauthorizedError:
        return None
    return result.access_token


def web_logout(db: Session, tenant: Tenant, token: str | None) -> None:
    """Revoke the `AuthSession` backing `token`, if any.

    Plain, direct query against `AuthSession` — a CORE model — same pattern
    `login()` above uses to CREATE a session; this is the mirror-image
    REVOKE. Silently no-ops on a missing/already-revoked/foreign token —
    logout must always succeed from the caller's point of view (clearing a
    stale cookie is not an error).
    """
    with _operation(db) as owner:
        if not token:
            return
        tid = tenant.id
        owner.check()
        digest = hash_token(token)
        # Detached discovery is not authority, and takes no child lock.
        found = db.execute(
            select(AuthSession.id, AuthSession.party_id).where(
                AuthSession.tenant_id == tid,
                AuthSession.token_hash == digest,
            )
        ).first()
        if found is None:
            return
        sid, pid = found
        principal = db.scalar(
            select(Party.id)
            .where(
                Party.tenant_id == tid,
                Party.id == pid,
            )
            .with_for_update()
        )
        if principal is None:
            return
        # The complete operation cohort is this exact proved row. Recheck
        # token membership after the parent wait, before any session lock.
        cohort = tuple(
            db.scalars(
                select(AuthSession.id)
                .where(
                    AuthSession.tenant_id == tid,
                    AuthSession.party_id == pid,
                    AuthSession.id == sid,
                    AuthSession.token_hash == digest,
                )
                .order_by(AuthSession.id)
                .with_for_update()
            )
        )
        if not cohort:
            return
        revoke_party_sessions(
            db,
            tenant_id=tid,
            party_id=pid,
            session_ids=cohort,
            revoked_at=datetime.now(UTC),
        )
        _refresh_sessions(db, cohort, owner=owner)


__all__ = [
    "LoginResult",
    "PersonView",
    "get_current_user_view",
    "login",
    "register",
    "web_login",
    "web_logout",
]


# The reference assembly imports only public Kernel APIs. Its bounded operation
# guard stays in this owner; the Kernel binding owner has its own private guard.
_REFERENCE_FAILED_ROOT = "_reference_auth_failed_identity_root"


def _hold_reference_root(db: Session, root: SessionTransaction) -> None:
    if db.get_transaction() is root:
        db.info[_REFERENCE_FAILED_ROOT] = root


@event.listens_for(Session, "before_commit")
def _refuse_failed_reference_root(db: Session) -> None:
    root = db.get_transaction()
    if root is not None and db.info.get(_REFERENCE_FAILED_ROOT) is root:
        raise ConflictSavepointProtocolError(
            "reference identity root requires owner rollback"
        )


@event.listens_for(Session, "after_transaction_end")
def _clear_reference_root(db: Session, transaction: SessionTransaction) -> None:
    if (
        transaction.parent is None
        and db.info.get(_REFERENCE_FAILED_ROOT) is transaction
    ):
        db.info.pop(_REFERENCE_FAILED_ROOT, None)


def _pending(db: Session) -> None:
    # Passive collection membership: never load attributes to decide cleanliness.
    if db.new or db.dirty or db.deleted:
        raise ConflictSavepointProtocolError(
            "identity operation requires clean pending state"
        )


@dataclass
class _Operation:
    db: Session
    root: SessionTransaction
    savepoint: SessionTransaction | None = None
    failed: bool = False
    connection: Connection | None = None
    physical_root: RootTransaction | None = None
    physical_savepoint: NestedTransaction | None = None
    physical_guarded: bool = False
    physical_released: bool = False
    physical_recovery_started: bool = False
    physical_recovered: bool = False
    affected: list[tuple[object, tuple[str, ...]]] = field(default_factory=list)

    def track(self, row: object, attributes: tuple[str, ...]) -> None:
        from sqlalchemy import inspect

        self.check()
        state = inspect(row)
        if state is None or state.session is not self.db or not state.persistent:
            self.refuse()
        self.affected.append((row, attributes))

    def refuse(self) -> NoReturn:
        self.failed = True
        raise ConflictSavepointProtocolError("identity operation protocol differs")

    def check(self) -> None:
        try:
            _pending(self.db)
        except ConflictSavepointProtocolError:
            self.failed = True
            raise
        if (
            self.failed
            or self.db.get_transaction() is not self.root
            or not self.root.is_active
        ):
            self.refuse()
        if self.connection is not None:
            if (
                self.physical_root is None
                or not self.physical_root.is_active
                or self.connection.get_transaction() is not self.physical_root
            ):
                self.refuse()
            if (
                self.savepoint is not None
                and not self.physical_released
                and (
                    self.physical_savepoint is None
                    or not self.physical_savepoint.is_active
                    or self.connection.get_nested_transaction()
                    is not self.physical_savepoint
                )
            ):
                self.refuse()
        if self.savepoint is not None and not self.physical_released:
            state = current_conflict_savepoint(self.db)
            if (
                state is None
                or state.root is not self.root
                or state.savepoint is not self.savepoint
                or self.db.get_nested_transaction() is not self.savepoint
                # SA deactivates the ORM savepoint before physical RELEASE.
                or (
                    not self.savepoint.is_active
                    and state.phase is not ConflictSavepointPhase.RELEASE
                )
                or state.phase
                not in (ConflictSavepointPhase.BODY, ConflictSavepointPhase.RELEASE)
            ):
                self.refuse()


@contextmanager
def _operation(db: Session) -> Generator[_Operation, None, None]:
    # These are the first operations, before any connection, ORM refresh,
    # savepoint, callback or implicit autobegin. Preexisting state stays intact.
    _pending(db)
    root = db.get_transaction()
    if root is None or not root.is_active:
        raise ConflictSavepointProtocolError(
            "identity operation requires an active caller root"
        )
    owner = _Operation(db, root)

    def before_flush(*args: object) -> None:
        # All participating mutations are explicit Core statements. Never let
        # begin_nested/release or a callback flush a pending ORM object.
        owner.refuse()

    def before_execute(*args: object) -> None:
        owner.check()

    def cursor_check(*args: object) -> None:
        state = current_conflict_savepoint(db)
        if (
            state is not None
            and state.root is root
            and state.savepoint is owner.savepoint
            and state.phase is ConflictSavepointPhase.ROLLBACK
        ):
            if owner.physical_recovery_started:
                owner.physical_recovered = True
            return  # the public helper owns physical recovery, even after refusal
        owner.check()

    def before_commit(*args: object) -> None:
        owner.check()
        state = current_conflict_savepoint(db)
        if (
            owner.savepoint is None
            or state is None
            or state.savepoint is not owner.savepoint
            or state.phase is not ConflictSavepointPhase.RELEASE
        ):
            owner.refuse()

    def no_physical_completion(*args: object) -> None:
        owner.refuse()

    def physical_savepoint_event(*args: object) -> None:
        # An enclosing harness may restart its SAVEPOINT from
        # after_transaction_end once this operation's own savepoint has
        # ended; a new savepoint during BODY still refuses.
        state = current_conflict_savepoint(db)
        if (
            state is not None
            and state.savepoint is owner.savepoint
            and db.get_nested_transaction() is not owner.savepoint
            and (
                (
                    state.phase is ConflictSavepointPhase.RELEASE
                    and owner.physical_released
                )
                or (
                    state.phase is ConflictSavepointPhase.ROLLBACK
                    and owner.physical_recovery_started
                )
            )
        ):
            return
        owner.refuse()

    def physical_release(*args: object) -> None:
        state = current_conflict_savepoint(db)
        if (
            state is None
            or state.savepoint is not owner.savepoint
            or state.phase is not ConflictSavepointPhase.RELEASE
        ):
            owner.refuse()
        owner.check()
        owner.physical_released = True

    def physical_recovery(*args: object) -> None:
        state = current_conflict_savepoint(db)
        if (
            state is None
            or state.savepoint is not owner.savepoint
            or state.phase is not ConflictSavepointPhase.ROLLBACK
        ):
            owner.refuse()
        owner.physical_recovery_started = True

    physical_listeners = (
        ("before_cursor_execute", cursor_check),
        ("after_cursor_execute", cursor_check),
        ("commit", no_physical_completion),
        ("rollback", no_physical_completion),
        ("savepoint", physical_savepoint_event),
        ("release_savepoint", physical_release),
        ("rollback_savepoint", physical_recovery),
    )
    listeners = (
        ("before_flush", before_flush),
        ("do_orm_execute", before_execute),
        ("before_commit", before_commit),
    )
    for name, callback in listeners:
        event.listen(db, name, callback)
    connection = None
    completed = False
    try:
        with db.no_autoflush:
            owner.check()
            with conflict_savepoint(db):
                state = current_conflict_savepoint(db)
                if state is None or state.root is not root:
                    owner.refuse()
                owner.savepoint = state.savepoint
                owner.check()
                # Force the actual SAVEPOINT before any product query/mutation.
                # Opening hooks cannot flush: the guard was installed before
                # begin_nested. A hook-produced pending value is never refreshed.
                connection = db.connection()
                owner.connection = connection
                owner.physical_root = connection.get_transaction()
                owner.physical_savepoint = connection.get_nested_transaction()
                owner.check()
                for name, callback in physical_listeners:
                    event.listen(connection, name, callback)
                owner.physical_guarded = True
                yield owner
                owner.check()
            owner.savepoint = None
            owner.check()
            completed = True
    finally:
        preserving_primary = exc_info()[0] is not None
        cleanup_failed = False
        if not completed and db.get_transaction() is root and root.is_active:
            if owner.connection is not None and (
                owner.physical_root is None
                or not owner.physical_root.is_active
                or owner.connection.get_transaction() is not owner.physical_root
                or (owner.physical_guarded and not owner.physical_recovered)
            ):
                _hold_reference_root(db, root)
            if not (db.new or db.dirty or db.deleted):
                from sqlalchemy import inspect

                for row, attributes in owner.affected:
                    if db.new or db.dirty or db.deleted:
                        _hold_reference_root(db, root)
                        break
                    row_state = inspect(row)
                    if (
                        row_state is not None
                        and row_state.session is db
                        and row_state.persistent
                    ):
                        try:
                            # Core writes do not create UOW dirty snapshots.
                            # Expire ONLY known clean affected fields after the
                            # owning SP recovers, never unrelated caller state.
                            db.expire(row, attribute_names=list(attributes))
                        except BaseException:
                            _hold_reference_root(db, root)
            else:
                _hold_reference_root(db, root)
        owner.affected.clear()
        if connection is not None:
            for name, callback in physical_listeners:
                try:
                    if event.contains(connection, name, callback):
                        event.remove(connection, name, callback)
                except BaseException:
                    cleanup_failed = True
        for name, callback in listeners:
            try:
                if event.contains(db, name, callback):
                    event.remove(db, name, callback)
            except BaseException:
                cleanup_failed = True
        if cleanup_failed:
            _hold_reference_root(db, root)
            if not preserving_primary:
                raise ConflictSavepointProtocolError(
                    "identity operation guard cleanup differs"
                )
        # Recovery hooks may not leave work outside the recovered SAVEPOINT.
        # Do not replace the primary exception or clear any pending values.
        if db.get_transaction() is root and (db.new or db.dirty or db.deleted):
            _hold_reference_root(db, root)


def _refresh_sessions(
    db: Session, ids: tuple[object, ...], *, owner: _Operation
) -> None:
    """Owners refresh only already-cached CLEAN affected revocation attributes."""
    from dotmac_kernel.models import AuthSession
    from sqlalchemy import inspect

    _pending(db)
    wanted = set(ids)
    for row in tuple(db.identity_map.values()):
        if isinstance(row, AuthSession):
            identity = inspect(row).identity
            if identity is not None and identity[0] in wanted:
                owner.track(row, ("revoked_at", "updated_at"))
                _pending(db)
                db.refresh(row, attribute_names=["revoked_at", "updated_at"])
                _pending(db)
