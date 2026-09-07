"""Canonical serialization and the injected signer port for health evidence.

ADR-0070's 2026-09-07 amendment: "a health receipt has one producer, and
Foundation only verifies it." Platform Health authors
`DeploymentHealthEvidence.v1` and signs its own canonical bytes; it does not
embed a signing implementation, hold key material, or reach a network. This
module is deliberately the ONLY place those two things happen — canonicalizing
the document into deterministic bytes, and defining the `Protocol` an assembly
implements and injects.

**No generic Kernel signer exists, and none is added here.** This package
defines its OWN domain-specific port (`HealthEvidenceSigner`); an assembly
composes a concrete implementation (Ed25519, key material from an approved
`SecretSource`/OpenBao pointer) and supplies it as an ordinary constructor
argument to the service's `produce_signed_health_evidence` — there is no
`install_health_evidence_signer()`-style global registration. Read
`dotmac_kernel.settings_crypto.KeyProvider` for the SHAPE this follows (a port
loaded once, held, refreshed only explicitly, never fetched per request) —
not as something this package reuses; nothing here imports `dotmac_kernel`.

**Domain-specific, not a bytes-signer.** `HealthEvidenceSigner.sign_health_evidence`
takes the typed `DeploymentHealthEvidence` object alongside its canonical
bytes, not bare `bytes`. An implementation written against this Protocol is
visibly a health-evidence signer at every call site; reusing it to sign an
unrelated document class requires changing the signature, not just the call.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from dotmac_platform_health.contracts import (
    DEPLOYMENT_HEALTH_EVIDENCE_SCHEMA,
    ComponentEvidence,
    DeploymentHealthEvidence,
)


class HealthEvidenceError(ValueError):
    """Canonical evidence cannot be produced or signed as requested."""


def _canonical_instant(value: datetime) -> str:
    if value.utcoffset() is None:
        raise HealthEvidenceError("evidence timestamps must be timezone-aware")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _canonical_component(component: ComponentEvidence) -> dict[str, str | None]:
    return {
        "component_code": component.component_code,
        "observation_id": (
            str(component.observation_id) if component.observation_id else None
        ),
        "observed_at": (
            _canonical_instant(component.observed_at)
            if component.observed_at is not None
            else None
        ),
        "state": component.state,
        "freshness": component.freshness,
    }


def canonical_health_evidence_bytes(evidence: DeploymentHealthEvidence) -> bytes:
    """Deterministic, self-describing bytes for a `DeploymentHealthEvidence`.

    Byte-stable across processes and `PYTHONHASHSEED` values: every mapping
    key ordering is fixed by `sort_keys=True` (Python dict iteration order is
    never consulted), the component list is re-sorted HERE by `component_code`
    rather than trusted from the caller's construction order, and every
    timestamp goes through `_canonical_instant`'s fixed-width UTC format
    rather than `datetime.isoformat()` (whose output width depends on whether
    microseconds happen to be zero). `separators=(",", ":")` removes the one
    remaining source of incidental whitespace drift between `json` versions.

    Self-describing and dependency-free to read: the `schema` field is the
    only thing a reader needs to recognize this document — `json.loads` and a
    dict access is the whole contract, no `dotmac_platform_health` import
    required. This is what makes the document readable by Foundation, which
    must import neither Platform Health, Control nor Integrator (ADR-0070
    amendment).
    """
    payload = {
        "schema": DEPLOYMENT_HEALTH_EVIDENCE_SCHEMA,
        "evaluated_at": _canonical_instant(evidence.evaluated_at),
        "valid_until": _canonical_instant(evidence.valid_until),
        "components": [
            _canonical_component(component)
            for component in sorted(evidence.components, key=lambda c: c.component_code)
        ],
    }
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class HealthEvidenceSignature:
    """An Ed25519 signature over exactly one document's canonical bytes."""

    algorithm: str
    key_id: str
    signature: bytes


@dataclass(frozen=True, slots=True)
class SignedHealthEvidence:
    """A canonical evidence document paired with the exact bytes signed.

    `canonical_bytes` is carried alongside `evidence` rather than re-derived
    by every reader, so a verifier checks the signature against the bytes
    that were actually signed — re-deriving independently would let a
    canonicalization drift between producer and verifier versions pass
    silently as "still verifies".
    """

    evidence: DeploymentHealthEvidence
    canonical_bytes: bytes
    signature: HealthEvidenceSignature


@runtime_checkable
class HealthEvidenceSigner(Protocol):
    """The injected port. Platform Health defines it; a product supplies it.

    No default implementation exists anywhere in this package — see module
    docstring. `produce_signed_health_evidence` refuses outright if `signer` is
    `None`, rather than falling back to emitting unsigned evidence.
    """

    def sign_health_evidence(
        self, evidence: DeploymentHealthEvidence, canonical_bytes: bytes
    ) -> HealthEvidenceSignature: ...


__all__ = [
    "HealthEvidenceError",
    "HealthEvidenceSignature",
    "HealthEvidenceSigner",
    "SignedHealthEvidence",
    "canonical_health_evidence_bytes",
]
