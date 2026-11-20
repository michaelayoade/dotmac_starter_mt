"""Pure structural custody contracts; hosted execution only."""

from dataclasses import FrozenInstanceError, replace
from uuid import uuid4

import pytest
from dotmac_billing.opening_contracts import OpeningEvidenceError
from dotmac_billing.opening_window_contracts import (
    OpeningWindowAdmissionV1,
    OpeningWindowV1,
    opening_admission_population_digest,
)
from dotmac_kernel.cache import PlatformScope, TenantScope

from tests.unit import test_billing_opening_manifest as evidence


def _admission():
    candidate = evidence._candidate()
    return OpeningWindowAdmissionV1(
        candidate.manifest.scope,
        uuid4(),
        uuid4(),
        candidate.partition.account_id,
        "request:1",
        "a" * 64,
        "b" * 64,
    )


def test_population_digest_is_order_independent_full_field_and_scope_bound():
    first = replace(_admission(), scope=TenantScope(uuid4()))
    second = replace(first, receipt_id=uuid4(), request_identity="request:2")
    digest = opening_admission_population_digest((first, second))
    assert digest == opening_admission_population_digest((second, first))
    assert digest != opening_admission_population_digest((first,))
    for field, value in (
        ("scope", PlatformScope()),
        ("window_id", uuid4()),
        ("receipt_id", uuid4()),
        ("billing_account_id", uuid4()),
        ("request_identity", "different"),
        ("request_digest", "c" * 64),
        ("manifest_fingerprint", "d" * 64),
    ):
        assert digest != opening_admission_population_digest(
            (replace(first, **{field: value}), second)
        )
    assert opening_admission_population_digest(()) != digest


def test_frozen_full_manifest_window_rejects_mutable_or_coerced_evidence():
    candidate = evidence._candidate()
    window = OpeningWindowV1(
        candidate.manifest.scope,
        uuid4(),
        uuid4(),
        1,
        "a" * 64,
        candidate.manifest,
        "fixture_restricted",
        123,
        "authority:1",
        evidence.NOW,
    )
    with pytest.raises(FrozenInstanceError):
        window.principal_name = "other"
    for field, value in (
        ("principal_oid", True),
        ("manifest", {}),
        ("epoch", str(uuid4())),
        ("principal_name", " "),
    ):
        with pytest.raises(OpeningEvidenceError):
            replace(window, **{field: value})


def test_dual_control_defaults_off_is_closed_and_the_last_field():
    from dataclasses import fields

    from dotmac_billing.opening_window_queries import _legacy_hold, _legacy_window

    candidate = evidence._candidate()
    args = (
        candidate.manifest.scope,
        uuid4(),
        uuid4(),
        1,
        "a" * 64,
        candidate.manifest,
        "fixture_restricted",
        123,
        "authority:1",
        evidence.NOW,
    )
    window = OpeningWindowV1(*args)  # existing positional calls still work
    assert window.dual_control == "off"  # the D-9 default
    assert OpeningWindowV1(*args, "on").dual_control == "on"
    assert [f.name for f in fields(OpeningWindowV1)][-1] == "dual_control"
    for bad in ("maybe", "", None, True):
        with pytest.raises(OpeningEvidenceError):
            OpeningWindowV1(*args, bad)
    # A stored window with no key predates bi_0019 and reads as "on".
    stored = {"window": {"window_id": "x"}, "verification": {}}
    assert _legacy_window(stored)["window"]["dual_control"] == "on"
    assert "dual_control" not in stored["window"]  # the stored bytes are untouched
    kept = {"window": {"dual_control": "off"}}
    assert _legacy_window(kept) is kept
    assert _legacy_hold({"maker_ref": "m"})["dual_control"] == "on"
    already = {"dual_control": "off"}
    assert _legacy_hold(already) is already
