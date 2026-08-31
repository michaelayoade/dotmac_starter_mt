"""Offline candidate parsing. Fetching and review remain outside this package."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from dotmac_runner_transport import ExactHost

from .snapshot import DOMAIN_NAMES, SNAPSHOT

__all__ = ["CandidateSnapshot", "MetadataDrift", "parse_candidate"]


class MetadataDrift(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CandidateSnapshot:
    domains: tuple[ExactHost, ...]
    semantic_sha256: str
    added: tuple[ExactHost, ...]
    removed: tuple[ExactHost, ...]

    @property
    def unchanged(self) -> bool:
        return not self.added and not self.removed


def parse_candidate(document: bytes) -> CandidateSnapshot:
    try:
        payload: Any = json.loads(document)
        values = payload["domains"]["actions_inbound"]["full_domains"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise MetadataDrift("candidate has no exact Actions full-domain field") from exc
    if not isinstance(values, list) or not values:
        raise MetadataDrift("candidate exact-domain field is empty or malformed")
    if not all(isinstance(item, str) for item in values):
        raise MetadataDrift("candidate exact domains must all be strings")
    domains = tuple(sorted({ExactHost(item) for item in values}))
    if len(domains) != len(values):
        raise MetadataDrift("candidate exact-domain field contains duplicates")
    semantic = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                [item.value for item in domains],
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
        ).hexdigest()
    )
    current = {ExactHost(item) for item in DOMAIN_NAMES}
    candidate = set(domains)
    return CandidateSnapshot(
        domains=domains,
        semantic_sha256=semantic,
        added=tuple(sorted(candidate - current)),
        removed=tuple(sorted(current - candidate)),
    )


def assert_current(candidate: CandidateSnapshot) -> None:
    if not candidate.unchanged or candidate.semantic_sha256 != SNAPSHOT.semantic_sha256:
        raise MetadataDrift(
            "provider metadata changed; retain the working snapshot and review the diff"
        )
