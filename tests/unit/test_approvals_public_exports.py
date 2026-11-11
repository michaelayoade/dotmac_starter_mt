"""The money-flows composition surface of `dotmac_approvals` (PR A2, gap G3).

ADR 0024 section 14: a composition package may call only names in an owner's
top-level `__all__`. These are the existing request, decision and evaluate
commands of both planes and their result type; exporting them changes no
behaviour, so each must be the very object `dotmac_approvals.service` defines.
"""

from __future__ import annotations

import pytest

import dotmac_approvals
from dotmac_approvals import outbox, service

EXPORTED = (
    "ApprovalOutcome",
    "request_tenant_approval",
    "request_platform_approval",
    "record_tenant_decision",
    "record_platform_decision",
    "evaluate_tenant_approval",
    "evaluate_platform_approval",
    "cancel_tenant_request",
    "cancel_platform_request",
)
EMIT = ("emit_tenant_events", "emit_platform_events")


@pytest.mark.parametrize("name", EXPORTED)
def test_name_is_public_and_is_the_service_object(name: str) -> None:
    assert name in dotmac_approvals.__all__
    assert getattr(dotmac_approvals, name) is getattr(service, name)


@pytest.mark.parametrize("name", EMIT)
def test_emit_function_is_public_and_is_the_outbox_object(name: str) -> None:
    assert name in dotmac_approvals.__all__
    assert getattr(dotmac_approvals, name) is getattr(outbox, name)


def test_every_exported_name_resolves_and_is_unique() -> None:
    names = dotmac_approvals.__all__
    assert len(names) == len(set(names))
    for name in names:
        assert hasattr(dotmac_approvals, name), name
