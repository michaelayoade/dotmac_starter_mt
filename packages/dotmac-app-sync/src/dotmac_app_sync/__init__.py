"""Public surface for provider-neutral inter-application synchronization."""

from dotmac_app_sync.contracts import (
    AuthenticatedPeer,
    DuplicateContract,
    EnvelopeInvalid,
    PeerMismatch,
    SyncAcceptance,
    SyncContract,
    SyncContractError,
    SyncContractRegistry,
    SyncEnvelope,
    SyncReceipt,
    SyncReceiver,
    UnknownContract,
    deliver_authenticated,
    encode_envelope,
    fingerprint_for,
    idempotency_key_for,
)

__version__ = "0.1.0a1"

__all__ = [
    "AuthenticatedPeer",
    "DuplicateContract",
    "EnvelopeInvalid",
    "PeerMismatch",
    "SyncAcceptance",
    "SyncContract",
    "SyncContractError",
    "SyncContractRegistry",
    "SyncEnvelope",
    "SyncReceipt",
    "SyncReceiver",
    "UnknownContract",
    "deliver_authenticated",
    "encode_envelope",
    "fingerprint_for",
    "idempotency_key_for",
]
