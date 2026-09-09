"""The package-owned writer for ``TrustedHostAttestation.v2`` records.

`trusted_host_source.py` verifies; it does no I/O by explicit design (its own
module docstring: "These types freeze the successor contract; they hold no
private key, discover no file, and do no I/O"). A document type still needs
exactly one writer, so this sibling module owns it — the same split
``lease.py`` already draws between a store module and a pure-type module,
applied here to a verifier module instead of a dataclass module. Adding a
write function to ``trusted_host_source.py`` itself would break a load-bearing
architectural boundary that module states about itself, not merely a style
preference.

## Why this reuses ``lease.write_store_record_once`` rather than
## re-implementing it

Two independent ``os.link``-based atomic-once writers in one package is
exactly the duplication ``write_store_record_once``'s own docstring warns
against generalizing past: "A release may not [be silently overwritten]... a
second write is either a replay or two runs each believing they finished the
same work." A signed candidate (or installed-host) attestation has the
identical hazard — a second write to the same path must never silently
replace the first, because a caller downstream may already be citing it. So
the MECHANISM is reused, not copied; what this module adds is a NAME:
``TrustedHostAttestation.v2`` now has one declared owner for its wire form,
and neither a CI signer nor a host-attestation workload hand-rolls its own
``json.dumps(..., sort_keys=True)`` again.

## Why the path is a real parameter here, unlike ``lease.py``'s callers

``HostLease``/``HostLeaseRelease.v1`` each have a canonical, package-derived
ledger location (``_lease_path``/``release_path``, both keyed off a target
name) — the store is a fixed, host-side directory this package also reads
back from. A signed attestation has no such location: it is produced once (by
a CI signer, or later a host-attestation workload) and handed to a
workflow-local or host-local path that becomes a build artifact or an
observation input, never a directory this package itself reads back from
later. So unlike ``write_lease``/the release writer, ``path`` here is a real,
caller-supplied parameter rather than something this module derives.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .lease import write_store_record_once
from .trusted_host_source import AttestationEnvelopeV2

__all__ = ["attestation_envelope_document", "write_attestation_record"]


def attestation_envelope_document(envelope: AttestationEnvelopeV2) -> dict[str, Any]:
    """The one wire mapping for a signed envelope.

    Works identically for either custody role — candidate or installed-host —
    because both are the same ``AttestationEnvelopeV2`` type; there is nothing
    role-specific here for a caller to get wrong. A producer and a later
    reader of the record agree on field order and shape because both go
    through this function, never through an inline dict literal at the call
    site.
    """
    return {
        "schema": envelope.schema,
        "purpose": envelope.purpose,
        "issuer": envelope.issuer,
        "key_id": envelope.key_id,
        "algorithm": envelope.algorithm,
        "public_key_fingerprint": envelope.public_key_fingerprint,
        "custody_domain": envelope.custody_domain,
        "trust_root_version": envelope.trust_root_version,
        "issued_at": envelope.issued_at,
        "expires_at": envelope.expires_at,
        "audience": envelope.audience,
        "observation_id": envelope.observation_id,
        "subject": envelope.subject_mapping(),
        "signature": envelope.signature,
    }


def write_attestation_record(path: Path, envelope: AttestationEnvelopeV2) -> Path:
    """Publish a signed envelope, atomically and exactly once, at ``path``.

    Raises ``FileExistsError`` on a second write to the same path — the same
    refusal ``write_store_record_once`` raises for a duplicate release, and
    for the identical reason: a second write is either a replay or two runs
    each believing they produced the authoritative bytes, and overwriting
    would pick one silently.
    """
    return write_store_record_once(path, attestation_envelope_document(envelope))
