"""Closed public claims are untrusted until signature and binding checks finish."""

import base64
import hashlib
import json
import sys
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from lane3_offline_result import (
    AUDIENCE,
    MAX_POLICY_BYTES,
    MAX_RESULT_BYTES,
    MAX_REVOCATIONS_BYTES,
    MAX_SIGNERS_BYTES,
    NAMESPACE,
    PRINCIPAL,
    OfflineResultCoordinates,
    OfflineResultRefused,
    OfflineResultUnavailable,
    parse_policy,
    parse_result,
    require_result_bindings,
)


def canonical(document):
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()


def digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


COORDINATES = OfflineResultCoordinates(
    1,
    2,
    3,
    digest(b"receipt"),
    digest(b"cipher"),
    digest(b"candidate record"),
    "a" * 40,
)
NOW = datetime(2026, 11, 10, 12, tzinfo=UTC)
# Structural synthetic public bytes only; OpenSSH owns actual key validity.
PUBLIC_KEY = base64.b64encode(
    b"\0\0\0\x0bssh-ed25519\0\0\0\x20" + bytes(range(32))
).decode()
SIGNERS = (
    f'{PRINCIPAL} namespaces="{NAMESPACE}",'
    'valid-after="20261101000000Z",valid-before="20261201000000Z" '
    f"ssh-ed25519 {PUBLIC_KEY}\n"
).encode()


def policy_document(signers=SIGNERS, revocations=b"", state="enrolled"):
    return {
        "schema": "lane3.offline-verifier-policy.v1",
        "state": state,
        "namespace": NAMESPACE,
        "principal": PRINCIPAL,
        "audience": AUDIENCE,
        "ssh_keygen_sha256": digest(b"tool") if state == "enrolled" else None,
        "verifier_source_revision": "b" * 40 if state == "enrolled" else None,
        "verifier_profile_digest": digest(b"profile") if state == "enrolled" else None,
        "allowed_signers_digest": digest(signers),
        "revoked_keys_digest": digest(revocations),
        "max_result_age_seconds": 86400,
        "future_clock_tolerance_seconds": 300,
    }


def policy():
    return parse_policy(canonical(policy_document()), SIGNERS, b"")


def result_document():
    return {
        "schema": "lane3.offline-verification-result.v1",
        "purpose": NAMESPACE,
        "audience": AUDIENCE,
        "coordinates": asdict(COORDINATES),
        "verifier_source_revision": "b" * 40,
        "verifier_profile_digest": digest(b"profile"),
        "policy_digest": policy().sha256_digest(),
        "verified_at": "2026-11-10T11:00:00Z",
        "expires_at": "2026-11-10T13:00:00Z",
        "conclusion": "verified",
    }


def test_exact_tuple_can_be_repeated_while_valid():
    payload = canonical(result_document())
    result = parse_result(payload)
    assert result.canonical_bytes == payload
    assert result.sha256_digest() == digest(payload)
    for _ in range(2):
        require_result_bindings(result, expected=COORDINATES, policy=policy(), now=NOW)


@pytest.mark.parametrize("field", list(asdict(COORDINATES)))
def test_every_selected_coordinate_is_bound(field):
    value = getattr(COORDINATES, field)
    changed = (
        value + 1
        if type(value) is int
        else ("c" * 40 if field == "release_revision" else digest(b"other"))
    )
    with pytest.raises(OfflineResultRefused):
        require_result_bindings(
            parse_result(canonical(result_document())),
            expected=replace(COORDINATES, **{field: changed}),
            policy=policy(),
            now=NOW,
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("verifier_source_revision", "c" * 40),
        ("verifier_profile_digest", digest(b"other")),
        ("policy_digest", digest(b"other")),
    ],
)
def test_verifier_and_policy_pins_are_bound(field, value):
    doc = result_document()
    doc[field] = value
    with pytest.raises(OfflineResultRefused):
        require_result_bindings(
            parse_result(canonical(doc)), expected=COORDINATES, policy=policy(), now=NOW
        )


@pytest.mark.parametrize(
    "payload", [b"{}\n", b"\xff", b'{"x":1,"x":2}\n', b'{"x":NaN}\n', b"[]\n"]
)
def test_malformed_result_is_refused_without_echoing_input(payload):
    with pytest.raises(OfflineResultRefused, match=r"^offline_result\.[a-z_]+$"):
        parse_result(payload)


@pytest.mark.parametrize(
    "mutation",
    [
        "extra",
        "missing",
        "bool",
        "float",
        "upper_digest",
        "timezone",
        "conclusion",
        "huge_id",
    ],
)
def test_closed_schema_negative_cases(mutation):
    doc = result_document()
    if mutation == "extra":
        doc["private_topology"] = "must never echo"
    elif mutation == "missing":
        del doc["purpose"]
    elif mutation == "bool":
        doc["coordinates"]["run_id"] = True
    elif mutation == "float":
        doc["coordinates"]["run_id"] = 2.0
    elif mutation == "upper_digest":
        doc["policy_digest"] = "sha256:" + "A" * 64
    elif mutation == "timezone":
        doc["verified_at"] = "2026-11-10T11:00:00+00:00"
    elif mutation == "conclusion":
        doc["conclusion"] = "pass"
    elif mutation == "huge_id":
        doc["coordinates"]["run_id"] = 2**63
    with pytest.raises(OfflineResultRefused):
        parse_result(canonical(doc))


def test_only_exact_canonical_bytes_are_accepted():
    payload = canonical(result_document())
    for changed in (
        payload[:-1],
        payload + b"\n",
        payload.replace(b":", b": ", 1),
        payload.replace(b'"run_id":2', b'"run_id":2,"run_id":2'),
    ):
        with pytest.raises(OfflineResultRefused):
            parse_result(changed)


@pytest.mark.parametrize(
    "verified,expires,now",
    [
        ("2026-11-10T12:05:01Z", "2026-11-10T13:00:00Z", NOW),
        ("2026-11-10T11:00:00Z", "2026-11-10T11:00:00Z", NOW),
        ("2026-11-09T10:00:00Z", "2026-11-10T13:00:00Z", NOW),
        ("2026-11-10T11:00:00Z", "2026-11-10T13:00:00Z", NOW + timedelta(hours=1)),
    ],
)
def test_time_bounds(verified, expires, now):
    doc = result_document()
    doc.update(verified_at=verified, expires_at=expires)
    with pytest.raises(OfflineResultRefused):
        require_result_bindings(
            parse_result(canonical(doc)), expected=COORDINATES, policy=policy(), now=now
        )


def test_now_must_be_aware_utc():
    with pytest.raises(OfflineResultRefused):
        require_result_bindings(
            parse_result(canonical(result_document())),
            expected=COORDINATES,
            policy=policy(),
            now=NOW.replace(tzinfo=None),
        )


def test_shipped_policy_is_unenrolled_and_transitively_pins_empty_files():
    home = Path(__file__).resolve().parents[2] / "docs/inventories"
    parsed = parse_policy(
        (home / "lane3-offline-verifier-policy.json").read_bytes(),
        (home / "lane3-offline-verifier.allowed_signers").read_bytes(),
        (home / "lane3-offline-verifier.revoked_keys").read_bytes(),
    )
    assert parsed.state == "unenrolled"
    assert parsed.allowed_signers_digest == parsed.revoked_keys_digest == digest(b"")
    with pytest.raises(OfflineResultUnavailable):
        require_result_bindings(
            parse_result(canonical(result_document())),
            expected=COORDINATES,
            policy=parsed,
            now=NOW,
        )


@pytest.mark.parametrize(
    "signers",
    [
        b"",
        SIGNERS + SIGNERS,
        SIGNERS.replace(PRINCIPAL.encode(), b"*"),
        SIGNERS.replace(b"ssh-ed25519 ", b"cert-authority ssh-ed25519 "),
        SIGNERS.replace(b"valid-before", b"unknown-option"),
        SIGNERS.replace(b"20261201000000Z", b"forever"),
        SIGNERS.replace(PUBLIC_KEY.encode(), b"!!!"),
    ],
)
def test_enrolled_signer_constraints(signers):
    with pytest.raises(OfflineResultRefused):
        parse_policy(canonical(policy_document(signers)), signers, b"")


def test_policy_rejects_missing_trust_bytes_and_digest_mismatch():
    with pytest.raises(OfflineResultRefused):
        parse_policy(canonical(policy_document()), None, b"")
    with pytest.raises(OfflineResultRefused):
        parse_policy(canonical(policy_document()), SIGNERS, b"changed")


@pytest.mark.parametrize(
    "revocations",
    [
        b"/some/path\n",
        b"@cert-authority ssh-ed25519 AAAA\n",
        b"ssh-rsa AAAA\n",
        b"ssh-ed25519 !!!\n",
    ],
)
def test_revocations_are_only_plain_public_ed25519_lines(revocations):
    with pytest.raises(OfflineResultRefused):
        parse_policy(
            canonical(policy_document(revocations=revocations)), SIGNERS, revocations
        )


def test_caps_are_checked_before_decoding():
    with pytest.raises(OfflineResultRefused):
        parse_result(b"x" * (MAX_RESULT_BYTES + 1))
    for payload, signers, revoked in (
        (b"x" * (MAX_POLICY_BYTES + 1), SIGNERS, b""),
        (canonical(policy_document()), b"x" * (MAX_SIGNERS_BYTES + 1), b""),
        (canonical(policy_document()), SIGNERS, b"x" * (MAX_REVOCATIONS_BYTES + 1)),
    ):
        with pytest.raises(OfflineResultRefused):
            parse_policy(payload, signers, revoked)


@pytest.mark.parametrize(
    "mutation",
    ["unknown", "missing", "partial", "duration", "bool", "duplicate", "noncanonical"],
)
def test_policy_closed_schema(mutation):
    doc = policy_document()
    if mutation == "unknown":
        doc["trust_root_path"] = "/not-accepted"
    elif mutation == "missing":
        del doc["revoked_keys_digest"]
    elif mutation == "partial":
        doc["ssh_keygen_sha256"] = None
    elif mutation == "duration":
        doc["max_result_age_seconds"] = 86401
    elif mutation == "bool":
        doc["future_clock_tolerance_seconds"] = True
    payload = canonical(doc)
    if mutation == "duplicate":
        payload = payload.replace(
            b'"state":"enrolled"', b'"state":"enrolled","state":"enrolled"'
        )
    elif mutation == "noncanonical":
        payload = payload[:-1]
    with pytest.raises(OfflineResultRefused):
        parse_policy(payload, SIGNERS, b"")


def test_valid_plain_revocations_and_exact_future_tolerance_are_parseable():
    revocations = f"ssh-ed25519 {PUBLIC_KEY}\n".encode()
    parsed_policy = parse_policy(
        canonical(policy_document(revocations=revocations)), SIGNERS, revocations
    )
    assert parsed_policy.revoked_keys_bytes == revocations
    doc = result_document()
    doc["verified_at"] = "2026-11-10T12:05:00Z"
    require_result_bindings(
        parse_result(canonical(doc)), expected=COORDINATES, policy=policy(), now=NOW
    )


@pytest.mark.parametrize(
    "expected",
    [
        replace(COORDINATES, repository_id=True),
        replace(COORDINATES, repository_id=1.0),
        {},
    ],
)
def test_expected_coordinate_types_cannot_match_by_python_coercion(expected):
    with pytest.raises(OfflineResultRefused):
        require_result_bindings(
            parse_result(canonical(result_document())),
            expected=expected,
            policy=policy(),
            now=NOW,
        )
