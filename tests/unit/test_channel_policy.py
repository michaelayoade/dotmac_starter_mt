"""Channel policy: a typed reader over one setting (ADR-0006 § 5c).

`docs/inventories/channel-policy-sources.md` found this owner was drawn one size
too large — Sub stores the whole policy as a single JSON setting and resolves it
through ordinary settings precedence, which the kernel already owns. So there is
no table and no service to test, only the resolution order and the write-time
validation.

Sub's precedence has a fifth step above these: a legacy per-event setting that
shadows the document. It is deliberately not ported (a second writer for one
decision), so there is deliberately no test for it.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from dotmac_kernel import channel_policy
from dotmac_kernel.channel_policy import ChannelPolicyError
from dotmac_kernel.models import Tenant
from dotmac_kernel.setting_domains import (
    SettingDomainRegistry,
    active_setting_domains,
    install_setting_domains,
)
from dotmac_kernel.settings_models import DomainSetting, SettingDomain
from dotmac_kernel.settings_resolver import register_specs, upsert_by_key

SPEC = channel_policy.make_spec(domain=SettingDomain("communication"))


@pytest.fixture(autouse=True)
def _registered():
    """A product declares the domain on its manifest; here the test plays that
    part. The kernel deliberately refuses a write to an undeclared domain, so
    inventing one at the write site is not an option — which is the point."""
    from dotmac_kernel.settings_resolver import _REGISTRY

    previous_domains = active_setting_domains()
    install_setting_domains(
        SettingDomainRegistry([("test_communications", "communication")])
    )
    register_specs([SPEC])
    yield
    install_setting_domains(previous_domains)
    # `_REGISTRY` is PROCESS-GLOBAL and `register_specs` has no unregister. A
    # spec left behind here fails `test_every_registered_spec_names_a_declared_
    # domain`, which checks the real assembly's specs against its manifests —
    # and it fails in a DIFFERENT test file, which is a miserable thing to debug.
    _REGISTRY.pop((SPEC.domain, SPEC.key), None)


@pytest.fixture
def tenant(db) -> Tenant:
    tenant = Tenant(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    db.add(tenant)
    db.flush()
    return tenant


def _store(db, tenant, document) -> None:
    upsert_by_key(db, SPEC.domain, SPEC.key, document, tenant_id=tenant.id)
    db.flush()


def _channels(db, tenant, **kwargs) -> tuple[str, ...]:
    return channel_policy.resolve_channels(db, SPEC, tenant_id=tenant.id, **kwargs)


# ── Resolution order ────────────────────────────────────────────────────────


def test_an_event_entry_beats_a_category_entry(db, tenant) -> None:
    _store(
        db,
        tenant,
        {
            "default": ["email"],
            "categories": {"billing": ["email", "sms"]},
            "events": {"invoice_overdue": ["sms"]},
        },
    )
    assert _channels(db, tenant, event="invoice_overdue", category="billing") == (
        "sms",
    )


def test_a_category_entry_beats_the_default(db, tenant) -> None:
    _store(
        db,
        tenant,
        {"default": ["email"], "categories": {"billing": ["email", "sms"]}},
    )
    assert _channels(db, tenant, event="unlisted", category="billing") == (
        "email",
        "sms",
    )


def test_the_default_beats_the_callers_fallback(db, tenant) -> None:
    _store(db, tenant, {"default": ["push"]})
    assert _channels(db, tenant, category="anything", fallback=("email",)) == ("push",)


def test_the_fallback_answers_when_the_policy_says_nothing(db, tenant) -> None:
    """The last resort that keeps an unconfigured deployment reaching the
    customer rather than going silent."""
    _store(db, tenant, {"default": []})
    assert _channels(db, tenant, category="billing", fallback=("email",)) == ("email",)


def test_an_unset_policy_falls_back(db, tenant) -> None:
    assert _channels(db, tenant, category="billing", fallback=("email",)) == ("email",)


def test_order_within_a_list_is_preserved(db, tenant) -> None:
    """The stored type is `json`, and order is part of the value — a caller may
    treat the first entry as primary."""
    _store(db, tenant, {"default": ["sms", "email", "push"]})
    assert _channels(db, tenant) == ("sms", "email", "push")


# ── Validation on WRITE ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "document",
    [
        "not-an-object",
        {"default": "email"},
        {"categories": ["billing"]},
        {"events": {"x": "sms"}},
        {"default": ["email"], "surprise": {}},
        {"categories": {"billing": [1, 2]}},
    ],
)
def test_a_malformed_document_is_refused(document) -> None:
    with pytest.raises(ChannelPolicyError):
        channel_policy.validate_policy_document(document)


def test_a_wellformed_document_validates() -> None:
    channel_policy.validate_policy_document(
        {
            "default": ["email"],
            "categories": {"billing": ["email", "sms"]},
            "events": {"invoice_overdue": []},
        }
    )


def test_an_empty_document_is_valid() -> None:
    """A deployment that has not configured routing yet is not malformed."""
    channel_policy.validate_policy_document({})


# ── Degrading on READ ───────────────────────────────────────────────────────


def test_a_malformed_stored_document_degrades_to_the_fallback(db, tenant) -> None:
    """Defence in depth on the SEND path.

    The writer above already refuses a malformed document, so this row can only
    exist through a path that bypassed it — a hand-edited database, a migration,
    an older schema. Even then one bad document must not become a total delivery
    outage, so the reader degrades to the caller's fallback. The loud failure
    stays on write, where the operator is present to see it.
    """
    db.add(
        DomainSetting(
            tenant_id=tenant.id,
            domain=SPEC.domain,
            key=SPEC.key,
            value_type=SPEC.value_type,
            value_json={"categories": "not-an-object"},
        )
    )
    db.flush()
    assert _channels(db, tenant, category="billing", fallback=("email",)) == ("email",)


def test_the_writer_refuses_the_same_document(db, tenant) -> None:
    """The pair to the test above: malformed input fails LOUDLY on write."""
    from dotmac_kernel.exceptions import BadRequestError

    with pytest.raises(BadRequestError, match="channel_policy"):
        _store(db, tenant, {"categories": "not-an-object"})
