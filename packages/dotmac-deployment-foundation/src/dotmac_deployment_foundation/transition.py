"""Authorized database descriptor transitions and recoverable promotion.

A database transaction and an accepted descriptor normally live in different
transaction domains.  Calling their movement "atomic" hides the crash window
between them.  This module names that window ``promotion_pending`` and makes it
recoverable with an idempotent compare-and-swap keyed by ``transition_id``.

The descriptor is authored before execution.  Nothing here derives one from a
running database: live observations are accepted only as equality evidence for
the declared ``from`` or ``to`` digest.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Mapping
from enum import Enum
from typing import Any, Final, Protocol

from .digest import Digest
from .errors import PreconditionFailed, SpecError

__all__ = [
    "DATABASE_TRANSITION_RECEIPT_SCHEMA",
    "DATABASE_TRANSITION_SCHEMA",
    "PROMOTION_PENDING_SCHEMA",
    "DatabaseCheckpointV1",
    "DatabaseDurability",
    "DatabasePostconditionV1",
    "DatabasePreconditionV1",
    "DatabaseTransitionAuthorizationV1",
    "DatabaseTransitionGrant",
    "DatabaseTransitionReceiptV1",
    "DatabaseTransitionV1",
    "DescriptorPromoter",
    "DescriptorPromotionEvidenceV1",
    "PromotionPendingV1",
    "authorize_database_transition",
    "observe_database_postcondition",
    "promote_database_descriptor",
    "recover_database_promotion",
    "require_database_precondition",
]

DATABASE_TRANSITION_SCHEMA: Final = "DatabaseDescriptorTransition.v1"
PROMOTION_PENDING_SCHEMA: Final = "DatabaseDescriptorPromotionPending.v1"
DATABASE_TRANSITION_RECEIPT_SCHEMA: Final = "DatabaseDescriptorTransitionReceipt.v1"
_TRANSITION_FIELDS: Final = {
    "transition_id",
    "target",
    "plan_digest",
    "from_descriptor_digest",
    "to_descriptor_digest",
    "durability",
    "checkpoints",
}


def _required(value: str, *, where: str) -> str:
    text = str(value).strip()
    if not text:
        raise SpecError(f"{where} is required and cannot be empty")
    return text


def _digest(value: str, *, where: str) -> str:
    return str(Digest.parse(value, where=where))


def _mapping(value: object, *, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SpecError(f"{where} must be an object")
    return value


def _strict(document: Mapping[str, Any], *, where: str, known: set[str]) -> None:
    unknown = sorted(set(document) - known)
    if unknown:
        raise SpecError(f"{where} has unknown field(s) {unknown}")
    missing = sorted(known - set(document))
    if missing:
        raise SpecError(f"{where} is missing required field(s) {missing}")


class DatabaseDurability(str, Enum):
    """How the database operation reaches durable states."""

    ONE_TRANSACTION = "one_transaction"
    DECLARED_CHECKPOINTS = "declared_checkpoints"


@dataclasses.dataclass(frozen=True, slots=True)
class DatabaseCheckpointV1:
    """One ordered durable state of a partially committing operation."""

    code: str
    descriptor_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _required(self.code, where="checkpoint.code"))
        object.__setattr__(
            self,
            "descriptor_digest",
            _digest(self.descriptor_digest, where=f"checkpoint[{self.code}].digest"),
        )

    def as_document(self) -> dict[str, str]:
        return {"code": self.code, "descriptor_digest": self.descriptor_digest}

    @classmethod
    def from_document(cls, value: object) -> DatabaseCheckpointV1:
        document = _mapping(value, where="DatabaseCheckpointV1")
        _strict(
            document,
            where="DatabaseCheckpointV1",
            known={"code", "descriptor_digest"},
        )
        return cls(str(document["code"]), str(document["descriptor_digest"]))


@dataclasses.dataclass(frozen=True, slots=True)
class DatabaseTransitionV1:
    """The pre-authored state change one database operation may produce.

    ``ONE_TRANSACTION`` means there is no durable database state between
    ``from`` and ``to``.  An operation that can commit partial progress must
    instead use ``DECLARED_CHECKPOINTS`` and name at least one intermediate
    descriptor plus the final descriptor.  A lone final candidate is refused:
    it does not describe the state recovery sees after a partial commit.
    """

    transition_id: str
    target: str
    plan_digest: str
    from_descriptor_digest: str
    to_descriptor_digest: str
    durability: DatabaseDurability
    checkpoints: tuple[DatabaseCheckpointV1, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "transition_id",
            _required(self.transition_id, where="transition.transition_id"),
        )
        object.__setattr__(
            self, "target", _required(self.target, where="transition.target")
        )
        object.__setattr__(
            self,
            "plan_digest",
            _digest(self.plan_digest, where="transition.plan_digest"),
        )
        object.__setattr__(
            self,
            "from_descriptor_digest",
            _digest(
                self.from_descriptor_digest,
                where="transition.from_descriptor_digest",
            ),
        )
        object.__setattr__(
            self,
            "to_descriptor_digest",
            _digest(
                self.to_descriptor_digest,
                where="transition.to_descriptor_digest",
            ),
        )
        try:
            durability = DatabaseDurability(self.durability)
        except ValueError as exc:
            raise SpecError(
                f"transition.durability must be one of "
                f"{[item.value for item in DatabaseDurability]}"
            ) from exc
        object.__setattr__(self, "durability", durability)
        checkpoints = tuple(self.checkpoints)
        object.__setattr__(self, "checkpoints", checkpoints)

        if self.from_descriptor_digest == self.to_descriptor_digest:
            raise SpecError(
                "a database descriptor transition must advance from one declared "
                "state to another; from and to are the same digest"
            )
        if durability is DatabaseDurability.ONE_TRANSACTION:
            if checkpoints:
                raise SpecError(
                    "an operation declared as one transaction has no durable "
                    "checkpoints between from and to"
                )
            return
        if len(checkpoints) < 2:
            raise SpecError(
                "a partially committing operation must declare at least one "
                "intermediate durable state and its final state; a lone final "
                "candidate cannot describe partial progress"
            )
        codes = [checkpoint.code for checkpoint in checkpoints]
        digests = [checkpoint.descriptor_digest for checkpoint in checkpoints]
        if len(set(codes)) != len(codes):
            raise SpecError("database checkpoint codes must be unique")
        if len(set(digests)) != len(digests):
            raise SpecError("database checkpoint descriptor digests must be unique")
        if self.from_descriptor_digest in digests:
            raise SpecError(
                "the starting descriptor is a precondition, not a checkpoint"
            )
        if digests[-1] != self.to_descriptor_digest:
            raise SpecError(
                "the last checkpoint must be the transition's result descriptor"
            )

    def as_document(self) -> dict[str, Any]:
        return {
            "transition_id": self.transition_id,
            "target": self.target,
            "plan_digest": self.plan_digest,
            "from_descriptor_digest": self.from_descriptor_digest,
            "to_descriptor_digest": self.to_descriptor_digest,
            "durability": self.durability.value,
            "checkpoints": [item.as_document() for item in self.checkpoints],
        }

    @classmethod
    def from_document(cls, value: object) -> DatabaseTransitionV1:
        document = _mapping(value, where=DATABASE_TRANSITION_SCHEMA)
        _strict(
            document,
            where=DATABASE_TRANSITION_SCHEMA,
            known=set(_TRANSITION_FIELDS),
        )
        raw_checkpoints = document["checkpoints"]
        if not isinstance(raw_checkpoints, list):
            raise SpecError(f"{DATABASE_TRANSITION_SCHEMA}.checkpoints must be a list")
        return cls(
            transition_id=str(document["transition_id"]),
            target=str(document["target"]),
            plan_digest=str(document["plan_digest"]),
            from_descriptor_digest=str(document["from_descriptor_digest"]),
            to_descriptor_digest=str(document["to_descriptor_digest"]),
            durability=DatabaseDurability(str(document["durability"])),
            checkpoints=tuple(
                DatabaseCheckpointV1.from_document(item) for item in raw_checkpoints
            ),
        )


@dataclasses.dataclass(frozen=True, slots=True)
class DatabaseTransitionAuthorizationV1:
    """Control's authorization for the result, plan and target.

    Wave 7A authorizes ``to_descriptor_digest``.  The ``from`` digest is not a
    second authorization term: it is the independently observed live-state and
    compare-and-swap precondition.
    """

    target: str
    plan_digest: str
    to_descriptor_digest: str
    decision_ref: str
    authorized_at: str
    control_version: str

    def __post_init__(self) -> None:
        for name in ("target", "decision_ref", "authorized_at", "control_version"):
            object.__setattr__(
                self,
                name,
                _required(getattr(self, name), where=f"authorization.{name}"),
            )
        object.__setattr__(
            self,
            "plan_digest",
            _digest(self.plan_digest, where="authorization.plan_digest"),
        )
        object.__setattr__(
            self,
            "to_descriptor_digest",
            _digest(
                self.to_descriptor_digest,
                where="authorization.to_descriptor_digest",
            ),
        )

    def as_document(self) -> dict[str, str]:
        return dataclasses.asdict(self)

    @classmethod
    def from_document(cls, value: object) -> DatabaseTransitionAuthorizationV1:
        document = _mapping(value, where="DatabaseTransitionAuthorizationV1")
        known = {
            "target",
            "plan_digest",
            "to_descriptor_digest",
            "decision_ref",
            "authorized_at",
            "control_version",
        }
        _strict(document, where="DatabaseTransitionAuthorizationV1", known=known)
        return cls(
            target=str(document["target"]),
            plan_digest=str(document["plan_digest"]),
            to_descriptor_digest=str(document["to_descriptor_digest"]),
            decision_ref=str(document["decision_ref"]),
            authorized_at=str(document["authorized_at"]),
            control_version=str(document["control_version"]),
        )


class _GrantWitness:
    __slots__ = ()


_GRANTED: Final = _GrantWitness()


@dataclasses.dataclass(frozen=True, slots=True)
class DatabaseTransitionGrant:
    """A transition that passed the independent authorization binding."""

    witness: _GrantWitness
    transition: DatabaseTransitionV1
    authorization: DatabaseTransitionAuthorizationV1

    def __post_init__(self) -> None:
        if self.witness is not _GRANTED:
            raise PreconditionFailed(
                "a DatabaseTransitionGrant may only be produced by "
                "authorize_database_transition()"
            )


def authorize_database_transition(
    *,
    transition: DatabaseTransitionV1,
    authorization: DatabaseTransitionAuthorizationV1,
) -> DatabaseTransitionGrant:
    """Bind Control's decision to the exact result, plan and target."""
    if authorization.target != transition.target:
        raise PreconditionFailed(
            f"authorization names target {authorization.target!r}, not "
            f"{transition.target!r}"
        )
    if authorization.plan_digest != transition.plan_digest:
        raise PreconditionFailed(
            "authorization binds a different plan digest from this transition"
        )
    if authorization.to_descriptor_digest != transition.to_descriptor_digest:
        raise PreconditionFailed(
            "authorization binds a different result descriptor from this " "transition"
        )
    return DatabaseTransitionGrant(_GRANTED, transition, authorization)


@dataclasses.dataclass(frozen=True, slots=True)
class DatabasePreconditionV1:
    """Proof the transition was compared with its declared live starting state."""

    grant: DatabaseTransitionGrant
    observed_descriptor_digest: str


def require_database_precondition(
    grant: DatabaseTransitionGrant,
    observed_descriptor_digest: str,
) -> DatabasePreconditionV1:
    """Refuse unless live state is the transition's declared starting state."""
    observed = Digest.parse(
        observed_descriptor_digest, where="database precondition observation"
    )
    expected = Digest.parse(
        grant.transition.from_descriptor_digest,
        where="transition.from_descriptor_digest",
    )
    if observed != expected:
        raise PreconditionFailed(
            f"the live starting descriptor is {observed}, not the transition's "
            f"declared starting descriptor {expected}"
        )
    return DatabasePreconditionV1(grant, str(observed))


@dataclasses.dataclass(frozen=True, slots=True)
class DatabasePostconditionV1:
    observed_descriptor_digest: str
    observed_at: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "observed_descriptor_digest",
            _digest(
                self.observed_descriptor_digest,
                where="postcondition.observed_descriptor_digest",
            ),
        )
        object.__setattr__(
            self,
            "observed_at",
            _required(self.observed_at, where="postcondition.observed_at"),
        )

    def as_document(self) -> dict[str, str]:
        return dataclasses.asdict(self)

    @classmethod
    def from_document(cls, value: object) -> DatabasePostconditionV1:
        document = _mapping(value, where="DatabasePostconditionV1")
        known = {"observed_descriptor_digest", "observed_at"}
        _strict(document, where="DatabasePostconditionV1", known=known)
        return cls(
            observed_descriptor_digest=str(document["observed_descriptor_digest"]),
            observed_at=str(document["observed_at"]),
        )


@dataclasses.dataclass(frozen=True, slots=True)
class PromotionPendingV1:
    """The DB reached ``to`` but the accepted pointer has not yet promoted."""

    transition: DatabaseTransitionV1
    authorization: DatabaseTransitionAuthorizationV1
    postcondition: DatabasePostconditionV1

    def __post_init__(self) -> None:
        authorize_database_transition(
            transition=self.transition,
            authorization=self.authorization,
        )
        if (
            self.postcondition.observed_descriptor_digest
            != self.transition.to_descriptor_digest
        ):
            raise PreconditionFailed(
                "a promotion_pending record must carry the declared result as "
                "its observed database postcondition"
            )

    @property
    def state(self) -> str:
        return "promotion_pending"

    def as_document(self) -> dict[str, Any]:
        return {
            "schema": PROMOTION_PENDING_SCHEMA,
            "state": self.state,
            **self.transition.as_document(),
            "authorization": self.authorization.as_document(),
            "postcondition": self.postcondition.as_document(),
        }

    @classmethod
    def from_document(cls, value: object) -> PromotionPendingV1:
        document = _mapping(value, where=PROMOTION_PENDING_SCHEMA)
        transition_keys = set(_TRANSITION_FIELDS)
        known = transition_keys | {
            "schema",
            "state",
            "authorization",
            "postcondition",
        }
        _strict(document, where=PROMOTION_PENDING_SCHEMA, known=known)
        if (
            document["schema"] != PROMOTION_PENDING_SCHEMA
            or document["state"] != "promotion_pending"
        ):
            raise SpecError("the document is not a promotion_pending record")
        return cls(
            transition=DatabaseTransitionV1.from_document(
                {key: document[key] for key in transition_keys}
            ),
            authorization=DatabaseTransitionAuthorizationV1.from_document(
                document["authorization"]
            ),
            postcondition=DatabasePostconditionV1.from_document(
                document["postcondition"]
            ),
        )


def observe_database_postcondition(
    precondition: DatabasePreconditionV1,
    *,
    observed_descriptor_digest: str,
    observed_at: str,
) -> PromotionPendingV1:
    """Record the honest cross-system state after the database has committed."""
    grant = precondition.grant
    postcondition = DatabasePostconditionV1(observed_descriptor_digest, observed_at)
    if (
        postcondition.observed_descriptor_digest
        != grant.transition.to_descriptor_digest
    ):
        raise PreconditionFailed(
            "the observed database postcondition does not match the transition's "
            "declared result descriptor; descriptor promotion is refused"
        )
    return PromotionPendingV1(grant.transition, grant.authorization, postcondition)


@dataclasses.dataclass(frozen=True, slots=True)
class DescriptorPromotionEvidenceV1:
    """Evidence emitted by the owner of the accepted descriptor pointer."""

    transition_id: str
    target: str
    expected_descriptor_digest: str
    promoted_descriptor_digest: str
    observed_before_digest: str
    observed_after_digest: str
    event_ref: str
    promoted_at: str

    def __post_init__(self) -> None:
        for name in ("transition_id", "target", "event_ref", "promoted_at"):
            object.__setattr__(
                self, name, _required(getattr(self, name), where=f"promotion.{name}")
            )
        for name in (
            "expected_descriptor_digest",
            "promoted_descriptor_digest",
            "observed_before_digest",
            "observed_after_digest",
        ):
            object.__setattr__(
                self, name, _digest(getattr(self, name), where=f"promotion.{name}")
            )

    def as_document(self) -> dict[str, str]:
        return dataclasses.asdict(self)

    @classmethod
    def from_document(cls, value: object) -> DescriptorPromotionEvidenceV1:
        document = _mapping(value, where="DescriptorPromotionEvidenceV1")
        known = {field.name for field in dataclasses.fields(cls)}
        _strict(document, where="DescriptorPromotionEvidenceV1", known=known)
        return cls(
            transition_id=str(document["transition_id"]),
            target=str(document["target"]),
            expected_descriptor_digest=str(document["expected_descriptor_digest"]),
            promoted_descriptor_digest=str(document["promoted_descriptor_digest"]),
            observed_before_digest=str(document["observed_before_digest"]),
            observed_after_digest=str(document["observed_after_digest"]),
            event_ref=str(document["event_ref"]),
            promoted_at=str(document["promoted_at"]),
        )


class DescriptorPromoter(Protocol):
    """Port owned by the accepted-descriptor registry.

    Implementations atomically compare-and-swap the pointer and durably record
    the event.  Calls are idempotent by ``transition_id``: after a crash that
    followed the swap but preceded receipt persistence, the retry returns the
    original event evidence.  Merely observing that the pointer now equals
    ``to`` is not promotion evidence and must not be returned as though it were.
    """

    def compare_and_swap(
        self,
        *,
        transition_id: str,
        target: str,
        expected_descriptor_digest: str,
        promoted_descriptor_digest: str,
    ) -> DescriptorPromotionEvidenceV1: ...


@dataclasses.dataclass(frozen=True, slots=True)
class DatabaseTransitionReceiptV1:
    """Terminal evidence that the DB result and descriptor promotion agree."""

    pending: PromotionPendingV1
    promotion: DescriptorPromotionEvidenceV1

    def __post_init__(self) -> None:
        _require_exact_promotion(self.pending, self.promotion)

    @property
    def state(self) -> str:
        return "promoted"

    @property
    def from_descriptor_digest(self) -> str:
        return self.pending.transition.from_descriptor_digest

    @property
    def to_descriptor_digest(self) -> str:
        return self.pending.transition.to_descriptor_digest

    @property
    def postcondition(self) -> DatabasePostconditionV1:
        return self.pending.postcondition

    def as_document(self) -> dict[str, Any]:
        return {
            "schema": DATABASE_TRANSITION_RECEIPT_SCHEMA,
            "state": self.state,
            **self.pending.transition.as_document(),
            "authorization": self.pending.authorization.as_document(),
            "postcondition": self.pending.postcondition.as_document(),
            "promotion": self.promotion.as_document(),
        }

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.as_document(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")

    def sha256_digest(self) -> str:
        return "sha256:" + hashlib.sha256(self.canonical_bytes()).hexdigest()

    @classmethod
    def from_document(cls, value: object) -> DatabaseTransitionReceiptV1:
        document = _mapping(value, where=DATABASE_TRANSITION_RECEIPT_SCHEMA)
        transition_keys = set(_TRANSITION_FIELDS)
        known = transition_keys | {
            "schema",
            "state",
            "authorization",
            "postcondition",
            "promotion",
        }
        _strict(document, where=DATABASE_TRANSITION_RECEIPT_SCHEMA, known=known)
        if (
            document["schema"] != DATABASE_TRANSITION_RECEIPT_SCHEMA
            or document["state"] != "promoted"
        ):
            raise SpecError(
                "the document is not a promoted database transition receipt"
            )
        pending = PromotionPendingV1(
            transition=DatabaseTransitionV1.from_document(
                {key: document[key] for key in transition_keys}
            ),
            authorization=DatabaseTransitionAuthorizationV1.from_document(
                document["authorization"]
            ),
            postcondition=DatabasePostconditionV1.from_document(
                document["postcondition"]
            ),
        )
        promotion = DescriptorPromotionEvidenceV1.from_document(document["promotion"])
        _require_exact_promotion(pending, promotion)
        return cls(pending, promotion)


def _require_exact_promotion(
    pending: PromotionPendingV1, evidence: DescriptorPromotionEvidenceV1
) -> None:
    transition = pending.transition
    comparisons = (
        ("transition id", evidence.transition_id, transition.transition_id),
        ("target", evidence.target, transition.target),
        (
            "expected descriptor",
            evidence.expected_descriptor_digest,
            transition.from_descriptor_digest,
        ),
        (
            "promoted descriptor",
            evidence.promoted_descriptor_digest,
            transition.to_descriptor_digest,
        ),
        (
            "observed before",
            evidence.observed_before_digest,
            transition.from_descriptor_digest,
        ),
        (
            "observed after",
            evidence.observed_after_digest,
            transition.to_descriptor_digest,
        ),
    )
    for label, observed, expected in comparisons:
        if observed != expected:
            raise PreconditionFailed(
                f"descriptor promotion {label} is {observed!r}, expected {expected!r}"
            )


def promote_database_descriptor(
    pending: PromotionPendingV1,
    promoter: DescriptorPromoter,
) -> DatabaseTransitionReceiptV1:
    """CAS the accepted pointer and build terminal evidence on success."""
    transition = pending.transition
    evidence = promoter.compare_and_swap(
        transition_id=transition.transition_id,
        target=transition.target,
        expected_descriptor_digest=transition.from_descriptor_digest,
        promoted_descriptor_digest=transition.to_descriptor_digest,
    )
    _require_exact_promotion(pending, evidence)
    return DatabaseTransitionReceiptV1(pending, evidence)


def recover_database_promotion(
    pending: PromotionPendingV1,
    promoter: DescriptorPromoter,
) -> DatabaseTransitionReceiptV1:
    """Recover ``promotion_pending`` by re-driving the same idempotent CAS.

    This deliberately does not infer success from the current pointer.  The
    promoter must return the durable event for this ``transition_id``, whether
    the retry performs the swap now or retrieves the event written before a
    crash.
    """
    return promote_database_descriptor(pending, promoter)
