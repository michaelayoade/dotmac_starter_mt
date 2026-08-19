"""Canaries for the frozen web-analytics public contracts (ADR-0035)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from dotmac_web_analytics import (
    MAX_BATCH_SIZE,
    AttributeKind,
    AttributeRejected,
    CollectionAdmissionEvidence,
    CollectionDecision,
    CollectionRefused,
    ConsentState,
    EventAttributeSpec,
    EventDeclaration,
    EventDeclarationRegistry,
    InvalidContract,
    OpaqueVisitorToken,
    PrivacyPolicyEvidence,
    PropertyRegistration,
    RecordEventBatchCommand,
    UnknownEventDeclaration,
)

NOW = datetime(2026, 8, 18, 12, tzinfo=UTC)


def test_event_vocabulary_is_an_installed_declaration_registry() -> None:
    declaration = EventDeclaration(
        code="forms.completed",
        schema_version=1,
        attributes=(
            EventAttributeSpec(
                "form_kind",
                AttributeKind.ENUM,
                required=True,
                max_length=24,
                allowed_values=("contact", "coverage"),
            ),
        ),
    )
    registry = EventDeclarationRegistry((declaration,))

    assert registry.require("forms.completed", 1) is declaration
    with pytest.raises(UnknownEventDeclaration):
        registry.require("caller.invented_name", 1)


def test_declared_attributes_are_typed_bounded_and_allowlisted() -> None:
    declaration = EventDeclaration(
        code="forms.completed",
        schema_version=2,
        attributes=(
            EventAttributeSpec("step", AttributeKind.INTEGER, minimum=1, maximum=9),
            EventAttributeSpec("kind", AttributeKind.STRING, max_length=8),
        ),
    )

    assert declaration.validate_attributes((("kind", "contact"), ("step", 2))) == (
        ("kind", "contact"),
        ("step", 2),
    )
    with pytest.raises(AttributeRejected, match="unknown"):
        declaration.validate_attributes((("free_form", "anything"),))
    with pytest.raises(AttributeRejected, match="oversized"):
        declaration.validate_attributes((("kind", "far-too-long"),))
    with pytest.raises(AttributeRejected, match="must be integer"):
        declaration.validate_attributes((("step", "2"),))


@pytest.mark.parametrize(
    "name",
    ["email", "customer_id", "subscriber_ref", "auth_token", "revenue_amount"],
)
def test_event_declarations_cannot_allowlist_pii_secrets_or_revenue(name: str) -> None:
    with pytest.raises(InvalidContract, match="reserved"):
        EventAttributeSpec(name, AttributeKind.STRING, max_length=20)


def test_visitor_token_never_reprs_its_value() -> None:
    token = OpaqueVisitorToken("opaque-browser-token-123")
    assert "opaque-browser-token-123" not in repr(token)
    assert "redacted" in repr(token)


def test_consent_is_evaluated_for_each_submission_not_inherited_from_page_load() -> (
    None
):
    page_load = PrivacyPolicyEvidence(
        policy_version="notice-7",
        consent_state=ConsentState.GRANTED,
        decision=CollectionDecision.ALLOW,
        global_privacy_control=False,
        do_not_track=False,
        evaluated_at=NOW,
    )
    assert page_load.decision is CollectionDecision.ALLOW

    later_event = PrivacyPolicyEvidence(
        policy_version="notice-8",
        consent_state=ConsentState.DENIED,
        decision=CollectionDecision.DENY,
        global_privacy_control=True,
        do_not_track=True,
        evaluated_at=NOW,
    )
    assert later_event.decision is CollectionDecision.DENY


def test_a_failed_origin_or_rate_limit_never_becomes_admission_evidence() -> None:
    with pytest.raises(CollectionRefused, match="origin"):
        CollectionAdmissionEvidence(
            adapter_code="web.collect",
            origin="https://site.invalid",
            checked_at=NOW,
            origin_verified=False,
            rate_limit_permitted=True,
        )
    with pytest.raises(CollectionRefused, match="rate limit"):
        CollectionAdmissionEvidence(
            adapter_code="web.collect",
            origin="https://site.invalid",
            checked_at=NOW,
            origin_verified=True,
            rate_limit_permitted=False,
        )


@pytest.mark.parametrize(
    "origin",
    (
        "https://site.invalid/path",
        "https://site.invalid?token=secret",
        "https://user:password@site.invalid",
    ),
)
def test_property_and_admission_origins_are_origin_only(origin: str) -> None:
    with pytest.raises((InvalidContract, CollectionRefused), match="origin"):
        PropertyRegistration(
            tenant_id=uuid.uuid4(),
            property_code="public.site",
            display_name="Public site",
            allowed_origins=(origin,),
            timezone_name="Africa/Lagos",
            raw_retention_days=30,
            replay_evidence_days=60,
        )
    with pytest.raises(CollectionRefused, match="origin"):
        CollectionAdmissionEvidence(
            adapter_code="web.collect",
            origin=origin,
            checked_at=NOW,
            origin_verified=True,
            rate_limit_permitted=True,
        )


def test_origins_are_canonicalized_without_default_ports_or_trailing_slashes() -> None:
    registration = PropertyRegistration(
        tenant_id=uuid.uuid4(),
        property_code="public.site",
        display_name="Public site",
        allowed_origins=("HTTPS://SITE.INVALID:443/",),
        timezone_name="Africa/Lagos",
        raw_retention_days=30,
        replay_evidence_days=60,
    )
    admission = CollectionAdmissionEvidence(
        adapter_code="web.collect",
        origin="HTTPS://SITE.INVALID:443/",
        checked_at=NOW,
        origin_verified=True,
        rate_limit_permitted=True,
    )

    assert registration.allowed_origins == ("https://site.invalid",)
    assert admission.origin == "https://site.invalid"


def test_property_policy_has_no_hidden_retention_default() -> None:
    with pytest.raises(TypeError):
        PropertyRegistration(  # type: ignore[call-arg]
            tenant_id=uuid.uuid4(),
            property_code="public.site",
            display_name="Public site",
            allowed_origins=("https://site.invalid",),
            timezone_name="Africa/Lagos",
        )


def test_replay_evidence_must_outlive_raw_observations() -> None:
    with pytest.raises(InvalidContract, match="outlive"):
        PropertyRegistration(
            tenant_id=uuid.uuid4(),
            property_code="public.site",
            display_name="Public site",
            allowed_origins=("https://site.invalid",),
            timezone_name="Africa/Lagos",
            raw_retention_days=30,
            replay_evidence_days=30,
        )


def test_batch_size_is_bounded_by_the_protocol() -> None:
    with pytest.raises(InvalidContract, match=f"1..{MAX_BATCH_SIZE}"):
        RecordEventBatchCommand(())
