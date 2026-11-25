"""Q-F release of frozen expected withholding: refusals that need no database."""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import dotmac_billing
import pytest
from dotmac_billing import manifest, models
from dotmac_billing.commands import (
    WITHHOLDING_RELEASE_PERMISSION,
    ReleaseWithholdingComponent,
    ReverseWithholdingRelease,
)
from dotmac_billing.errors import BillingRuleViolation
from dotmac_billing.withholding_release import (
    RELEASED_EVENT_TYPE,
    release_withholding_component,
    reverse_withholding_release,
)
from dotmac_kernel.cache import PlatformScope, TenantScope
from dotmac_kernel.money import Currency, Money

TENANT = TenantScope(uuid4())
MONEY = Money.of("5.00", Currency("NGN", 2))


class _Reader:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed
        self.asked: list[str] = []

    def holds(self, *, scope, actor_ref, code) -> bool:
        self.asked.append(code)
        return self.allowed


def _command(scope=TENANT, **changes):
    base = ReleaseWithholdingComponent(
        scope, uuid4(), uuid4(), uuid4(), "cmd-1", "actor-1", "not deducted", MONEY
    )
    return replace(base, **changes)


def _refused(call) -> str:
    with pytest.raises(BillingRuleViolation) as caught:
        call()
    return caught.value.code


def test_the_module_declares_the_permission_and_the_event_for_admin_only():
    permission = next(
        p
        for p in manifest.module.permissions
        if p.code == WITHHOLDING_RELEASE_PERMISSION
    )
    assert permission.code == "billing.withholding.release"
    assert permission.default_roles == ("admin",)
    assert RELEASED_EVENT_TYPE == "billing.withholding.released.v1"
    assert RELEASED_EVENT_TYPE in manifest.module.outbox_event_types
    assert "withholding_releases" in models.TENANT_TABLES
    assert "withholding_releases" not in " ".join(models.PLATFORM_TABLES)


def test_the_public_surface_exports_the_release_names():
    for name in (
        "ReleaseWithholdingComponent",
        "ReverseWithholdingRelease",
        "release_withholding_component",
        "get_withholding_release",
        "get_withholding_component_position",
        "WithholdingReleaseV1",
        "WithholdingComponentPositionV1",
    ):
        assert name in dotmac_billing.__all__
        assert hasattr(dotmac_billing, name)


def test_the_platform_plane_is_refused_before_anything_else():
    assert (
        _refused(
            lambda: release_withholding_component(
                None,
                scope=PlatformScope(),
                command=_command(PlatformScope()),
                permissions=_Reader(True),
            )
        )
        == "billing.withholding_release_platform_unsupported"
    )


def test_no_reader_or_a_denied_actor_refuses_before_any_write():
    assert (
        _refused(
            lambda: release_withholding_component(
                None, scope=TENANT, command=_command(), permissions=None
            )
        )
        == "billing.withholding_release_permission_unavailable"
    )
    reader = _Reader(False)
    assert (
        _refused(
            lambda: release_withholding_component(
                None, scope=TENANT, command=_command(), permissions=reader
            )
        )
        == "billing.withholding_release_permission_denied"
    )
    assert reader.asked == [WITHHOLDING_RELEASE_PERMISSION]


@pytest.mark.parametrize(
    "changes",
    [
        {"reason": "  "},
        {"reason": "x" * 2001},
        {"command_id": ""},
        {"actor_ref": "a" * 256},
        {"evidence_ref": " "},
    ],
)
def test_blank_or_unbounded_text_is_refused(changes):
    reader = _Reader(True)
    assert (
        _refused(
            lambda: release_withholding_component(
                None, scope=TENANT, command=_command(**changes), permissions=reader
            )
        )
        == "billing.withholding_release_evidence_required"
    )
    assert reader.asked == []  # text is checked before the permission is asked


def test_a_scope_mismatch_is_refused_and_the_undo_has_the_same_gates():
    other = TenantScope(uuid4())
    assert (
        _refused(
            lambda: release_withholding_component(
                None, scope=TENANT, command=_command(other), permissions=_Reader(True)
            )
        )
        == "billing.scope_mismatch"
    )
    undo = ReverseWithholdingRelease(TENANT, uuid4(), "c", "a", "r")
    assert (
        _refused(
            lambda: reverse_withholding_release(
                None, scope=TENANT, command=undo, permissions=None
            )
        )
        == "billing.withholding_release_permission_unavailable"
    )
    assert (
        _refused(
            lambda: reverse_withholding_release(
                None, scope=TENANT, command=undo, permissions=_Reader(False)
            )
        )
        == "billing.withholding_release_permission_denied"
    )
