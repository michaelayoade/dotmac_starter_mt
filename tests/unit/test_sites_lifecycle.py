"""Canaries for the site and release-readiness lifecycle."""

from __future__ import annotations

from importlib import import_module
from types import ModuleType

import pytest


def _lifecycle() -> ModuleType:
    try:
        return import_module("dotmac_sites.lifecycle")
    except ModuleNotFoundError as exc:
        if not (exc.name or "").startswith("dotmac_sites"):
            raise
        pytest.fail(
            "dotmac-sites is intentionally absent: this is the Gate 1 RED canary"
        )


def test_site_status_vocabulary_is_small_and_provider_free() -> None:
    lifecycle = _lifecycle()
    assert [value.value for value in lifecycle.SiteState] == ["active", "archived"]


def test_site_archive_is_terminal_but_reassertion_is_idempotent() -> None:
    lifecycle = _lifecycle()
    lifecycle.check_site_transition(
        lifecycle.SiteState.ACTIVE, lifecycle.SiteState.ARCHIVED
    )
    lifecycle.check_site_transition(
        lifecycle.SiteState.ARCHIVED, lifecycle.SiteState.ARCHIVED
    )
    with pytest.raises(lifecycle.TransitionError, match="archived"):
        lifecycle.check_site_transition(
            lifecycle.SiteState.ARCHIVED, lifecycle.SiteState.ACTIVE
        )


def test_revision_readiness_has_one_forward_only_progression() -> None:
    lifecycle = _lifecycle()
    assert [value.value for value in lifecycle.SiteRevisionState] == [
        "draft",
        "ready",
        "retired",
    ]
    lifecycle.check_revision_transition(
        lifecycle.SiteRevisionState.DRAFT, lifecycle.SiteRevisionState.READY
    )
    lifecycle.check_revision_transition(
        lifecycle.SiteRevisionState.READY, lifecycle.SiteRevisionState.RETIRED
    )


def test_revision_cannot_return_to_draft_or_skip_readiness() -> None:
    lifecycle = _lifecycle()
    with pytest.raises(lifecycle.TransitionError, match="ready"):
        lifecycle.check_revision_transition(
            lifecycle.SiteRevisionState.DRAFT, lifecycle.SiteRevisionState.RETIRED
        )
    with pytest.raises(lifecycle.TransitionError, match="draft"):
        lifecycle.check_revision_transition(
            lifecycle.SiteRevisionState.READY, lifecycle.SiteRevisionState.DRAFT
        )


def test_retired_revision_is_terminal_and_reassertion_is_idempotent() -> None:
    lifecycle = _lifecycle()
    lifecycle.check_revision_transition(
        lifecycle.SiteRevisionState.RETIRED, lifecycle.SiteRevisionState.RETIRED
    )
    with pytest.raises(lifecycle.TransitionError, match="retired"):
        lifecycle.check_revision_transition(
            lifecycle.SiteRevisionState.RETIRED, lifecycle.SiteRevisionState.READY
        )
