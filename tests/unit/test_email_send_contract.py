"""The `email.send.v1` payload contract published by the kernel delivery domain."""

from __future__ import annotations

import pytest
from dotmac_integration.capability_registry import (
    CapabilityContract,
    CapabilityOwner,
    CapabilityPayloadRejected,
)
from dotmac_kernel.delivery_providers import (
    ALLOWED_HEADERS,
    EMAIL_SEND_CAPABILITY_ID,
    EMAIL_SEND_COMMAND_SCHEMA,
)

#: The digest a connector claims (copied, never derived by the connector). A
#: change to the schema changes this; the published contract is then succeeded
#: by a new `.vN` id (ADR-0024 11), not edited.
PUBLISHED_DIGEST = "b746eb0073de9d148cc659dd3e7282a19d40e1e0296ade4a8ec8223ba651be52"

CONTRACT = CapabilityContract(
    capability_id=EMAIL_SEND_CAPABILITY_ID,
    owner=CapabilityOwner("starter", "delivery"),
    summary="Send one already-rendered email message.",
    command_schema=EMAIL_SEND_COMMAND_SCHEMA,
)

GOOD = {"dispatch_id": "11111111-2222-3333-4444-555555555555", "address": "a@b.example"}


def test_id_and_digest_are_pinned() -> None:
    assert EMAIL_SEND_CAPABILITY_ID == "email.send.v1"
    assert CONTRACT.contract_digest == PUBLISHED_DIGEST
    assert CONTRACT.result_schema is None and CONTRACT.observation_schema is None


@pytest.mark.parametrize(
    "payload",
    [
        {**GOOD, "text": "hi"},
        {**GOOD, "html": "<p>x</p>", "subject": "S"},
        {**GOOD, "text": "hi", "html": None, "subject": None},
        {**GOOD, "text": "hi", "headers": {"Reply-To": "h@b.example"}},
        {
            **GOOD,
            "text": "hi",
            "attachments": [
                {
                    "kind": "document",
                    "ref": "d1",
                    "filename": "a.pdf",
                    "content_type": "application/pdf",
                }
            ],
        },
    ],
)
def test_valid_commands_are_accepted(payload: dict[str, object]) -> None:
    CONTRACT.require_command(payload)


@pytest.mark.parametrize(
    "payload",
    [
        GOOD,  # no body part
        {"address": "a@b.example", "text": "x"},
        {**GOOD, "text": "x", "dispatch_id": "bad id\r\nBcc: x"},
        {**GOOD, "text": "x", "address": "no-at-sign"},
        {**GOOD, "text": "x", "address": "a@b.example\r\nBcc: z@y.example"},
        {**GOOD, "text": "x", "headers": {"Bcc": "z@y.example"}},
        {**GOOD, "text": None},
        {**GOOD, "text": None, "html": None},
        {**GOOD, "html": None},
        {**GOOD, "text": "x", "dispatch_id": "abc\n"},
        {**GOOD, "text": "x", "address": "a@b.example\n"},
        {**GOOD, "text": "x", "subject": "a\r\nBcc: z@y.example"},
        {**GOOD, "text": "x", "subject": "a\nb"},
        {**GOOD, "text": "x" * (1024 * 1024 + 1)},
        {**GOOD, "text": "x", "headers": {"Reply-To": "h@b.example\r\nBcc: z@y.x"}},
        {**GOOD, "text": "x", "headers": {"Reply-To": "h@b.example\n"}},
        {**GOOD, "text": "x", "headers": {"Reply-To": "h@b.example" + "x" * 998}},
        {**GOOD, "text": "x", "unknown": 1},
        {**GOOD, "text": "x", "attachments": [{"kind": "document"}]},
        {
            **GOOD,
            "text": "x",
            "attachments": [
                {
                    "kind": "document",
                    "ref": "d",
                    "filename": "a.html",
                    "content_type": "text/html",
                }
            ],
        },
    ],
)
def test_malformed_commands_are_refused(payload: dict[str, object]) -> None:
    with pytest.raises(CapabilityPayloadRejected):
        CONTRACT.require_command(payload)


def test_header_names_follow_the_kernel_allowlist() -> None:
    headers = EMAIL_SEND_COMMAND_SCHEMA["properties"]["headers"]["properties"]  # type: ignore[index]
    assert set(headers) == set(ALLOWED_HEADERS)
