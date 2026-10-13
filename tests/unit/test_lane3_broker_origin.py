"""Canonical broker origin and finite policy; synthetic hosts only, hosted CI."""

from __future__ import annotations

import hashlib
import importlib.util
import pathlib
import sys
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "lane3_broker_origin", ROOT / "scripts/lane3_broker_origin.py"
)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)

GOOD = "https://broker-1.region.example"


def test_valid_origin_is_returned_unchanged() -> None:
    assert m.canonical_origin(GOOD) == GOOD
    assert m.canonical_origin("https://a.b") == "https://a.b"
    assert m.host_of(GOOD) == "broker-1.region.example"


CASES = [
    ("https://broker.example:443", "origin.port"),
    ("https://broker.example:8443", "origin.port"),
    ("https://user@broker.example", "origin.userinfo"),
    ("https://user:pw@broker.example", "origin.userinfo"),
    ("https://broker.example/", "origin.path"),
    ("https://broker.example/x", "origin.path"),
    ("https://broker.example\\x", "origin.path"),
    ("https://broker.example?a=1", "origin.query"),
    ("https://broker.example#f", "origin.fragment"),
    ("https://broker.example.", "origin.trailing_dot"),
    ("https://broker.example ", "origin.whitespace"),
    (" https://broker.example", "origin.whitespace"),
    ("https://broker.example\n", "origin.whitespace"),
    ("https://broker.example\x00", "origin.whitespace"),
    ("https://bro ker.example", "origin.whitespace"),
    ("https://broker%2eexample", "origin.percent"),
    ("https://%62roker.example", "origin.percent"),
    ("https://192.0.2.1", "origin.ip_literal"),
    ("https://[2001:db8::1]", "origin.ip_literal"),
    ("https://2130706433.example.7", "origin.ip_literal"),
    ("https://0x7f.0.0.1", "origin.ip_literal"),
    ("https://brökér.example", "origin.non_ascii"),
    ("https://xn--bcher-kva.example", "origin.idna"),
    ("https://Broker.example", "origin.uppercase"),
    ("HTTPS://broker.example", "origin.scheme"),
    ("http://broker.example", "origin.scheme"),
    ("//broker.example", "origin.scheme"),
    ("broker.example", "origin.scheme"),
    ("https://", "origin.empty"),
    ("https://broker..example", "origin.label"),
    ("https://.broker.example", "origin.label"),
    ("https://-broker.example", "origin.label"),
    ("https://broker-.example", "origin.label"),
    ("https://bro_ker.example", "origin.label"),
    ("https://localhost", "origin.label"),
    ("https://" + "a" * 64 + ".example", "origin.length"),
    ("https://" + ".".join(["a" * 60] * 5) + ".example", "origin.length"),
    ("", "origin.whitespace"),
]


@pytest.mark.parametrize(("value", "label"), CASES)
def test_refuses_with_fixed_label_never_echoing_input(value: str, label: str) -> None:
    with pytest.raises(m.OriginRefused) as e:
        m.canonical_origin(value)
    assert e.value.label == label
    assert str(e.value) == label
    if len(value) > 3:
        assert value not in str(e.value)


@pytest.mark.parametrize("value", [None, 1, b"https://a.example", ["x"], object()])
def test_non_string_refuses(value: Any) -> None:
    with pytest.raises(m.OriginRefused) as e:
        m.canonical_origin(value)
    assert e.value.label == "origin.type"


def test_str_subclass_refuses() -> None:
    class S(str):
        pass

    with pytest.raises(m.OriginRefused):
        m.canonical_origin(S(GOOD))


def test_refused_input_is_never_normalised_into_accepted() -> None:
    for value, _ in CASES:
        try:
            result = m.canonical_origin(value)
        except m.OriginRefused:
            continue
        raise AssertionError(f"accepted a refused spelling: {result!r}")
    # Sensitivity: the lowercased / stripped forms ARE valid, so refusal above
    # comes from the predicate, not from a broken host.
    assert m.canonical_origin("https://broker.example") == "https://broker.example"


def test_max_length_boundary() -> None:
    label = "a" * 63
    host = ".".join([label, label, label, "a" * 61])
    assert len(host) == 253
    assert m.canonical_origin("https://" + host) == "https://" + host
    with pytest.raises(m.OriginRefused):
        m.canonical_origin("https://" + host + "b")


def doc(origins: list[Any]) -> dict[str, Any]:
    return {"schema": m.POLICY_SCHEMA, "origins": origins}


def test_policy_exact_membership_and_digest() -> None:
    other = "https://broker-2.region.example"
    policy = m.OriginPolicy.from_mapping(doc([other, GOOD]))
    assert policy.admits(GOOD) and policy.admits(other)
    expected = hashlib.sha256(
        "\n".join([m.POLICY_SCHEMA, *sorted([GOOD, other])]).encode() + b"\n"
    ).hexdigest()
    assert policy.digest == expected
    assert m.OriginPolicy.from_mapping(doc([GOOD, other])).digest == policy.digest
    assert m.OriginPolicy.from_mapping(doc([GOOD])).digest != policy.digest


@pytest.mark.parametrize(
    "candidate",
    [
        "https://broker-3.region.example",
        "https://sub.broker-1.region.example",
        "https://region.example",
        "https://broker-1.region.example:443",
        "https://broker-1.region.example/",
        "https://BROKER-1.region.example",
        "https://broker-1.region.example.",
        "http://broker-1.region.example",
        "broker-1.region.example",
        "",
        None,
        5,
    ],
)
def test_policy_is_not_suffix_wildcard_or_normalising(candidate: Any) -> None:
    policy = m.OriginPolicy.from_mapping(doc([GOOD]))
    assert policy.admits(candidate) is False


@pytest.mark.parametrize(
    "mapping",
    [
        {},
        {"schema": m.POLICY_SCHEMA},
        {"schema": "other", "origins": [GOOD]},
        {"schema": m.POLICY_SCHEMA, "origins": []},
        {"schema": m.POLICY_SCHEMA, "origins": GOOD},
        {"schema": m.POLICY_SCHEMA, "origins": [GOOD, GOOD]},
        {"schema": m.POLICY_SCHEMA, "origins": [GOOD, "https://*.example"]},
        {"schema": m.POLICY_SCHEMA, "origins": [GOOD, "https://Bad.example"]},
        {"schema": m.POLICY_SCHEMA, "origins": [GOOD], "extra": 1},
        {
            "schema": m.POLICY_SCHEMA,
            "origins": [f"https://h{i}.example" for i in range(65)],
        },
        "text",
        None,
    ],
)
def test_policy_refuses_malformed_documents(mapping: Any) -> None:
    with pytest.raises(m.OriginRefused):
        m.OriginPolicy.from_mapping(mapping)


def test_policy_is_immutable_and_repr_is_count_only() -> None:
    policy = m.OriginPolicy.from_mapping(doc([GOOD]))
    assert GOOD not in repr(policy)
    with pytest.raises(AttributeError):
        policy.extra = 1  # type: ignore[attr-defined]
    assert isinstance(policy.origins, frozenset)
