from __future__ import annotations

import json

import pytest
from dotmac_runner_transport_github_actions import DOMAIN_NAMES, SNAPSHOT
from dotmac_runner_transport_github_actions.collector import (
    MetadataDrift,
    assert_current,
    parse_candidate,
)


def _document(domains: list[str]) -> bytes:
    return json.dumps(
        {"domains": {"actions_inbound": {"full_domains": domains}}}
    ).encode("utf-8")


def test_pinned_snapshot_round_trips_through_candidate_parser() -> None:
    candidate = parse_candidate(_document(list(reversed(DOMAIN_NAMES))))
    assert candidate.unchanged
    assert candidate.semantic_sha256 == SNAPSHOT.semantic_sha256
    assert_current(candidate)


def test_added_provider_domain_is_reported_never_admitted() -> None:
    candidate = parse_candidate(_document([*DOMAIN_NAMES, "future.provider.invalid"]))
    assert [item.value for item in candidate.added] == ["future.provider.invalid"]
    with pytest.raises(MetadataDrift, match="review"):
        assert_current(candidate)


def test_removed_result_storage_domain_is_reported() -> None:
    values = [
        item
        for item in DOMAIN_NAMES
        if item != "productionresultssa0.blob.core.windows.net"
    ]
    candidate = parse_candidate(_document(values))
    assert [item.value for item in candidate.removed] == [
        "productionresultssa0.blob.core.windows.net"
    ]
    with pytest.raises(MetadataDrift):
        assert_current(candidate)


@pytest.mark.parametrize(
    "document",
    [b"{}", b"not-json", _document([]), _document(["one.invalid", "one.invalid"])],
)
def test_malformed_empty_and_duplicate_candidates_refuse(document: bytes) -> None:
    with pytest.raises(MetadataDrift):
        parse_candidate(document)
