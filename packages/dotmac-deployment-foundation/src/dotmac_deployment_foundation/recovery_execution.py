"""The restore EXECUTOR — the half `recovery.py` deliberately does not have.

## What existed, and why it could not recover anything

`recovery.py` is a complete and careful DATA contract: a bundle manifest with
per-component digests, role-closure derivation, catalog diffing
(:func:`verify_recovery`), a restore adjudicator that refuses a partial target,
and a value-free receipt. It runs no `pg_restore`, opens no socket and reads no
credential, and its own docstring says so — correctly, because those are
decisions about evidence and they must not depend on who is holding a database
handle.

What nothing owned was the other half. :data:`RESTORE_PROCEDURE`'s ten steps
are :class:`RestoreStepSpec` **text**, consumed by `cmd_recovery_bundle`'s print
loop; the deployment `Effects` protocol has twenty-four methods and not one of
them restores anything. So a Foundation could describe a recovery in exact
detail and could not perform one — the same shape as
`ExecutionPlanDigestV1` before a5: a mechanism built, tested, and unreachable
from anything that touches a host.

This module is the executor. It decides nothing that `recovery.py` already
decides: it DRIVES the ten steps through an injected seam, hands the measured
facts back to `adjudicate_restore` and `verify_recovery`, and obeys their
verdicts.

## Why a SEPARATE seam from `Effects`

A deployment's `Effects` mutates the product host. A restore targets a fresh,
isolated cluster that must not be the product's — step 1 exists precisely to
establish that. Putting `restore_objects` on the deployment protocol would put
a database-destroying capability on the object every deploy already holds, and
"the executor happened not to call it" is not a boundary. `RecoveryEffects` is
its own protocol for the same reason `ExposureHostEffects` is.

## What this module IS reachable as, and what it is NOT

This docstring previously asserted three things about its own unreachability —
no CLI subcommand, no authorization binding, no `recover` member — and the
commit that added the executor made the third false while leaving the sentence
in place. A module that misdescribes its own reachability is how an executor
comes to sit outside the authorization chain without anyone noticing, so what
follows states what is TRUE rather than deleting what became false.

**Reachable, as `dotmac-deploy restore-rehearsal --execute`.** The assembly
supplies a :class:`RecoverySession` through its execution-bindings entry point
(`build_recovery_session`), because everything this executor needs beyond the
descriptor is something only the assembly can produce: a `RecoveryEffects` that
can create and destroy a cluster, the bundle bytes, and the SOURCE
`CatalogEvidence` captured when the backup was taken. This facility parses no
bundle bytes and holds no database driver, by design — see "Why the source
evidence is not read here" below.

**NOT reachable as an authorized operation, and that is the boundary.** There is
no `recover` member in `authorization.OPERATIONS`; it was added in a6 and
withdrawn in the same release, with the reasoning recorded at that constant.
This executor performs a restore REHEARSAL: step 1 creates a fresh, isolated
cluster that must not be the product's, and the adjudicator's terminal verdict
destroys it.

## The premise for having no `ExecutionGrant`, stated so it can be checked

An `ExecutionGrant` exists because `Executor` mutates the product host. This
executor cannot: it acts only on a target IT created through
`create_fresh_target`, and `RecoveryEffects` has no method that names, reaches
or mutates a running deployment. That is an enforceable premise rather than an
exemption — the protocol is the enforcement, and
`test_deployment_foundation_recovery_execution.py` asserts the surface stays
that way, so widening `RecoveryEffects` to touch a product host fails there and
sends the reader here.

What that premise does NOT cover is a recovery of a failed production system.
That act needs a captured prestate, the failed system's own observed state and a
desired poststate; it mutates something that already exists; and it therefore
needs an authorization binding, a replay coordinate and a signed, settleable
result. It is `0.4.0a1`'s subject, with its own `RecoveryExecutionPlanV1`,
because a deployment-shaped plan is not a recovery plan.

Note what that sentence does NOT say, since an earlier draft did: it does not say
`recover` returns to `authorization.OPERATIONS` when the capability lands. The
counterparty vocabulary divergence is Control's to repair, and the constant
carries the measurement.

## Why the source evidence is not read here

`verify_recovery` compares the restored catalogue against the SOURCE catalogue
as captured. The manifest deliberately carries digests and counts, not facts —
that value-free split is what lets this module run with no database and no
credential — so a `CatalogEvidence` cannot be reconstructed from it, and there
is no deserializer for one anywhere in this package.

The facts themselves are in the BUNDLE, as the components the manifest digests.
The assembly already holds those bytes and is the only thing that reads them, so
it is the only correct supplier. Reaching instead for a fourteenth
`BundleComponent` carrying serialized catalogue facts is the turn a later reader
will take first, and it is wrong twice: it changes a closed vocabulary, and it
puts parsed facts inside the very document whose value-free-ness this module
depends on.

(`BundleComponent` has thirteen members. So does `ApplicationFoundationProfile`'s
concern set. They are unrelated vocabularies and must never be conflated — and
these thirteen ARE the fact set the verification registry compares.)
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from typing import Any, Final, Protocol, runtime_checkable

from .errors import PreconditionFailed, StepFailed
from .recovery import (
    RESTORE_PROCEDURE,
    Adjudication,
    CatalogEvidence,
    RecoveryBundleManifestV1,
    RestoreAttempt,
    RestoreStep,
    adjudicate_restore,
    restore_plan,
    verify_recovery,
)

__all__ = [
    "SESSION_ABSENT",
    "SESSION_EFFECTS_INVALID",
    "SESSION_EVIDENCE_INVALID",
    "SESSION_IMAGE_MISSING",
    "RecoveryEffects",
    "RecoverySession",
    "RecoveryExecutor",
    "RecoveryOutcome",
    "RestoreTarget",
]


#: Stable identifiers for this module's refusals. Assert these, read the prose.
SESSION_EFFECTS_INVALID: Final = "recovery.session.effects_invalid"
SESSION_EVIDENCE_INVALID: Final = "recovery.session.evidence_invalid"
SESSION_IMAGE_MISSING: Final = "recovery.session.image_missing"
SESSION_ABSENT: Final = "recovery.session.absent"


@dataclasses.dataclass(frozen=True, slots=True)
class RecoverySession:
    """Everything the executor needs that only the ASSEMBLY can produce.

    One object rather than four CLI flags, because these four are meaningless
    apart: effects that can reach a cluster, the bundle bytes, the source
    catalogue those bytes were captured from, and the image the restore must be
    proved able to run. A caller holding three of them has nothing.

    There is no default and no empty `CatalogEvidence` fallback. An empty source
    catalogue compares clean against an empty restored one, so a defaulted
    session is a rehearsal that proves a database was created — the exact shape
    of "a check that passes because it looked at nothing".
    """

    effects: RecoveryEffects
    source_evidence: CatalogEvidence
    product_image: str
    bundle: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.effects, RecoveryEffects):
            raise PreconditionFailed(
                "RecoverySession.effects does not implement RecoveryEffects; "
                f"got {type(self.effects).__name__}. The typed protocol is the "
                "contract, and a look-alike is what it exists to refuse",
                code=SESSION_EFFECTS_INVALID,
            )
        if not isinstance(self.source_evidence, CatalogEvidence):
            raise PreconditionFailed(
                "RecoverySession.source_evidence must be a CatalogEvidence "
                f"captured from the SOURCE, got "
                f"{type(self.source_evidence).__name__}",
                code=SESSION_EVIDENCE_INVALID,
            )
        if not str(self.product_image).strip():
            raise PreconditionFailed(
                "RecoverySession.product_image is empty; step 9 starts the "
                "real application against the restored database, and a restore "
                "nothing has started the application against is a copy",
                code=SESSION_IMAGE_MISSING,
            )


@dataclasses.dataclass(frozen=True, slots=True)
class RestoreTarget:
    """A handle to the isolated cluster step 1 created.

    Opaque on purpose: this facility never builds a connection string, and a
    handle carrying one would put a credential in every log line that reports
    a step. The provider knows what it means; this module only passes it back.
    """

    identifier: str
    major_version: int

    def __post_init__(self) -> None:
        if not str(self.identifier).strip():
            raise PreconditionFailed(
                "a restore target with no identifier cannot be destroyed, and "
                "the adjudicator's DESTROY verdict is the one thing that must "
                "always be actionable"
            )


@runtime_checkable
class RecoveryEffects(Protocol):
    """Everything the restore executor can do to a recovery target.

    One method per step of :data:`RESTORE_PROCEDURE` that has a host effect,
    plus the observations the pure adjudicators consume. Every method returns a
    fact or raises; none of them decides anything, for the same reason the
    deployment `Effects` does not.
    """

    # ── step 1 ──
    def create_fresh_target(self, *, major_version: int) -> RestoreTarget: ...

    # ── steps 2 and 3 ──
    def restore_roles(
        self, target: RestoreTarget, *, bundle: Mapping[str, Any]
    ) -> RestoreAttempt: ...

    def restore_objects(
        self, target: RestoreTarget, *, bundle: Mapping[str, Any]
    ) -> RestoreAttempt: ...

    # ── step 4's actuator: the verdict must always be performable ──
    def destroy_target(self, target: RestoreTarget) -> None: ...

    # ── step 5 ──
    def install_login_material(self, target: RestoreTarget) -> None: ...

    # ── steps 6, 7 and 8 read the restored catalog ──
    def observe_catalog(self, target: RestoreTarget) -> CatalogEvidence: ...

    def observe_plane_isolation(self, target: RestoreTarget) -> Sequence[Any]: ...

    # ── step 9 ──
    def start_product_image(self, target: RestoreTarget, *, image: str) -> bool: ...


@dataclasses.dataclass(slots=True)
class RecoveryOutcome:
    """What the restore did, in enough detail to be the evidence artefact."""

    target: RestoreTarget | None = None
    steps_completed: tuple[str, ...] = ()
    attempt: RestoreAttempt | None = None
    adjudication: Adjudication | None = None
    findings: tuple[str, ...] = ()
    destroyed: bool = False
    proved: bool = False
    failure: str = ""

    def as_evidence(self) -> dict[str, object]:
        return {
            "schema": RECOVERY_EXECUTION_SCHEMA,
            "target": self.target.identifier if self.target else "",
            "major_version": self.target.major_version if self.target else 0,
            "steps_completed": list(self.steps_completed),
            "exit_status": self.attempt.exit_status if self.attempt else None,
            "disposition": (
                self.adjudication.disposition.value if self.adjudication else ""
            ),
            "adjudication_reasons": (
                list(self.adjudication.reasons) if self.adjudication else []
            ),
            "findings": list(self.findings),
            "destroyed": self.destroyed,
            "proved": self.proved,
            "failure": self.failure,
        }


RECOVERY_EXECUTION_SCHEMA: Final = "RecoveryExecution.v1"


class RecoveryExecutor:
    """Drives :data:`RESTORE_PROCEDURE` against a `RecoveryEffects`.

    The ordering is the procedure's, not this class's — `restore_plan` returns
    it and the executor walks it, so a step added to the contract cannot be
    silently skipped here.
    """

    def __init__(
        self,
        spec: Any,
        manifest: RecoveryBundleManifestV1,
        effects: RecoveryEffects,
        *,
        source_evidence: CatalogEvidence,
        product_image: str,
    ) -> None:
        self._spec = spec
        self._manifest = manifest
        self._effects = effects
        self._source = source_evidence
        self._image = str(product_image)
        if not self._image.strip():
            raise PreconditionFailed(
                "a recovery with no product image cannot perform step 9, and a "
                "restore nothing has started the real application against is a "
                "database that was copied rather than a system that recovered"
            )

    def run(self, bundle: Mapping[str, Any]) -> RecoveryOutcome:
        """Restore, adjudicate, prove — or destroy and say why."""
        outcome = RecoveryOutcome()
        # The plan is asked for FIRST and from the contract, so a bundle that
        # does not match the descriptor refuses before a cluster exists.
        procedure = restore_plan(self._spec, self._manifest)
        if tuple(step.step for step in procedure) != tuple(
            step.step for step in RESTORE_PROCEDURE
        ):  # pragma: no cover - contract drift canary
            raise PreconditionFailed(
                "the restore plan is not the declared procedure; this executor "
                "walks the contract rather than a copy of it"
            )

        completed: list[str] = []
        try:
            for spec in procedure:
                self._dispatch(spec.step, bundle, outcome, completed)
                completed.append(spec.step.value)
        except StepFailed as exc:
            outcome.failure = str(exc)
        except PreconditionFailed as exc:
            outcome.failure = str(exc)
        outcome.steps_completed = tuple(completed)
        return outcome

    # ── the ten steps ───────────────────────────────────────────────────────

    def _dispatch(
        self,
        step: RestoreStep,
        bundle: Mapping[str, Any],
        outcome: RecoveryOutcome,
        completed: list[str],
    ) -> None:
        handler = getattr(self, f"_do_{step.value}")
        handler(bundle, outcome)

    def _do_fresh_target(
        self, bundle: Mapping[str, Any], outcome: RecoveryOutcome
    ) -> None:
        # `postgres_major` is the manifest's own accessor. The step refuses a
        # cluster at a different major, so the number comes from the BUNDLE and
        # never from the descriptor: a restore across majors is a migration
        # wearing a recovery's clothes, and the bundle is what was captured.
        outcome.target = self._effects.create_fresh_target(
            major_version=self._manifest.postgres_major
        )

    def _do_restore_roles(
        self, bundle: Mapping[str, Any], outcome: RecoveryOutcome
    ) -> None:
        outcome.attempt = self._effects.restore_roles(
            self._require_target(outcome), bundle=bundle
        )

    def _do_restore_objects(
        self, bundle: Mapping[str, Any], outcome: RecoveryOutcome
    ) -> None:
        outcome.attempt = self._effects.restore_objects(
            self._require_target(outcome), bundle=bundle
        )

    def _do_adjudicate(
        self, bundle: Mapping[str, Any], outcome: RecoveryOutcome
    ) -> None:
        """THE refusal, and the one that must always be actionable.

        `adjudicate_restore` owns the verdict; this obeys it. A DESTROY is
        performed HERE, before anything inspects the target — the measured
        Vendor CP failure left 45 tables, 23 policies and 16 RLS-enabled tables
        behind after exiting 1, and the next reader would have found a database
        that passes a table count.
        """
        attempt = outcome.attempt
        if attempt is None:  # pragma: no cover - ordering canary
            raise PreconditionFailed("nothing was restored, so nothing can be judged")
        outcome.adjudication = adjudicate_restore(attempt)
        if outcome.adjudication.must_destroy:
            self._effects.destroy_target(self._require_target(outcome))
            outcome.destroyed = True
            raise StepFailed(
                RestoreStep.ADJUDICATE.value,
                "the restore was adjudicated DESTROY and the target has been "
                "destroyed: " + "; ".join(outcome.adjudication.reasons),
            )

    def _do_install_login_material(
        self, bundle: Mapping[str, Any], outcome: RecoveryOutcome
    ) -> None:
        self._effects.install_login_material(self._require_target(outcome))

    def _do_prove_catalog(
        self, bundle: Mapping[str, Any], outcome: RecoveryOutcome
    ) -> None:
        restored = self._effects.observe_catalog(self._require_target(outcome))
        findings = verify_recovery(
            manifest=self._manifest, source=self._source, restored=restored
        )
        if findings:
            outcome.findings = tuple(findings)
            raise StepFailed(
                RestoreStep.PROVE_CATALOG.value,
                f"the restored catalog differs from the bundle in "
                f"{len(findings)} way(s): " + "; ".join(findings[:5]),
            )

    def _do_prove_plane_isolation(
        self, bundle: Mapping[str, Any], outcome: RecoveryOutcome
    ) -> None:
        target = self._require_target(outcome)
        restored = self._effects.observe_catalog(target)
        findings = verify_recovery(
            manifest=self._manifest,
            source=self._source,
            restored=restored,
            isolation=self._effects.observe_plane_isolation(target),
        )
        if findings:
            outcome.findings = tuple(findings)
            raise StepFailed(
                RestoreStep.PROVE_PLANE_ISOLATION.value,
                "plane isolation is not proved on the restored target: "
                + "; ".join(findings[:5]),
            )

    def _do_prove_revocations(
        self, bundle: Mapping[str, Any], outcome: RecoveryOutcome
    ) -> None:
        # Same observation, different question — the revocation half of
        # `verify_recovery`'s findings is already covered by the catalog proof
        # above; this step exists in the procedure so a reader sees it, and it
        # re-reads rather than trusting the earlier read.
        restored = self._effects.observe_catalog(self._require_target(outcome))
        findings = verify_recovery(
            manifest=self._manifest, source=self._source, restored=restored
        )
        if findings:
            outcome.findings = tuple(findings)
            raise StepFailed(
                RestoreStep.PROVE_REVOCATIONS.value,
                "declared revocations are not proved on the restored target: "
                + "; ".join(findings[:5]),
            )

    def _do_start_product_image(
        self, bundle: Mapping[str, Any], outcome: RecoveryOutcome
    ) -> None:
        ready = self._effects.start_product_image(
            self._require_target(outcome), image=self._image
        )
        if not ready:
            raise StepFailed(
                RestoreStep.START_PRODUCT_IMAGE.value,
                f"the product image {self._image} did not become ready against "
                "the restored database. A database that restores and cannot run "
                "the application is a copy, not a recovery",
            )

    def _do_emit_receipt(
        self, bundle: Mapping[str, Any], outcome: RecoveryOutcome
    ) -> None:
        outcome.proved = True

    # ── helpers ─────────────────────────────────────────────────────────────

    def _require_target(self, outcome: RecoveryOutcome) -> RestoreTarget:
        if outcome.target is None:  # pragma: no cover - ordering canary
            raise PreconditionFailed(
                "no restore target exists; step 1 creates it and every later "
                "step needs it"
            )
        return outcome.target
