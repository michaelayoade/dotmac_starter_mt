"""Canonical evidence, the injected signer port, and the freshness repair.

Three things are proven here, each with a plant/near-miss pair per ADR-0018's
proof standard:

1. `rebuild_projections` reproduces the classification an observation was
   ACCEPTED under, not whatever policy is live when the rebuild runs
   (`test_a_policy_change_does_not_reclassify_an_unchanged_observation`).
   Before the repair, both `record_observation` and `rebuild_projections`
   read `component.freshness_seconds` — the LIVE value — to compute
   `freshness_deadline`. Reading the code prior to this change: a
   `register_component` call narrowing `freshness_seconds` between an
   observation's acceptance and a later `rebuild_projections` call would
   change `freshness_deadline` for that already-accepted observation, because
   `rebuild_projections` re-derived it from `component.freshness_seconds`
   (current) rather than the value in effect at acceptance. This test is
   exactly that scenario and would have failed against that code: it accepts
   an observation under `freshness_seconds=600` (nowhere near expiry at
   `as_of`), narrows the policy to `freshness_seconds=1`, rebuilds, and
   asserts the projection is STILL fresh — which only holds if the rebuild
   derives the deadline from the observation's own frozen snapshot.
2. `canonical_health_evidence_bytes` is byte-stable across processes and
   `PYTHONHASHSEED` values, and a single-field mutation changes the bytes
   (non-vacuity: a signer that ignored its input and signed a constant would
   pass a "signature field is present" test but fail
   `test_authoritative_signing_uses_durable_observations`).
3. authoritative signed evidence refuses without an injected signer, and
   holds no default implementation anywhere in this package.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from dotmac_kernel.models import Base
from dotmac_platform_health import (
    ComponentEvidence,
    DeploymentHealthEvidence,
    HealthError,
    HealthEvidenceError,
    HealthEvidenceSignature,
    HealthObservationInput,
    HealthState,
    build_health_evidence,
    canonical_health_evidence_bytes,
    produce_signed_health_evidence,
    rebuild_projections,
    record_observation,
    register_component,
    summarize_health,
)
from dotmac_platform_health.models import PLATFORM_MODELS, HealthProjection
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "packages/dotmac-platform-health/src/dotmac_platform_health"


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_health": None}},
    )
    Base.metadata.create_all(engine, tables=[m.__table__ for m in PLATFORM_MODELS])
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


# ── 1. The freshness-policy repair ──────────────────────────────────────────


def test_a_policy_change_does_not_reclassify_an_unchanged_observation(
    db: Session,
) -> None:
    """PLANT/repair proof, named: this is the exact scenario the pre-repair
    code got wrong (see module docstring)."""
    register_component(db, code="api", display_name="API", freshness_seconds=600)
    at = datetime(2026, 9, 7, 8, 0, tzinfo=UTC)
    record_observation(
        db,
        HealthObservationInput(
            "agent", "api:1", "api", HealthState.HEALTHY, at, at, "ok", {}
        ),
    )
    as_of = at + timedelta(seconds=90)
    before = summarize_health(db, as_of=as_of)
    assert before[0].freshness == "fresh"

    # Narrow the policy AFTER acceptance — this alone must not touch the
    # already-recorded observation's classification.
    register_component(db, code="api", display_name="API", freshness_seconds=1)
    rebuild_projections(db, rebuilt_at=as_of)
    after = summarize_health(db, as_of=as_of)

    assert after[0].freshness == "fresh", (
        "a policy change after acceptance reclassified an unchanged "
        "observation on rebuild — freshness_deadline must be derived from "
        "the observation's own frozen freshness_seconds snapshot"
    )
    assert after[0].state == before[0].state == "healthy"


def test_near_miss_a_rebuild_with_no_policy_change_stays_silent(
    db: Session,
) -> None:
    """NEGATIVE CONTROL: a rebuild that changes nothing about the policy must
    reproduce byte-identical classification — proves the repair did not
    simply freeze deadlines forever, only pin them to the accepting policy."""
    register_component(db, code="api", display_name="API", freshness_seconds=60)
    at = datetime(2026, 9, 7, 8, 0, tzinfo=UTC)
    record_observation(
        db,
        HealthObservationInput(
            "agent", "api:1", "api", HealthState.HEALTHY, at, at, "ok", {}
        ),
    )
    as_of = at + timedelta(seconds=90)
    before = summarize_health(db, as_of=as_of)
    rebuild_projections(db, rebuilt_at=as_of)
    after = summarize_health(db, as_of=as_of)
    assert before == after


def test_the_observation_carries_its_own_freshness_snapshot(db: Session) -> None:
    register_component(db, code="api", display_name="API", freshness_seconds=42)
    at = datetime(2026, 9, 7, 8, 0, tzinfo=UTC)
    receipt = record_observation(
        db,
        HealthObservationInput(
            "agent", "api:1", "api", HealthState.HEALTHY, at, at, "ok", {}
        ),
    )
    assert receipt.observation.freshness_seconds == 42
    register_component(db, code="api", display_name="API", freshness_seconds=999)
    # The already-accepted observation's snapshot is untouched by the later
    # policy change.
    assert receipt.observation.freshness_seconds == 42


def test_rebuild_refuses_legacy_unknown_provenance_before_projection_delete(
    db: Session,
) -> None:
    register_component(db, code="api", display_name="API", freshness_seconds=60)
    at = datetime(2026, 9, 7, 8, 0, tzinfo=UTC)
    receipt = record_observation(
        db,
        HealthObservationInput(
            "agent", "api:1", "api", HealthState.HEALTHY, at, at, "ok", {}
        ),
    )
    projection = db.scalar(select(HealthProjection))
    receipt.observation.freshness_seconds = None
    with pytest.raises(HealthError, match="freshness provenance"):
        rebuild_projections(db, rebuilt_at=at + timedelta(seconds=1))
    assert db.scalar(select(HealthProjection)).id == projection.id


def test_evidence_refuses_selected_projection_without_freshness_provenance(
    db: Session,
) -> None:
    register_component(db, code="api", display_name="API", freshness_seconds=60)
    at, as_of = _evidence_now()
    receipt = record_observation(
        db,
        HealthObservationInput(
            "agent", "api:1", "api", HealthState.HEALTHY, at, at, "ok", {}
        ),
    )
    receipt.observation.freshness_seconds = None
    db.flush()
    with pytest.raises(HealthError, match="freshness provenance"):
        build_health_evidence(db, requested_components=("api",), evaluated_at=as_of)


def test_summary_hides_legacy_health_and_freshness(db: Session) -> None:
    register_component(db, code="api", display_name="API", freshness_seconds=60)
    at, as_of = _evidence_now()
    legacy = record_observation(
        db,
        HealthObservationInput(
            "agent", "api:legacy", "api", HealthState.HEALTHY, at, at, "ok", {}
        ),
    )
    legacy.observation.freshness_seconds = None
    db.flush()
    summary = summarize_health(db, as_of=as_of)[0]
    assert (summary.state, summary.freshness, summary.summary) == (
        HealthState.UNKNOWN.value,
        "missing",
        None,
    )


def test_legacy_signing_refusal_does_not_call_signer(db: Session) -> None:
    register_component(db, code="api", display_name="API", freshness_seconds=60)
    at, as_of = _evidence_now()
    legacy = record_observation(
        db,
        HealthObservationInput(
            "agent", "api:legacy", "api", HealthState.HEALTHY, at, at, "ok", {}
        ),
    )
    legacy.observation.freshness_seconds = None

    class _ShouldNotBeCalled:
        def sign_health_evidence(self, evidence, canonical_bytes):
            raise AssertionError("legacy evidence reached signer")

    with pytest.raises(HealthError, match="freshness provenance"):
        produce_signed_health_evidence(
            db,
            requested_components=("api",),
            evaluated_at=as_of,
            signer=_ShouldNotBeCalled(),
        )


def test_newer_observation_repairs_legacy_provenance_refusal(db: Session) -> None:
    register_component(db, code="api", display_name="API", freshness_seconds=60)
    at, as_of = _evidence_now()
    legacy = record_observation(
        db,
        HealthObservationInput(
            "agent", "api:legacy", "api", HealthState.HEALTHY, at, at, "old", {}
        ),
    )
    legacy.observation.freshness_seconds = None
    db.flush()
    with pytest.raises(HealthError, match="freshness provenance"):
        rebuild_projections(db, rebuilt_at=as_of)

    # Later receipt does not make an earlier observation the selected latest
    # fact, so the refusal remains in force.
    record_observation(
        db,
        HealthObservationInput(
            "agent",
            "api:late-receipt",
            "api",
            HealthState.HEALTHY,
            at - timedelta(seconds=1),
            at + timedelta(seconds=2),
            "old",
            {},
        ),
    )
    with pytest.raises(HealthError, match="freshness provenance"):
        rebuild_projections(db, rebuilt_at=as_of)

    record_observation(
        db,
        HealthObservationInput(
            "agent",
            "api:new",
            "api",
            HealthState.HEALTHY,
            at + timedelta(seconds=1),
            at + timedelta(seconds=1),
            "new",
            {},
        ),
    )
    rebuild_projections(db, rebuilt_at=as_of)
    evidence = build_health_evidence(
        db, requested_components=("api",), evaluated_at=as_of
    )
    assert evidence.components[0].freshness == "fresh"


# ── 2. Canonical evidence: determinism, roster exactness, valid_until ──────


def _evidence_now() -> tuple[datetime, datetime]:
    at = datetime(2026, 9, 7, 8, 0, tzinfo=UTC)
    return at, at + timedelta(seconds=30)


def test_build_health_evidence_represents_missing_entries_explicitly(
    db: Session,
) -> None:
    register_component(db, code="api", display_name="API", freshness_seconds=60)
    register_component(
        db, code="registered-empty", display_name="Empty", freshness_seconds=60
    )
    at, as_of = _evidence_now()
    record_observation(
        db,
        HealthObservationInput(
            "agent", "api:1", "api", HealthState.HEALTHY, at, at, "ok", {}
        ),
    )
    evidence = build_health_evidence(
        db,
        requested_components=("api", "registered-empty", "never-registered"),
        evaluated_at=as_of,
    )
    assert [c.component_code for c in evidence.components] == [
        "api",
        "registered-empty",
        "never-registered",
    ]
    for missing in evidence.components[1:]:
        assert missing.state == HealthState.UNKNOWN.value
        assert missing.freshness == "missing"
        assert missing.observation_id is None
        assert missing.observed_at is None


def test_build_health_evidence_roster_is_exact_not_every_active_component(
    db: Session,
) -> None:
    register_component(db, code="api", display_name="API", freshness_seconds=60)
    register_component(db, code="worker", display_name="Worker", freshness_seconds=60)
    at, as_of = _evidence_now()
    for code in ("api", "worker"):
        record_observation(
            db,
            HealthObservationInput(
                "agent", f"{code}:1", code, HealthState.HEALTHY, at, at, "ok", {}
            ),
        )
    evidence = build_health_evidence(
        db, requested_components=("api",), evaluated_at=as_of
    )
    assert [c.component_code for c in evidence.components] == ["api"]


def test_build_health_evidence_rejects_empty_or_repeated_roster(db: Session) -> None:
    at, as_of = _evidence_now()
    with pytest.raises(ValueError, match="non-empty"):
        build_health_evidence(db, requested_components=(), evaluated_at=as_of)
    with pytest.raises(ValueError, match="repeat"):
        build_health_evidence(
            db, requested_components=("api", "api"), evaluated_at=as_of
        )


def test_valid_until_is_the_earliest_included_deadline(db: Session) -> None:
    register_component(db, code="api", display_name="API", freshness_seconds=100)
    register_component(db, code="worker", display_name="Worker", freshness_seconds=10)
    at, as_of = _evidence_now()
    for code in ("api", "worker"):
        record_observation(
            db,
            HealthObservationInput(
                "agent", f"{code}:1", code, HealthState.HEALTHY, at, at, "ok", {}
            ),
        )
    evidence = build_health_evidence(
        db, requested_components=("api", "worker"), evaluated_at=as_of
    )
    assert evidence.valid_until == at + timedelta(seconds=10)


def test_valid_until_is_the_evaluation_instant_when_nothing_has_a_deadline(
    db: Session,
) -> None:
    at, as_of = _evidence_now()
    evidence = build_health_evidence(
        db, requested_components=("never-registered",), evaluated_at=as_of
    )
    assert evidence.valid_until == as_of


# ── Determinism: byte-identical across order, process and hash seed ────────


def _sample_evidence() -> DeploymentHealthEvidence:
    return DeploymentHealthEvidence(
        evaluated_at=datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
        valid_until=datetime(2026, 9, 7, 12, 5, tzinfo=UTC),
        components=(
            ComponentEvidence("worker", None, None, "unknown", "missing"),
            ComponentEvidence(
                "api",
                None,
                datetime(2026, 9, 7, 11, 59, tzinfo=UTC),
                "healthy",
                "fresh",
            ),
        ),
    )


def test_canonical_bytes_are_order_independent() -> None:
    forward = _sample_evidence()
    reversed_components = DeploymentHealthEvidence(
        evaluated_at=forward.evaluated_at,
        valid_until=forward.valid_until,
        components=tuple(reversed(forward.components)),
    )
    assert canonical_health_evidence_bytes(forward) == canonical_health_evidence_bytes(
        reversed_components
    )


def test_canonical_bytes_are_self_describing_and_plain_json() -> None:
    import json

    payload = json.loads(canonical_health_evidence_bytes(_sample_evidence()))
    assert payload["schema"] == "DeploymentHealthEvidence.v1"
    assert isinstance(payload["components"], list)
    # Readable with the standard library ONLY — no dotmac_platform_health
    # import anywhere in this assertion, matching the ADR-0070 amendment's
    # "Foundation imports neither Platform Health, Control nor Integrator".


def test_a_one_field_mutation_changes_canonical_bytes() -> None:
    """A one-field fact change must change canonical bytes."""
    base = _sample_evidence()
    mutated = DeploymentHealthEvidence(
        evaluated_at=base.evaluated_at,
        valid_until=base.valid_until,
        components=(
            base.components[0],
            ComponentEvidence(
                base.components[1].component_code,
                base.components[1].observation_id,
                base.components[1].observed_at,
                "degraded",
                base.components[1].freshness,
            ),
        ),
    )
    assert canonical_health_evidence_bytes(base) != canonical_health_evidence_bytes(
        mutated
    )


def test_authoritative_signing_uses_durable_observations(db: Session) -> None:
    """The signer receives exact bytes built from durable state."""
    register_component(db, code="api", display_name="API", freshness_seconds=60)
    at, as_of = _evidence_now()
    first = record_observation(
        db,
        HealthObservationInput(
            "agent",
            "api:1",
            "api",
            HealthState.HEALTHY,
            at,
            at,
            "ok",
            {},
        ),
    )

    class _RecordingSigner:
        def __init__(self) -> None:
            self.seen: list[bytes] = []

        def sign_health_evidence(self, evidence, canonical_bytes: bytes):
            self.seen.append(canonical_bytes)
            return HealthEvidenceSignature("ed25519", "test-key", canonical_bytes[:8])

    signer = _RecordingSigner()
    signed_base = produce_signed_health_evidence(
        db,
        requested_components=("api",),
        evaluated_at=as_of,
        signer=signer,
    )
    record_observation(
        db,
        HealthObservationInput(
            "agent",
            "api:2",
            "api",
            HealthState.UNHEALTHY,
            at + timedelta(seconds=1),
            at + timedelta(seconds=1),
            "down",
            {},
        ),
    )
    projection = db.scalar(select(HealthProjection))
    projection.observation_id = first.observation.id
    projection.state = HealthState.HEALTHY.value
    db.flush()
    signed_mutated = produce_signed_health_evidence(
        db,
        requested_components=("api",),
        evaluated_at=as_of,
        signer=signer,
    )

    # The signer received the ACTUAL canonical bytes of each document, not a
    # constant and not a summary — this is what rules out an implementation
    # that signs a fixed payload regardless of input.
    assert signed_base.evidence.components[0].state == "healthy"
    assert signed_mutated.evidence.components[0].state == "unhealthy"
    assert signed_mutated.evidence.components[0].observation_id != first.observation.id
    assert signed_base.canonical_bytes != signed_mutated.canonical_bytes
    assert signer.seen[0] != signer.seen[1]
    assert signed_base.signature.signature != signed_mutated.signature.signature


def test_determinism_survives_a_hash_seed_change_across_processes() -> None:
    """Executed, not asserted: two fresh interpreter processes, two different
    `PYTHONHASHSEED` values, compared by SHA-256 of the emitted bytes."""
    health_src = REPO_ROOT / "packages/dotmac-platform-health/src"
    kernel_src = REPO_ROOT / "packages/dotmac-kernel/src"
    script = (
        f"import sys; sys.path.insert(0, {str(health_src)!r}); "
        f"sys.path.insert(0, {str(kernel_src)!r})\n"
        "from datetime import UTC, datetime\n"
        "from dotmac_platform_health.contracts import ComponentEvidence, "
        "DeploymentHealthEvidence\n"
        "from dotmac_platform_health.evidence import "
        "canonical_health_evidence_bytes\n"
        "ev = DeploymentHealthEvidence(\n"
        "    evaluated_at=datetime(2026, 9, 7, 12, 0, tzinfo=UTC),\n"
        "    valid_until=datetime(2026, 9, 7, 12, 5, tzinfo=UTC),\n"
        "    components=(\n"
        "        ComponentEvidence('worker', None, None, 'unknown', 'missing'),\n"
        "        ComponentEvidence('api', None, datetime(2026, 9, 7, 11, 59, "
        "tzinfo=UTC), 'healthy', 'fresh'),\n"
        "    ),\n"
        ")\n"
        "sys.stdout.buffer.write(canonical_health_evidence_bytes(ev))\n"
    )
    digests = set()
    for seed in ("0", "1", "999", "random"):
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = seed
        result = subprocess.run(  # noqa: S603 -- fixed argv, shell=False, no untrusted input
            [sys.executable, "-c", script],
            capture_output=True,
            env=env,
            cwd=str(REPO_ROOT),
            check=True,
        )
        digests.add(hashlib.sha256(result.stdout).hexdigest())
    assert len(digests) == 1, f"bytes differ across PYTHONHASHSEED: {digests}"


# ── 3. The injected signer port ─────────────────────────────────────────────


def test_produce_signed_health_evidence_refuses_without_a_signer(db: Session) -> None:
    with pytest.raises(HealthEvidenceError, match="signer"):
        produce_signed_health_evidence(
            db,
            requested_components=("api",),
            evaluated_at=_evidence_now()[1],
            signer=None,
        )


def test_non_ed25519_signer_is_refused(db: Session) -> None:
    register_component(db, code="api", display_name="API", freshness_seconds=60)
    at, as_of = _evidence_now()
    record_observation(
        db,
        HealthObservationInput(
            "agent", "api:1", "api", HealthState.HEALTHY, at, at, "ok", {}
        ),
    )

    class _WrongAlgorithm:
        def sign_health_evidence(self, evidence, canonical_bytes):
            return HealthEvidenceSignature("rsa", "test-key", canonical_bytes[:8])

    with pytest.raises(HealthEvidenceError, match="ed25519"):
        produce_signed_health_evidence(
            db,
            requested_components=("api",),
            evaluated_at=as_of,
            signer=_WrongAlgorithm(),
        )


def test_this_package_defines_no_default_signer_implementation() -> None:
    """AST sweep, not an import-and-hope: no class anywhere under
    `dotmac_platform_health` defines `sign_health_evidence` except the
    `Protocol` declaration itself (an abstract `...` body, never real logic
    such as constructing a private key or calling a crypto library)."""
    import ast

    implementations = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef)
                and node.name == "sign_health_evidence"
            ):
                body = node.body
                is_stub = len(body) == 1 and (
                    isinstance(body[0], ast.Pass)
                    or (
                        isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and body[0].value.value is Ellipsis
                    )
                )
                implementations.append(
                    (str(path.relative_to(PACKAGE_ROOT)), node.lineno, is_stub)
                )
    assert implementations, "sign_health_evidence must be declared somewhere"
    assert all(is_stub for _, _, is_stub in implementations), (
        f"a real (non-stub) sign_health_evidence body exists: {implementations} — "
        "this package "
        "must hold no default signer implementation"
    )


def test_package_holds_no_network_or_key_material_imports() -> None:
    import ast

    forbidden = {"httpx", "requests", "cryptography", "nacl"}
    violations = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            roots: set[str] = set()
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".", 1)[0])
            for root in sorted(roots & forbidden):
                violations.append(
                    (str(path.relative_to(PACKAGE_ROOT)), node.lineno, root)
                )
    assert not violations, violations


def test_evidence_module_stays_kernel_independent() -> None:
    import ast

    path = PACKAGE_ROOT / "evidence.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots = {
        node.module.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
    assert "dotmac_kernel" not in roots
