"""The classification table moved byte-identically from dotmac-delivery-smtp."""

from __future__ import annotations

import hashlib
import inspect

import pytest
from dotmac_connector_smtp import classify
from dotmac_connector_smtp import config as config_module
from dotmac_connector_smtp.classify import classify_reply

# sha256 of dotmac_delivery_smtp/classify.py (docstring table + everything from
# STAGE_SESSION on), computed from the ORIGINAL file at the commit this was
# moved from. The kernel import block is the only text that differs.
ORIGINAL_CLASSIFY_SHA256 = (
    "05caa00992deed08229340423f3e6ec11c6c52900241d0887affd83fc031f2b4"
)
# sha256 of the whole of dotmac_delivery_smtp/config.py (moved unchanged).
ORIGINAL_CONFIG_SHA256 = (
    "644cc5b27012f0d01db63b10b7e3494efe62bb4d9c9af13d3608e4d4447d6330"
)
# sha256 of classify_reply over the grid below, computed from the ORIGINAL.
ORIGINAL_GRID_SHA256 = (
    "0e299e1f733be36bbcdd28752cbb791c5d1d206f65e5c54293d1067c5364c2f8"
)
ORIGINAL_GRID_ROWS = 29925

STAGES = ["session", "mail", "rcpt", "data", "other"]
CODES = [200, 250, 300, 421, 450, 451, 452, 500, 501, 550, 551, 552, 553, 554, 599]
TEXTS = [
    "",
    "no enhanced code",
    "garbage 5.1.1 not first",
    "5.1.1",
    "  5.1.1 leading space",
    "5.1.1\ttab",
    "5.1.1x suffix",
    "5.1000.1 too wide",
    "5.1.1000 too wide",
]
for _cls in (2, 4, 5):
    for _subj in range(10):
        for _det in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 999):
            TEXTS.append(f"{_cls}.{_subj}.{_det} sample text")


def test_the_table_text_and_logic_are_byte_identical_to_the_original() -> None:
    source = inspect.getsource(classify)
    head, body = source.index("from __future__"), source.index("STAGE_SESSION")
    assert (
        hashlib.sha256((source[:head] + source[body:]).encode()).hexdigest()
        == ORIGINAL_CLASSIFY_SHA256
    )


def test_config_is_unchanged_from_the_original() -> None:
    digest = hashlib.sha256(inspect.getsource(config_module).encode()).hexdigest()
    assert digest == ORIGINAL_CONFIG_SHA256


def test_every_cell_of_the_grid_classifies_as_the_original_did() -> None:
    rows = [
        f"{stage}|{code}|{text!r}|{classify_reply(stage, code, text)}"
        for stage in STAGES
        for code in CODES
        for text in TEXTS
    ]
    assert len(rows) == ORIGINAL_GRID_ROWS
    assert hashlib.sha256("\n".join(rows).encode()).hexdigest() == ORIGINAL_GRID_SHA256


def test_the_outcome_literals_are_the_kernel_receipt_statuses() -> None:
    assert (
        classify.DELIVERY_BOUNCED,
        classify.DELIVERY_FAILED,
        classify.DELIVERY_REJECTED,
    ) == ("bounced", "failed", "rejected")


@pytest.mark.parametrize(
    ("stage", "code", "text", "expected"),
    [
        ("rcpt", 550, "5.1.1 user unknown", "bounced"),
        ("rcpt", 553, "5.1.3 bad address syntax", "bounced"),
        ("rcpt", 550, "5.1.2 bad domain", "bounced"),
        ("rcpt", 550, "5.1.6 mailbox moved", "bounced"),
        ("rcpt", 550, "5.1.10 null mx", "bounced"),
        ("rcpt", 550, "5.1.7 bad sender mailbox syntax", "operator_fault"),
        ("rcpt", 550, "5.1.8 bad sender system address", "operator_fault"),
        ("rcpt", 550, "5.1.0 other address status", "rejected"),
        ("rcpt", 550, "5.7.1 relay denied", "rejected"),
        ("rcpt", 552, "5.2.2 mailbox full", "failed"),
        ("rcpt", 450, "4.2.0 greylisted", "failed"),
        ("rcpt", 554, "5.4.4 unable to route", "failed"),
        ("rcpt", 550, "4.1.1 class mismatch is not trusted", "rejected"),
        ("mail", 550, "5.7.1 sender policy", "operator_fault"),
        ("mail", 451, "4.3.0 local error", "failed"),
        ("data", 554, "5.6.0 content refused", "rejected"),
        ("data", 421, "4.7.0 slow down", "failed"),
        ("session", 554, "5.3.2 no service", "failed"),
        ("rcpt", 300, "odd", "failed"),
    ],
)
def test_readable_spot_checks(stage: str, code: int, text: str, expected: str) -> None:
    assert classify_reply(stage, code, text) == expected
