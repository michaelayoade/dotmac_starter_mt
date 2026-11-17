"""Message parts, header allowlist, attachment refs and fingerprint v2 (K-1).

The v1 golden vectors below were computed from the code BEFORE parts existed.
They are the back-compat contract: a legacy-shaped message must keep producing
the digest already stored on existing receipts, so replay still matches.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from dotmac_kernel import consent, delivery
from dotmac_kernel.delivery_models import DELIVERY_ACCEPTED, DELIVERY_FAILED
from dotmac_kernel.delivery_providers import (
    ALLOWED_HEADERS,
    FEATURE_ATTACHMENTS,
    FEATURE_HEADERS,
    FEATURE_HTML,
    AttachmentRef,
    OutboundMessage,
    ProviderResult,
    Sent,
    send,
)
from dotmac_kernel.models import Tenant

_D = UUID(int=1)


def _m(**kwargs) -> OutboundMessage:
    defaults = {
        "dispatch_id": _D,
        "channel": "email",
        "address": "jane@example.com",
        "body": None,
        "category": "billing",
    }
    return OutboundMessage(**{**defaults, **kwargs})


def _doc(**kw) -> AttachmentRef:
    base = {
        "kind": "document",
        "ref": "doc-1",
        "filename": "invoice.pdf",
        "content_type": "application/pdf",
    }
    return AttachmentRef(**{**base, **kw})


# ── v1 golden vectors (computed before the change) ──────────────────────────

V1_VECTORS = {
    "basic": (
        {"address": "Jane@Example.com", "body": "hello", "category": "Billing"},
        "2bf9224bc6b95ea9ef1ed287dd01a8d57dcb6a1fb9b47dda5dd7db80eeefcbf6",
    ),
    "subject": (
        {
            "address": "Jane@Example.com",
            "body": "hello",
            "category": "Billing",
            "subject": "Your invoice",
        },
        "27f6e450fd0efe8704d3433539f833f2e3b36be58f2a4a2880af3595c5c214aa",
    ),
    "unicode": (
        {
            "address": "Jane@Example.com",
            "category": "Billing",
            "subject": "Facture café ☕",
            "body": "Bonjour\r\né\u0000x",
        },
        "8fc6b5ebdfc723541b3875f88cbc99a9a747ffb7f8138063269b80076a035645",
    ),
    "empty_body": (
        {"address": "Jane@Example.com", "body": "", "category": "Billing"},
        "4788b19396a35b95f0df2caa05e727a27ff3ae145b69a6b8d2bbdb0e0bd4bc44",
    ),
    "sms": (
        {
            "channel": "sms",
            "address": "+15550100",
            "body": "Pay now",
            "category": "marketing",
        },
        "d341ed392b449d47c8baa654d26a3ec0ece7f32fcc3877e4effc47970e3a350e",
    ),
    "whatsapp": (
        {
            "channel": "whatsapp",
            "address": "+15550101",
            "body": "hi",
            "category": "Billing",
        },
        "99a03c8af25d1afbf73f5f82e0cb1bd6b2d956fe2149ecd97913608bb0408038",
    ),
}


@pytest.mark.parametrize("name", sorted(V1_VECTORS))
def test_v1_golden_vectors_are_unchanged(name) -> None:
    kwargs, expected = V1_VECTORS[name]
    assert _m(**kwargs).request_fingerprint == expected


@pytest.mark.parametrize("name", sorted(V1_VECTORS))
def test_text_is_the_same_message_as_legacy_body(name) -> None:
    kwargs, expected = V1_VECTORS[name]
    kwargs = dict(kwargs)
    kwargs["text"] = kwargs.pop("body")
    assert _m(**kwargs).request_fingerprint == expected


# ── v2 golden vectors ───────────────────────────────────────────────────────


def test_v2_golden_vectors() -> None:
    assert (
        _m(html="<p>hi</p>").request_fingerprint
        == "2005fd1852fc97d7677ad14530500ff794e0ff125c4fcc40f67cb649d548c447"
    )
    assert (
        _m(text="hi", html="<p>hi</p>").request_fingerprint
        == "6dc15c2cb64e89c80dc013a4c89fab737a3f34d0a36da1f53341236984bae972"
    )
    assert (
        _m(
            text="hi",
            headers={
                "reply-to": "a@example.com",
                "List-Unsubscribe": "<https://example.com/u>",
            },
        ).request_fingerprint
        == "fcc026471697f571d767f8b263d9d16f8a5146e1bf533486a19673c0b78eabf6"
    )
    assert (
        _m(text="hi", attachments=[_doc()]).request_fingerprint
        == "4971e44fb14ace29241d6ee8634707f932f14578169c0ed19b646727df85151a"
    )


# ── Collisions ──────────────────────────────────────────────────────────────


def test_moving_bytes_between_parts_changes_the_fingerprint() -> None:
    fps = {
        _m(text="ab", html="c").request_fingerprint,
        _m(text="a", html="bc").request_fingerprint,
        _m(text="abc", html="").request_fingerprint,
        _m(text="", html="abc").request_fingerprint,
        _m(html="abc").request_fingerprint,
        _m(text="abc").request_fingerprint,
        _m(text="abc", subject="").request_fingerprint,
    }
    assert len(fps) == 7


def test_text_and_html_are_not_interchangeable() -> None:
    assert (
        _m(text="x", html="y").request_fingerprint
        != _m(text="y", html="x").request_fingerprint
    )


def test_moving_bytes_between_headers_and_parts_changes_the_fingerprint() -> None:
    a = _m(text="x", headers={"Reply-To": "a@example.com"})
    b = _m(text="x", headers={"Reply-To": "a@example.co", "References": "m"})
    c = _m(text="xReply-To", headers={"Reply-To": "a@example.com"})
    assert (
        len({a.request_fingerprint, b.request_fingerprint, c.request_fingerprint}) == 3
    )


def test_header_value_delimiters_cannot_forge_another_header() -> None:
    one = _m(
        text="x",
        headers={"Reply-To": "a@example.com", "In-Reply-To": "<1@example.com>"},
    )
    two = _m(
        text="x", headers={"Reply-To": 'a@example.com","In-Reply-To":"<1@example.com>'}
    )
    assert one.request_fingerprint != two.request_fingerprint


def test_header_name_case_and_order_do_not_change_the_fingerprint() -> None:
    a = _m(text="x", headers={"reply-to": "a@example.com", "REFERENCES": "<m>"})
    b = _m(text="x", headers={"References": "<m>", "Reply-To": "a@example.com"})
    assert a.request_fingerprint == b.request_fingerprint
    assert dict(a.headers) == {"Reply-To": "a@example.com", "References": "<m>"}


def test_attachment_fields_and_order_are_all_covered() -> None:
    base = _m(text="x", attachments=[_doc()])
    variants = [
        _m(text="x", attachments=[_doc(kind="storage")]),
        _m(text="x", attachments=[_doc(ref="doc-2")]),
        _m(text="x", attachments=[_doc(filename="other.pdf")]),
        _m(text="x", attachments=[_doc(content_type="text/plain")]),
        _m(text="x", attachments=[_doc(), _doc(ref="doc-2")]),
        _m(text="x"),
    ]
    fps = {v.request_fingerprint for v in variants} | {base.request_fingerprint}
    assert len(fps) == 7
    two = [_doc(), _doc(ref="doc-2")]
    assert (
        _m(text="x", attachments=two).request_fingerprint
        != _m(text="x", attachments=two[::-1]).request_fingerprint
    )


def test_attachment_field_boundary_cannot_collide() -> None:
    a = _m(text="x", attachments=[_doc(ref="ab", filename="c.pdf")])
    b = _m(text="x", attachments=[_doc(ref="a", filename="bc.pdf")])
    assert a.request_fingerprint != b.request_fingerprint


def test_a_v2_message_never_equals_a_v1_digest() -> None:
    legacy = _m(body="hi")
    assert legacy.request_fingerprint != _m(text="hi", html="").request_fingerprint


# ── Parts: construction rules ───────────────────────────────────────────────


def test_body_mirrors_text_both_ways() -> None:
    legacy = _m(body="hi")
    assert legacy.text == "hi" and legacy.body == "hi"
    new = _m(text="hi")
    assert new.body == "hi" and new.text == "hi"
    assert legacy == new and hash(legacy) == hash(new)


def test_html_only_has_no_text_or_body() -> None:
    message = _m(html="<p>x</p>")
    assert message.text is None and message.body is None


def test_body_and_text_with_different_values_are_refused() -> None:
    with pytest.raises(ValueError, match="legacy name"):
        _m(body="a", text="b")


def test_body_and_text_with_equal_values_are_accepted() -> None:
    assert _m(body="a", text="a").text == "a"


def test_at_least_one_part_is_required() -> None:
    with pytest.raises(ValueError, match="at least one"):
        _m()
    with pytest.raises(ValueError, match="at least one"):
        _m(body=None, text=None, html=None)


def test_empty_string_part_counts_as_given() -> None:
    assert _m(body="").text == ""


def test_parts_must_be_strings() -> None:
    with pytest.raises(ValueError, match="html must be a string"):
        _m(text="x", html=b"<p>")  # type: ignore[arg-type]


def test_html_and_headers_are_email_only() -> None:
    with pytest.raises(ValueError, match="email channel"):
        _m(channel="sms", address="+15550100", text="x", html="<p>")
    with pytest.raises(ValueError, match="email channel"):
        _m(
            channel="sms",
            address="+15550100",
            text="x",
            headers={"Reply-To": "a@example.com"},
        )


@pytest.mark.parametrize("bad", ["a\nb", "a\rb", "a\x00b"])
def test_subject_injection_characters_are_refused(bad) -> None:
    with pytest.raises(ValueError, match="subject"):
        _m(text="x", subject=bad)


# ── Header allowlist ────────────────────────────────────────────────────────


def test_the_allowlist_is_exactly_this() -> None:
    assert ALLOWED_HEADERS == (
        "In-Reply-To",
        "List-Unsubscribe",
        "List-Unsubscribe-Post",
        "References",
        "Reply-To",
    )


@pytest.mark.parametrize(
    "name",
    [
        "Message-ID",
        "message-id",
        "From",
        "To",
        "Cc",
        "Bcc",
        "Subject",
        "Content-Type",
        "X-Custom",
        "Reply-To\r\nBcc",
        "Reply-To\n",
        "Reply-To ",
        "Reply-To:",
        "",
    ],
)
def test_a_header_outside_the_allowlist_is_refused(name) -> None:
    with pytest.raises(ValueError, match="not allowed"):
        _m(text="x", headers={name: "a@example.com"})


@pytest.mark.parametrize(
    "value",
    [
        "a@example.com\r\nBcc: evil@example.com",
        "a@example.com\nBcc: x",
        "a@example.com\rx",
        "a@example.com\x00",
        "a@example.com\x1b",
        "a@example.com\x7f",
        "a@example.com\t",
        "a@example.com" + chr(0x2028) + "x",
        "a@example.com\u0085x",
        "café@example.com",
        "",
        "  ",
        " a@example.com",
    ],
)
def test_header_values_with_controls_or_padding_are_refused(value) -> None:
    with pytest.raises(ValueError, match="Reply-To"):
        _m(text="x", headers={"Reply-To": value})


def test_header_sizes_are_bounded() -> None:
    _m(text="x", headers={"References": "a" * 998})
    with pytest.raises(ValueError, match="998"):
        _m(text="x", headers={"References": "a" * 999})
    with pytest.raises(ValueError, match="exceed"):
        _m(
            text="x",
            headers={
                "References": "a" * 998,
                "In-Reply-To": "b" * 998,
                "Reply-To": "c" * 998,
                "List-Unsubscribe": "d" * 998,
                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            },
        )


def test_duplicate_headers_by_case_are_refused() -> None:
    with pytest.raises(ValueError, match="more than once"):
        _m(text="x", headers={"Reply-To": "a@example.com", "reply-to": "b@example.com"})


def test_non_string_header_is_refused() -> None:
    with pytest.raises(ValueError, match="strings"):
        _m(text="x", headers={"Reply-To": 1})  # type: ignore[dict-item]


def test_one_click_post_needs_its_exact_value_and_a_list_unsubscribe() -> None:
    ok = {
        "List-Unsubscribe": "<https://example.com/u>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }
    assert _m(text="x", headers=ok).headers == ok
    with pytest.raises(ValueError, match="One-Click"):
        _m(text="x", headers={**ok, "List-Unsubscribe-Post": "yes"})
    with pytest.raises(ValueError, match="requires"):
        _m(text="x", headers={"List-Unsubscribe-Post": ok["List-Unsubscribe-Post"]})


def test_headers_are_immutable() -> None:
    message = _m(text="x", headers={"Reply-To": "a@example.com"})
    with pytest.raises(TypeError):
        message.headers["Reply-To"] = "evil@example.com"  # type: ignore[index]


def test_caller_mutation_after_construction_cannot_change_the_message() -> None:
    raw = {"Reply-To": "a@example.com"}
    message = _m(text="x", headers=raw)
    before = message.request_fingerprint
    raw["Reply-To"] = "evil@example.com"
    assert message.request_fingerprint == before


# ── Attachment refs ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "filename",
    [
        "",
        " ",
        ".",
        "..",
        "a/b.pdf",
        "../a.pdf",
        "a\\b.pdf",
        "a\nb.pdf",
        "a\rb.pdf",
        "a\x00b.pdf",
        "a\x1fb.pdf",
        "a‮b.pdf",
        " a.pdf",
        "a" * 256,
    ],
)
def test_bad_filenames_are_refused(filename) -> None:
    with pytest.raises(ValueError, match="filename"):
        _doc(filename=filename)


def test_filename_bound_is_inclusive() -> None:
    assert _doc(filename="a" * 255).filename == "a" * 255


@pytest.mark.parametrize(
    "content_type",
    [
        "",
        "pdf",
        "application/pdf; charset=utf-8",
        "application/pdf\r\nX: y",
        "application/pdf\n",
        "Application/PDF",
        "application/",
        "/pdf",
        "application/pdf/x",
        "text/plain boundary",
    ],
)
def test_bad_content_types_are_refused(content_type) -> None:
    with pytest.raises(ValueError, match="content_type"):
        _doc(content_type=content_type)


@pytest.mark.parametrize(
    "content_type", ["application/pdf", "image/svg+xml", "text/csv"]
)
def test_good_content_types_are_accepted(content_type) -> None:
    assert _doc(content_type=content_type).content_type == content_type


@pytest.mark.parametrize("kind", ["", "Document", "1doc", "a b", "a\n", "k" * 65])
def test_bad_kinds_are_refused(kind) -> None:
    with pytest.raises(ValueError, match="kind"):
        _doc(kind=kind)


@pytest.mark.parametrize("ref", ["", " x", "x\n", "x\x00", "r" * 513])
def test_bad_refs_are_refused(ref) -> None:
    with pytest.raises(ValueError, match="ref"):
        _doc(ref=ref)


def test_attachments_are_bounded_and_typed() -> None:
    assert len(_m(text="x", attachments=[_doc()] * 10).attachments) == 10
    with pytest.raises(ValueError, match="at most 10"):
        _m(text="x", attachments=[_doc()] * 11)
    with pytest.raises(ValueError, match="AttachmentRef"):
        _m(text="x", attachments=[b"bytes"])  # type: ignore[list-item]
    with pytest.raises(ValueError, match="sequence"):
        _m(text="x", attachments="invoice.pdf")  # type: ignore[arg-type]


def test_an_attachment_ref_carries_no_bytes() -> None:
    assert list(AttachmentRef.__slots__) == [
        "kind",
        "ref",
        "filename",
        "content_type",
    ]


# ── Through send() ──────────────────────────────────────────────────────────


class _Provider:
    name = "fake"

    def __init__(self, features=frozenset(), status=DELIVERY_ACCEPTED) -> None:
        self.message_features = features
        self.status = status
        self.calls: list[OutboundMessage] = []

    def send(self, message: OutboundMessage) -> ProviderResult:
        self.calls.append(message)
        return ProviderResult(status=self.status)


class _LegacyProvider:
    """No `message_features`, reads only `.body`: an existing adapter."""

    name = "legacy"

    def __init__(self) -> None:
        self.bodies: list[str | None] = []

    def send(self, message: OutboundMessage) -> ProviderResult:
        self.bodies.append(message.body)
        return ProviderResult()


@pytest.fixture(autouse=True)
def _registries():
    prev = consent._reset_registries_for_tests(marketing=("marketing",))
    yield
    consent._reset_registries_for_tests(marketing=prev[0], numeric=prev[1])


@pytest.fixture
def tenant(db) -> Tenant:
    tenant = Tenant(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    db.add(tenant)
    db.flush()
    return tenant


def _rich(**kw) -> OutboundMessage:
    return _m(
        dispatch_id=uuid4(),
        text="hi",
        html="<p>hi</p>",
        headers={"Reply-To": "a@example.com"},
        attachments=[_doc()],
        **kw,
    )


def test_every_part_reaches_the_provider(db, tenant) -> None:
    provider = _Provider({FEATURE_HTML, FEATURE_HEADERS, FEATURE_ATTACHMENTS})
    message = _rich()
    send(db, tenant.id, provider=provider, message=message)
    got = provider.calls[0]
    assert got.text == "hi" and got.html == "<p>hi</p>"
    assert dict(got.headers) == {"Reply-To": "a@example.com"}
    assert got.attachments == (_doc(),)


def test_a_legacy_provider_keeps_working_for_a_body_message(db, tenant) -> None:
    provider = _LegacyProvider()
    for message in (
        _m(dispatch_id=uuid4(), body="a"),
        _m(dispatch_id=uuid4(), text="b"),
    ):
        assert isinstance(send(db, tenant.id, provider=provider, message=message), Sent)
    assert provider.bodies == ["a", "b"]


@pytest.mark.parametrize(
    ("kwargs", "feature"),
    [
        ({"html": "<p>x</p>"}, FEATURE_HTML),
        ({"headers": {"Reply-To": "a@example.com"}}, FEATURE_HEADERS),
        ({"attachments": [_doc()]}, FEATURE_ATTACHMENTS),
    ],
)
def test_a_provider_that_did_not_declare_a_feature_is_not_handed_it(
    db, tenant, kwargs, feature
) -> None:
    provider = _LegacyProvider()
    message = _m(dispatch_id=uuid4(), text="x", **kwargs)
    with pytest.raises(delivery.DeliveryError, match=feature):
        send(db, tenant.id, provider=provider, message=message)
    assert provider.bodies == []


def test_replay_matches_for_a_v2_message_and_conflicts_on_any_part_change(
    db, tenant
) -> None:
    provider = _Provider({FEATURE_HTML, FEATURE_HEADERS, FEATURE_ATTACHMENTS})
    message = _rich()
    first = send(db, tenant.id, provider=provider, message=message)
    db.expunge_all()
    again = send(db, tenant.id, provider=provider, message=message)
    assert first.receipt.id == again.receipt.id and len(provider.calls) == 1
    for changed in (
        {"html": "<p>other</p>"},
        {"text": "other"},
        {"headers": {"Reply-To": "b@example.com"}},
        {"attachments": [_doc(ref="doc-2")]},
        {"attachments": []},
    ):
        base = {
            "dispatch_id": message.dispatch_id,
            "text": "hi",
            "html": "<p>hi</p>",
            "headers": {"Reply-To": "a@example.com"},
            "attachments": [_doc()],
        }
        with pytest.raises(ValueError, match="dispatch id"):
            send(
                db,
                tenant.id,
                provider=provider,
                message=_m(**{**base, **changed}),
            )
    assert len(provider.calls) == 1


def test_a_legacy_receipt_still_replays_for_the_same_legacy_message(db, tenant) -> None:
    """A receipt written under the pre-parts code stored the v1 digest. Write
    one by hand from the golden vector and prove replay matches it."""
    kwargs, expected = V1_VECTORS["basic"]
    message = _m(dispatch_id=uuid4(), **{**kwargs, "address": "jane@example.com"})
    expected = message.request_fingerprint
    delivery.record_receipt(
        db,
        tenant.id,
        channel="email",
        address="jane@example.com",
        provider="fake",
        status=DELIVERY_ACCEPTED,
        dispatch_id=message.dispatch_id,
        request_fingerprint=expected,
    )
    provider = _Provider()
    outcome = send(db, tenant.id, provider=provider, message=message)
    assert outcome.status == DELIVERY_ACCEPTED and provider.calls == []


def test_a_failed_v2_dispatch_can_retry(db, tenant) -> None:
    message = _rich()
    features = {FEATURE_HTML, FEATURE_HEADERS, FEATURE_ATTACHMENTS}
    send(db, tenant.id, provider=_Provider(features, DELIVERY_FAILED), message=message)
    db.expunge_all()
    ok = _Provider(features)
    outcome = send(db, tenant.id, provider=ok, message=message)
    assert outcome.status == DELIVERY_ACCEPTED and ok.calls == [message]
