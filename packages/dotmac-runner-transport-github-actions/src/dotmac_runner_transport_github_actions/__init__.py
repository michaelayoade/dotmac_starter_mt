"""GitHub Actions adapter for the provider-neutral runner transport facility."""

from .adapter import ADAPTER, GitHubActionsAdapter
from .collector import CandidateSnapshot, MetadataDrift, assert_current, parse_candidate
from .snapshot import DOMAIN_NAMES, SNAPSHOT

__all__ = [
    "ADAPTER",
    "DOMAIN_NAMES",
    "SNAPSHOT",
    "CandidateSnapshot",
    "GitHubActionsAdapter",
    "MetadataDrift",
    "assert_current",
    "parse_candidate",
]
