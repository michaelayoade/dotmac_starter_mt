"""The private manifest inside a Lane 3 evidence bundle.

A v2 receipt binds only the SHA-256 of the age-encrypted bundle as
uploaded (`lane3_receipt_v2.evidence_bundle_digest` refuses plaintext).
That binds the ciphertext, but says nothing about what the bundle must
contain: today a bundle holding probe evidence alone and a bundle also
carrying the Executor exposure records are indistinguishable to every
reader. This module closes that gap on the plaintext side, before
encryption: one closed canonical manifest naming every member by digest
and length, a strict parser, and a member check that refuses a missing
or altered payload.

The manifest is itself private evidence — it names the files a bundle
carries, never their contents — so it belongs INSIDE the ciphertext
beside the members it lists. Building and encrypting the bundle stays
the launcher's act (the encryption recipient is Michael's open decision
in docs/LANE3_EXECUTION_TOPOLOGY.md § 11); nothing here encrypts,
uploads, or admits anything.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

EVIDENCE_BUNDLE_MANIFEST_SCHEMA = "lane3.evidence-bundle-manifest.v1"

#: The private member names a bundle may carry. Closed on purpose: a
#: member this list does not name is not evidence a proof may join, and a
#: new member arrives by naming it here with the check that consumes it.
BUNDLE_MEMBERS: frozenset[str] = frozenset(
    {"probe-evidence.json", "exposure-calls.json"}
)

#: The member the exposure proof join consumes.
EXPOSURE_CALLS_MEMBER = "exposure-calls.json"


class EvidenceBundleRefused(ValueError):
    """The manifest cannot be built, parsed, or joined to a member."""


def _member_document(name: str, payload: bytes) -> dict[str, Any]:
    return {
        "bytes": len(payload),
        "digest": hashlib.sha256(payload).hexdigest(),
        "name": name,
    }


def _canonical(document: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            document, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        + b"\n"
    )


def build_manifest(members: Mapping[str, bytes]) -> bytes:
    """The canonical manifest bytes for exactly these members.

    Every named member must be a member this module knows, and the set
    must be the WHOLE set: a manifest that quietly omits the exposure
    records is the failure this document exists to refuse, so a partial
    bundle is refused at build time rather than joined later.
    """
    unknown = frozenset(members) - BUNDLE_MEMBERS
    if unknown:
        raise EvidenceBundleRefused(
            f"evidence bundle members {sorted(unknown)} are not members a "
            f"bundle may carry: {sorted(BUNDLE_MEMBERS)}"
        )
    missing = BUNDLE_MEMBERS - frozenset(members)
    if missing:
        raise EvidenceBundleRefused(
            f"evidence bundle is missing members {sorted(missing)}; a "
            "partial bundle proves nothing and is refused whole"
        )
    document = {
        "members": [_member_document(name, members[name]) for name in sorted(members)],
        "schema": EVIDENCE_BUNDLE_MANIFEST_SCHEMA,
    }
    return _canonical(document)


def parse_manifest(data: bytes) -> dict[str, tuple[str, int]]:
    """Member name -> (digest, byte length), or a refusal.

    The parsed members are re-serialized through the same canonical form
    and compared byte for byte (digests and lengths are all the document
    carries, so rebuilding needs no payloads): a reformatted or extended
    manifest is not the manifest a bundle was built with.
    """
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceBundleRefused(
            f"evidence bundle manifest is not UTF-8 JSON: {exc}"
        ) from exc
    if not isinstance(document, dict) or frozenset(document) != {"members", "schema"}:
        raise EvidenceBundleRefused(
            "evidence bundle manifest keys do not close over members/schema"
        )
    if document["schema"] != EVIDENCE_BUNDLE_MANIFEST_SCHEMA:
        raise EvidenceBundleRefused(
            f"evidence bundle manifest schema {document['schema']!r} is not "
            f"{EVIDENCE_BUNDLE_MANIFEST_SCHEMA!r}"
        )
    raw_members = document["members"]
    if not isinstance(raw_members, list):
        raise EvidenceBundleRefused("evidence bundle manifest members must be a list")
    members: dict[str, tuple[str, int]] = {}
    for index, item in enumerate(raw_members):
        where = f"evidence bundle manifest members[{index}]"
        if not isinstance(item, dict) or frozenset(item) != {"bytes", "digest", "name"}:
            raise EvidenceBundleRefused(
                f"{where} keys do not close over bytes/digest/name"
            )
        name, digest, length = item["name"], item["digest"], item["bytes"]
        if not isinstance(name, str) or name not in BUNDLE_MEMBERS:
            raise EvidenceBundleRefused(f"{where}.name {name!r} is not a bundle member")
        if not isinstance(digest, str) or len(digest) != 64:
            raise EvidenceBundleRefused(f"{where}.digest is not a SHA-256 hex digest")
        if isinstance(length, bool) or not isinstance(length, int) or length < 0:
            raise EvidenceBundleRefused(f"{where}.bytes must be a non-negative integer")
        if name in members:
            raise EvidenceBundleRefused(
                f"evidence bundle member {name!r} is listed twice"
            )
        members[name] = (digest, length)
    if frozenset(members) != BUNDLE_MEMBERS:
        raise EvidenceBundleRefused(
            f"evidence bundle manifest members {sorted(members)} do not close "
            f"over {sorted(BUNDLE_MEMBERS)}"
        )
    rebuilt = {
        "members": [
            {"bytes": length, "digest": digest, "name": name}
            for name, (digest, length) in sorted(members.items())
        ],
        "schema": EVIDENCE_BUNDLE_MANIFEST_SCHEMA,
    }
    if _canonical(rebuilt) != data:
        raise EvidenceBundleRefused(
            "evidence bundle manifest is not its canonical serialization"
        )
    return members


def require_member(manifest: bytes, name: str, payload: bytes) -> None:
    """Refuse unless `payload` is exactly the named manifest member."""
    members = parse_manifest(manifest)
    digest, length = members[name]
    if len(payload) != length or hashlib.sha256(payload).hexdigest() != digest:
        raise EvidenceBundleRefused(
            f"payload does not join evidence bundle member {name!r}: the "
            "manifest binds a different digest or length"
        )
