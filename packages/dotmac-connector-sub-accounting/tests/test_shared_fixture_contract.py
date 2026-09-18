"""Contract test: ``map_item`` against Sub's own real, shared fixture.

``tests/fixtures/shared_invoice_accounting_sync_v2_sample.json`` is copied
verbatim from dotmac_sub's `tests/fixtures/invoice_accounting_sync_v2_sample.json`
at commit ``d67d9d968`` (branch ``feat/invoice-accounting-sync-canonical-digest``).
It is Sub's own canonical-digest fixture — a realistic 2-line, discounted,
BLOCKED-disposition invoice whose embedded ``projection_digest`` is Sub's
real, independently-verified-twice hardcoded oracle value, not something
this repo computes. If Sub's fixture is ever updated, this copy must be
updated to match, by hand — dotmac_sub and this connector are independent
git repositories with no shared build/dependency graph or cross-repo CI job;
consistency is enforced by this provenance note plus each repo's own test
suite, not by a shared file.

This test is deliberately separate from ``test_mapping.py``: it exists to
catch field-name drift in ``mapping.py`` against REAL Sub data, not just
synthetic connector-only fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path

from dotmac_connector_sub_accounting.mapping import map_item

FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "shared_invoice_accounting_sync_v2_sample.json"
)


def test_maps_subs_real_shared_fixture_without_error() -> None:
    raw = json.loads(FIXTURE_PATH.read_text())

    event, _, _ = map_item(raw)

    assert event.payload["source_invoice_id"] == "11111111-1111-1111-1111-111111111111"
    assert event.payload["source_account_id"] == "22222222-2222-2222-2222-222222222222"
    # Sub's exact string, preserved unreformatted -- not normalized to
    # `+00:00`.
    assert event.payload["source_updated_at"] == "2026-01-15T10:30:00Z"
    assert event.payload["source_kind"] == "native"
    assert event.payload["disposition"] == "blocked"
    assert event.payload["source_total_amount"] == "147000.00"
    assert event.payload["source_currency"] == "NGN"
    assert event.payload["source_issued_at"] == "2026-01-15T10:30:00Z"
    assert event.payload["source_due_at"] == "2026-02-14T10:30:00Z"

    # The fixture's single issue has `line_id: null` and
    # `expected_amount: null` -- a header-level "discount has nowhere to
    # allocate" issue with no specific line and no "expected" amount, only
    # an actual one. Both are OMITTED keys, not present with value `None`.
    assert event.payload["issues"] == [
        {"code": "discount_allocation_undefined", "actual_amount": "10000.00"}
    ]
    assert "source_line_id" not in event.payload["issues"][0]
    assert "expected_amount" not in event.payload["issues"][0]

    # The single most important assertion: the connector forwards Sub's own
    # real digest byte-identical, unrecomputed.
    assert event.payload["digest_version"] == 1
    assert (
        event.payload["projection_digest"]
        == "0dfecf2e1f96a2d1a63eb8e5e9f78f0aefbf661ab2c424b841b5bb2eda85aa7c"
    )
