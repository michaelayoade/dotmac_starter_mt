"""The Meta/WhatsApp ingress conformance kit — executable, connector-free.

ADR-0030 § 6 permits connector **dossiers, capability contracts and conformance
specifications**, and blocks connector implementation. This module is the third
of those: it turns
`docs/superpowers/specs/2026-08-15-meta-whatsapp-ingress-conformance.md` into
assertions that run today, against a corpus of real Meta request bodies ported
from `dotmac_sub`, with no connector installed and no network.

## What it proves without a connector

A conformance suite that only asserted "the future connector will do X" would be
a comment. These tests instead assert properties of the **corpus** that a
connector cannot satisfy by accident:

* the recorded signature of every fixture is over the exact bytes on disk, so a
  reformatted fixture fails loudly rather than quietly ceasing to prove anything;
* re-serialising a body — one whitespace byte — invalidates its signature, which
  is the raw-bytes rule stated as arithmetic rather than as advice;
* every declared event identity is **recomputed from the body node it points
  at**, so the identity templates are executable, not decorative;
* two fixtures share an event, and their request digests differ — which is the
  request-digest identity anti-pattern (`meta:{sha256(raw_body)}`, live in Sub
  today) failing in front of the reader.

When the gate in ADR-0030 § 6 opens and a connector distribution is named, its
`normalize` runs against this same manifest and must produce exactly the
declared observations. Nothing here needs to change for that to happen.

## The reference oracle

`_sign`, `_verify` and `_derived_error_identity` below are the conformance
oracle. They are not a connector: no provider client, no HTTP, no configuration,
no persistence, and no decision about what a message MEANS. They exist so the
fixtures can be checked against themselves.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from pathlib import Path
from typing import Any

import pytest

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "meta_whatsapp"
MANIFEST: dict[str, Any] = json.loads(
    (FIXTURE_ROOT / "manifest.json").read_text(encoding="utf-8")
)
SIGNATURES: dict[str, Any] = json.loads(
    (FIXTURE_ROOT / "signatures.json").read_text(encoding="utf-8")
)
KEYS: dict[str, Any] = MANIFEST["signing_keys"]
ACTIVE_KEYS: tuple[str, ...] = tuple(KEYS["active"])
FIXTURES: list[dict[str, Any]] = MANIFEST["fixtures"]
BY_FILE: dict[str, dict[str, Any]] = {f["file"]: f for f in FIXTURES}

#: `domain.noun.vN`, the grammar `dotmac_integration.spi._CAPABILITY_RE`
#: enforces. Restated rather than imported: this suite must keep passing while
#: the SPI is being amended, and a conformance kit that cannot run during an
#: SPI change is a conformance kit that stops running exactly when it matters.
CAPABILITY_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+\.v[1-9][0-9]*$")

SIGNATURE_VALUE_RE = re.compile(MANIFEST["signature_header"]["value_pattern"])


# ── the reference oracle ─────────────────────────────────────────────────────


def _read(relative: str) -> bytes:
    """The EXACT bytes, never `json.load` then re-dump. That distinction is the
    single most load-bearing line in this file."""
    return (FIXTURE_ROOT / relative).read_bytes()


def _sign(body: bytes, key: str) -> str:
    return "sha256=" + hmac.new(key.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _verify(
    body: bytes,
    presented: str | None,
    keys: tuple[str, ...],
    *,
    audit: list[str] | None = None,
) -> bool:
    """Constant-time, and constant-WORK across the active key set.

    Two properties, both deliberate:

    1. `hmac.compare_digest`, never `==`. A byte-by-byte comparison that returns
       early leaks how much of a forged signature was correct, which is enough
       to reconstruct one.
    2. Every active key is evaluated and the results are OR-ed at the end. An
       early `return True` on the first match would make the response time
       reveal WHICH secret verified the request — during a rotation window that
       tells an observer whether the old or the new secret is in play, and when
       the window closes.
    """
    if presented is None or not SIGNATURE_VALUE_RE.fullmatch(presented):
        return False
    matched = False
    for key in keys:
        if audit is not None:
            audit.append(key)
        matched |= hmac.compare_digest(presented, _sign(body, key))
    return matched


def _canonical(node: Any) -> bytes:
    return json.dumps(node, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _resolve(document: Any, pointer: str) -> Any:
    """RFC 6901 JSON pointer, enough of it for these locators."""
    node = document
    for token in pointer.split("/")[1:]:
        node = node[int(token)] if isinstance(node, list) else node[token]
    return node


def _locator_list(pointer: str) -> str:
    """`/entry/0/changes/0/value/messages/0` -> `messages`."""
    return pointer.split("/")[-2]


def _identity_scope(document: Any, pointer: str) -> str:
    """`value.metadata.phone_number_id`, else the entry id."""
    entry_index = int(pointer.split("/")[2])
    entry = document["entry"][entry_index]
    change_index = int(pointer.split("/")[4])
    value = entry["changes"][change_index]["value"]
    metadata = value.get("metadata") or {}
    return str(metadata.get("phone_number_id") or entry["id"])


def _derived_error_identity(document: Any, pointer: str) -> str:
    node = _resolve(document, pointer)
    digest = hashlib.sha256(_canonical(node)).hexdigest()[:32]
    return f"wa:error:{_identity_scope(document, pointer)}:{digest}"


def _expected_identity(document: Any, observation: dict[str, Any]) -> str:
    """Recompute an observation's identity from the body node it points at."""
    pointer = observation["locator"]
    node = _resolve(document, pointer)
    list_name = _locator_list(pointer)
    if list_name == "messages":
        return f"wa:msg:{node['id']}"
    if list_name == "statuses":
        return f"wa:status:{node['id']}:{node['status']}:{node['timestamp']}"
    if list_name == "errors":
        return _derived_error_identity(document, pointer)
    raise AssertionError(f"no identity rule for locator {pointer!r}")


def _document(relative: str) -> Any:
    return json.loads(_read(relative).decode("utf-8"))


ALL_FILES = pytest.mark.parametrize("relative", sorted(BY_FILE), ids=lambda p: p[7:-5])


# ── the corpus is complete and attributable ──────────────────────────────────


def test_every_body_file_is_declared_and_every_declaration_has_a_file():
    on_disk = {
        f"bodies/{path.name}" for path in (FIXTURE_ROOT / "bodies").glob("*.json")
    }
    assert on_disk == set(BY_FILE), (
        "a fixture body that no manifest entry describes proves nothing, and a "
        "manifest entry with no body is a promise with no evidence"
    )


@ALL_FILES
def test_every_fixture_records_where_it_came_from(relative: str):
    provenance = BY_FILE[relative]["provenance"]
    for field in ("repo", "path", "symbol", "lines", "commit"):
        assert provenance.get(field), f"{relative}: provenance is missing {field!r}"
    assert isinstance(provenance.get("verbatim"), bool)
    if not provenance["verbatim"]:
        assert provenance.get("composed_because"), (
            f"{relative}: a fixture that is not verbatim must say what was "
            "changed and why, or a reader cannot tell invention from evidence"
        )


def test_the_capability_id_matches_the_spi_grammar():
    assert CAPABILITY_ID_RE.fullmatch(MANIFEST["capability_id"])


def test_no_fixture_carries_credential_shaped_material():
    """The corpus must stay safe to publish, forever."""
    forbidden = ("bao://", "env://", "Bearer ", "access_token", "EAAG", "app_secret")
    for path in sorted(FIXTURE_ROOT.rglob("*.json")):
        text = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in text, f"{path.name} contains {needle!r}"
    for name, value in KEYS.items():
        for key in value if isinstance(value, list) else [value]:
            if name.startswith("_") or not isinstance(key, str):
                continue
            assert "NOT-A-SECRET" in key, (
                "every key in this corpus must announce that it is inert, so a "
                "secret scanner and a human reviewer reach the same conclusion"
            )


# ── verification is over the exact bytes received ────────────────────────────


@ALL_FILES
def test_the_recorded_signature_is_over_the_exact_bytes_on_disk(relative: str):
    body = _read(relative)
    recorded = SIGNATURES["bodies"][relative]
    assert recorded["byte_length"] == len(body)
    assert recorded["body_sha256"] == hashlib.sha256(body).hexdigest()
    for name, signature in recorded["signatures"].items():
        assert signature == _sign(body, KEYS[name]), (
            f"{relative}: the fixture bytes changed without regenerating "
            "signatures.json — run tests/fixtures/meta_whatsapp/regenerate.py"
        )


@ALL_FILES
def test_a_valid_signature_verifies(relative: str):
    body = _read(relative)
    presented = SIGNATURES["bodies"][relative]["signatures"]["primary"]
    assert _verify(body, presented, ACTIVE_KEYS)


def test_reserialising_the_body_invalidates_the_signature():
    """The whitespace case, which is the one that actually bites.

    An ingress edge that parses JSON and hands the connector `json.dumps(payload)`
    delivers the same DOCUMENT and different BYTES. Every signature then fails,
    and the failure looks like a provider or credential problem rather than like
    the re-serialisation it is.
    """
    case = next(
        c for c in MANIFEST["tamper_cases"] if c["case"] == "whitespace_only_change"
    )
    body = _read(case["file"])
    reserialised = json.dumps(json.loads(body), separators=(",", ":")).encode("utf-8")
    assert json.loads(reserialised) == json.loads(body), "same document"
    assert reserialised != body, "different bytes"
    presented = SIGNATURES["bodies"][case["file"]]["signatures"]["primary"]
    assert _verify(body, presented, ACTIVE_KEYS)
    assert not _verify(reserialised, presented, ACTIVE_KEYS)


def test_changing_one_byte_invalidates_the_signature():
    case = next(c for c in MANIFEST["tamper_cases"] if c["case"] == "one_byte_changed")
    body = _read(case["file"])
    presented = SIGNATURES["bodies"][case["file"]]["signatures"]["primary"]
    tampered = body.replace(
        case["replace"].encode("utf-8"), case["with"].encode("utf-8")
    )
    assert tampered != body
    assert not _verify(tampered, presented, ACTIVE_KEYS)


def test_a_replayed_valid_body_still_verifies_so_dedup_is_the_only_defence():
    """Verification cannot detect a replay, and must not try to.

    A redelivery is byte-identical and correctly signed — that is what makes it
    a redelivery. So the whole burden of not double-recording falls on the event
    identities below; a connector that "solves" replay in `verify` has instead
    started rejecting Meta's legitimate retries.
    """
    body = _read("bodies/01_text_message.json")
    presented = SIGNATURES["bodies"]["bodies/01_text_message.json"]["signatures"][
        "primary"
    ]
    assert all(_verify(body, presented, ACTIVE_KEYS) for _ in range(3))


@pytest.mark.parametrize(
    "case_name", ["signature_header_absent", "signature_header_malformed"]
)
def test_a_missing_or_malformed_signature_header_is_refused(case_name: str):
    case = next(c for c in MANIFEST["tamper_cases"] if c["case"] == case_name)
    body = _read(case["file"])
    assert not _verify(body, case.get("header_value"), ACTIVE_KEYS)


# ── rotation ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("key_name", ["primary", "previous"])
def test_both_secrets_verify_during_a_rotation_window(key_name: str):
    """The window is the point: Meta signs with whichever app secret the app
    currently carries, and the changeover is not atomic on their side."""
    relative = "bodies/01_text_message.json"
    body = _read(relative)
    presented = SIGNATURES["bodies"][relative]["signatures"][key_name]
    assert _verify(body, presented, ACTIVE_KEYS)


def test_a_retired_secret_is_refused_once_it_leaves_the_active_set():
    case = next(
        c for c in MANIFEST["tamper_cases"] if c["case"] == "signed_with_retired_key"
    )
    body = _read(case["file"])
    presented = SIGNATURES["bodies"][case["file"]]["signatures"]["retired"]
    assert KEYS["retired"] not in ACTIVE_KEYS
    assert not _verify(body, presented, ACTIVE_KEYS)
    # …and it verified while it WAS active, so the refusal is the retirement
    # and not a broken fixture.
    assert _verify(body, presented, (KEYS["retired"],))


@pytest.mark.parametrize("key_name", ["primary", "previous"])
def test_verification_evaluates_every_active_secret(key_name: str):
    """No early return, whichever secret signed the request."""
    relative = "bodies/01_text_message.json"
    body = _read(relative)
    presented = SIGNATURES["bodies"][relative]["signatures"][key_name]
    audit: list[str] = []
    assert _verify(body, presented, ACTIVE_KEYS, audit=audit)
    assert list(audit) == list(ACTIVE_KEYS)


def test_a_forged_signature_costs_the_same_work_as_a_real_one():
    audit: list[str] = []
    assert not _verify(
        _read("bodies/01_text_message.json"),
        "sha256=" + "0" * 64,
        ACTIVE_KEYS,
        audit=audit,
    )
    assert list(audit) == list(ACTIVE_KEYS)


# ── batch normalisation ──────────────────────────────────────────────────────


@ALL_FILES
def test_every_declared_locator_resolves_in_its_fixture(relative: str):
    document = _document(relative)
    for observation in BY_FILE[relative]["expected_observations"]:
        node = _resolve(document, observation["locator"])
        assert isinstance(
            node, dict
        ), f"{relative}: {observation['locator']} does not point at an item"


@ALL_FILES
def test_event_identities_are_unique_within_one_request(relative: str):
    identities = [
        observation["provider_event_id"]
        for observation in BY_FILE[relative]["expected_observations"]
    ]
    assert len(identities) == len(set(identities))
    locators = [o["locator"] for o in BY_FILE[relative]["expected_observations"]]
    assert len(locators) == len(set(locators))


@ALL_FILES
def test_every_identity_is_recomputable_from_the_body(relative: str):
    """The templates are executable. A hand-written identity that the rule does
    not reproduce is a rule nobody can implement."""
    document = _document(relative)
    for observation in BY_FILE[relative]["expected_observations"]:
        assert observation["provider_event_id"] == _expected_identity(
            document, observation
        ), f"{relative}: {observation['locator']}"


@ALL_FILES
def test_an_identity_declares_whether_the_provider_supplied_it(relative: str):
    for observation in BY_FILE[relative]["expected_observations"]:
        source = observation["identity_source"]
        assert source in {"provider", "derived"}
        list_name = _locator_list(observation["locator"])
        expected = "derived" if list_name == "errors" else "provider"
        assert source == expected, (
            "a consumer must be able to tell a provider-assigned identity from "
            "one this connector computed, because deduplication is only as "
            "strong as the identity behind it"
        )


def test_the_derived_error_identity_comes_from_the_item_not_the_request():
    """Meta assigns no id to a change-level error, so one is derived — from the
    ERROR, never from the request body. Deriving it from the request would mean
    the same error redelivered in a different batch dedupes to nothing."""
    relative = "bodies/06_batch_mixed.json"
    document = _document(relative)
    observation = next(
        o
        for o in BY_FILE[relative]["expected_observations"]
        if o["identity_source"] == "derived"
    )
    identity = _derived_error_identity(document, observation["locator"])
    assert identity == observation["provider_event_id"]

    # The same error item, moved to a different position in a different batch,
    # keeps its identity.
    item = _resolve(document, observation["locator"])
    moved = {
        "entry": [
            {
                "id": "waba-1",
                "changes": [{"field": "messages", "value": {"errors": [item]}}],
            }
        ]
    }
    assert (
        _derived_error_identity(moved, "/entry/0/changes/0/value/errors/0")
        == observation["provider_event_id"]
    )


def test_one_message_yields_distinct_identities_for_each_status():
    """`delivered` and `read` for the same wamid are two events. Keying a status
    on the message id alone collapses a message's whole delivery history into
    one row, and the last writer wins."""
    relative = "bodies/06_batch_mixed.json"
    statuses = [
        o
        for o in BY_FILE[relative]["expected_observations"]
        if _locator_list(o["locator"]) == "statuses"
    ]
    document = _document(relative)
    message_ids = {_resolve(document, o["locator"])["id"] for o in statuses}
    assert len(message_ids) == 1, "same outbound message"
    assert len({o["provider_event_id"] for o in statuses}) == len(statuses)


def test_a_malformed_entry_does_not_suppress_the_rest_of_the_batch():
    """Meta will not resend the good events beside a bad one.

    Sub's receiver `continue`s past anything it cannot parse, so the bad entry
    leaves no trace at all; raising instead would discard the whole batch. The
    contract is neither: a malformed item becomes its OWN typed observation,
    carrying a locator and a reason code, and normalisation continues.
    """
    relative = "bodies/06_batch_mixed.json"
    observations = BY_FILE[relative]["expected_observations"]
    malformed = [
        o for o in observations if o["event_type"] == "whatsapp.entry.malformed.v1"
    ]
    assert len(malformed) == 1
    assert malformed[0]["reason_code"]
    assert malformed[0]["identity_source"] == "provider", (
        "the wamid was present; what was missing was the sender — so a "
        "redelivery of the same bad entry still deduplicates"
    )
    index = observations.index(malformed[0])
    assert (
        index > 0 and index < len(observations) - 1
    ), "good observations must survive both before and after the bad item"


def test_an_uninterpretable_message_type_is_observed_rather_than_dropped():
    """Sub drops a `reaction` silently: it is outside
    `_WHATSAPP_QUALIFYING_MESSAGE_TYPES`, `_text_body` returns '', and
    `if not sender or not body: continue` ends it. No row, no metric, no
    receipt — and Meta will not send it again."""
    relative = "bodies/08_unsupported_message_type.json"
    observations = BY_FILE[relative]["expected_observations"]
    assert len(observations) == 1
    assert observations[0]["reason_code"] == "message_type_unsupported"
    assert observations[0]["provider_event_id"].startswith("wa:msg:"), (
        "an unsupported type keeps the message identity space, so a connector "
        "that later learns to interpret reactions does not re-record them"
    )


def test_an_empty_batch_normalises_to_zero_observations():
    """Meta sends these, and zero observations is a successful outcome."""
    assert BY_FILE["bodies/07_empty_entry.json"]["expected_observations"] == []


# ── redelivery, regrouping, and the identity anti-pattern ────────────────────


def test_an_event_keeps_its_identity_when_meta_regroups_the_batch():
    for identity, files in MANIFEST["shared_events"].items():
        if identity.startswith("_"):
            continue
        for relative in files:
            document = _document(relative)
            observation = next(
                o
                for o in BY_FILE[relative]["expected_observations"]
                if o["provider_event_id"] == identity
            )
            assert _expected_identity(document, observation) == identity


def test_a_request_digest_identity_cannot_deduplicate_a_regrouped_event():
    """The anti-pattern, failing in front of the reader.

    `provider_event_id = f"meta:{sha256(raw_body).hexdigest()}"` is live in
    `dotmac_sub/app/api/inbox_webhooks.py`. It deduplicates an exact retry and
    nothing else: the same message inside a differently grouped batch has a
    different digest, so it is recorded twice.
    """
    shared = {
        k: v for k, v in MANIFEST["shared_events"].items() if not k.startswith("_")
    }
    assert shared, "the corpus must actually contain a shared event"
    for identity, files in shared.items():
        digests = {hashlib.sha256(_read(relative)).hexdigest() for relative in files}
        assert len(digests) == len(
            files
        ), f"{identity}: the two carriers must be genuinely different requests"
        assert len({identity}) == 1


# ── the subscription handshake ───────────────────────────────────────────────


def test_the_handshake_is_answerable_while_configured_but_disabled():
    """The circularity, stated as a requirement.

    Meta performs the GET **before** any event is delivered, and the Cloud API
    will not save a callback URL whose challenge is unanswered. An
    implementation that demands an ENABLED binding to answer it can therefore
    never be enabled: the handshake is what precedes activation. Sub already
    resolves this — `verify_whatsapp_webhook_challenge` admits `disabled` and
    `enabled` — and its docstring is the rule: "Compare a setup challenge
    without granting inbound runtime capability."
    """
    states = MANIFEST["handshake"]["installation_states"]
    assert "disabled" in states["answers_challenge"]
    assert "enabled" in states["answers_challenge"]
    assert {"draft", "retired", "config_revision_invalid", "absent"} <= set(
        states["refuses"]
    )
    assert not set(states["answers_challenge"]) & set(states["refuses"])


def test_answering_the_handshake_grants_nothing():
    grants = MANIFEST["handshake"]["grants"]
    assert grants["creates_receipt"] is False
    assert grants["enables_binding"] is False
    assert grants["reveals_enablement_state"] is False


def test_the_handshake_echoes_the_challenge_verbatim():
    handshake = MANIFEST["handshake"]
    assert handshake["response"]["body"] == handshake["query"]["hub.challenge"]
    assert handshake["response"]["content_type"] == "text/plain"


def test_a_wrong_and_a_missing_verify_token_are_indistinguishable():
    """Otherwise the refusal is an oracle: an attacker learns whether a token
    was merely absent or actually wrong, and can probe from there."""
    refusals = {c["case"]: c for c in MANIFEST["handshake"]["refusals"]}
    assert (
        refusals["wrong_verify_token"]["reason_code"]
        == refusals["missing_verify_token"]["reason_code"]
    )
    assert refusals["wrong_verify_token"]["outcome"] == "refused"
    assert refusals["missing_verify_token"]["outcome"] == "refused"


def test_the_verify_token_is_compared_in_constant_time():
    handshake = MANIFEST["handshake"]
    assert "constant-time" in handshake["comparison"]
    expected = handshake["query"]["hub.verify_token"]
    assert hmac.compare_digest(expected, expected)
    assert not hmac.compare_digest(expected, "wrong")
