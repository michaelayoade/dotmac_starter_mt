"""The disposable test Postgres must publish on loopback and nowhere else.

`docker-compose.test.yml` runs with `POSTGRES_HOST_AUTH_METHOD: trust` — no
password at all — which is fine for a throwaway database and catastrophic if the
port is reachable from a network. The two facts are only safe TOGETHER, so the
binding is the thing that has to be enforced rather than described.

It previously published `"${TEST_DB_PORT:-5433}:5432"`, which docker interprets
as every interface, while the comment beside it claimed 127.0.0.1. On a laptop
or an ephemeral runner nothing happens. Run it on a host with a routable address
— which happened — and it is an unauthenticated Postgres on the internet. A
docker publish also **bypasses ufw**, so a host firewall is not a second line of
defence here.

Scope note: this file is about the BINDING. Trust auth stays, deliberately —
the initial Alembic migration creates `app_user` with LOGIN and no password, so
TCP auth would fail without it. Changing that is a different patch with a
different blast radius.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = PROJECT_ROOT / "docker-compose.test.yml"

#: `[host:]port:container` — docker's published-port grammar.
_PUBLISH_RE = re.compile(r'^\s*-\s*"(?P<mapping>[^"]+)"\s*$', re.MULTILINE)


def _split_mapping(mapping: str) -> list[str]:
    """Split a publish mapping on its FIELD colons only.

    `${TEST_DB_PORT:-5433}` contains a colon of its own, so a naive
    `split(":")` reports four fields and mis-reads the bind address — which is
    exactly the value this file exists to check.
    """
    fields: list[str] = []
    current: list[str] = []
    depth = 0
    for char in mapping:
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        if char == ":" and depth == 0:
            fields.append("".join(current))
            current = []
            continue
        current.append(char)
    fields.append("".join(current))
    return fields


def _published() -> list[str]:
    source = COMPOSE.read_text(encoding="utf-8")
    ports_block = source.split("ports:", 1)[1].split("healthcheck:", 1)[0]
    return [match.group("mapping") for match in _PUBLISH_RE.finditer(ports_block)]


def test_the_compose_file_publishes_exactly_one_port() -> None:
    assert len(_published()) == 1, _published()


def test_the_resolved_bind_address_is_exactly_loopback() -> None:
    """Not "starts with 127", not "contains localhost" — exactly 127.0.0.1.

    `127.0.0.0/8` is all loopback to the kernel, but an exact match is what
    keeps this reviewable: anything else in that field is a decision someone
    should have to defend in a diff.
    """
    mapping = _published()[0]
    parts = _split_mapping(mapping)
    assert len(parts) == 3, (
        f"published port {mapping!r} has no explicit bind address; docker "
        "publishes on 0.0.0.0 when the host part is omitted"
    )
    assert parts[0] == "127.0.0.1", f"bind address is {parts[0]!r}, not loopback"


def test_the_port_variable_cannot_widen_the_bind_address() -> None:
    """SENSITIVITY PROOF, and the reason the variable stays numeric.

    `TEST_DB_PORT=0.0.0.0:5433` was a working way to expose the database — it
    is how the exposure was worked around before being fixed. The variable must
    appear only in the PORT field, so the worst a caller can do is move the
    port.
    """
    mapping = _published()[0]
    host, port, container = _split_mapping(mapping)

    assert (
        "$" not in host
    ), f"the bind address {host!r} is interpolated, so a caller can widen it"
    assert "$" not in container
    assert "TEST_DB_PORT" in port, "the port should stay caller-configurable"


def test_the_old_all_interfaces_mapping_is_gone() -> None:
    """The exact string that shipped the exposure, refused by name."""
    # Substring matching would be wrong here: the FIXED mapping legitimately
    # ends with the old one. The whole published value is what must differ.
    for unbound in ("${TEST_DB_PORT:-5433}:5432", "${TEST_DB_PORT}:5432"):
        assert unbound not in _published(), unbound


@pytest.mark.parametrize(
    "mapping,safe",
    [
        ("127.0.0.1:5433:5432", True),
        ("${TEST_DB_PORT:-5433}:5432", False),
        ("0.0.0.0:5433:5432", False),
        ("5433:5432", False),
        ("[::]:5433:5432", False),
    ],
)
def test_the_detector_tells_a_bound_publish_from_an_open_one(
    mapping: str, safe: bool
) -> None:
    """Specificity for the assertions above: they must accept the fixed form and
    reject every shape that reaches a network, IPv6 included."""
    parts = _split_mapping(mapping)
    detected_safe = len(parts) == 3 and parts[0] == "127.0.0.1"
    assert detected_safe is safe


def test_trust_auth_is_justified_by_the_binding_in_the_same_file() -> None:
    """The comment must describe what is ENFORCED.

    It previously asserted a 127.0.0.1 binding the file did not have — the kind
    of comment that makes a reviewer skip the line that mattered.
    """
    source = COMPOSE.read_text(encoding="utf-8")
    assert "POSTGRES_HOST_AUTH_METHOD: trust" in source
    assert "127.0.0.1" in source
    # The justification must be adjacent to the setting it justifies.
    trust_at = source.index("POSTGRES_HOST_AUTH_METHOD")
    preamble = source[max(0, trust_at - 700) : trust_at]
    assert "loopback" in preamble.lower() or "127.0.0.1" in preamble
