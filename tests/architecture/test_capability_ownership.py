"""The layer that HOSTS a vocabulary never enumerates its members.

ADR-0008's rule, applied to the capability-id vocabulary that
`provider-capability-sources.md` § 7.2 records as open and unregistered.
`dotmac-integration` hosts capability ids: it validates them, binds them and
refuses the three ways a declaration can be wrong. It must not contain one.

That is not a stylistic preference. A capability id names a BUSINESS contract —
what an inbound message means, what a registration implies — and the moment the
transport layer writes one down it has taken a position on a meaning it does not
own. ADR-0030 § 8.2 splits it explicitly: the id and its semantics belong to the
business domain owner; only the registry mechanics belong here.

Three guards, each shown to fail on what it forbids. A structural check with
nothing to find passes for the wrong reason.
"""

from __future__ import annotations

import ast
import re
import textwrap
from pathlib import Path
from typing import Final

import pytest

PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
PACKAGE_ROOT: Final[Path] = (
    PROJECT_ROOT / "packages" / "dotmac-integration" / "src" / "dotmac_integration"
)

#: The same shape `spi._CAPABILITY_RE` enforces. Restated here rather than
#: imported because this guard must keep working if that private name moves —
#: and because a guard that imports the thing it polices can be disarmed by
#: editing the thing it polices.
CAPABILITY_ID_RE: Final[re.Pattern[str]] = re.compile(
    r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+\.v[1-9][0-9]*$"
)

#: The ONE exemption, and it states an enforceable premise (ADR-0018): the
#: conformance kit's fake capability is a synthetic id under the reserved
#: `conformance.` domain, which names no business contract and no owner. The
#: premise is checked below — a `conformance.py` literal OUTSIDE that domain is
#: still a failure, so this is an exemption rather than an unmonitored region.
CONFORMANCE_MODULE: Final[str] = "conformance.py"
RESERVED_FAKE_DOMAIN: Final[str] = "conformance."


def _package_sources() -> list[Path]:
    return sorted(PACKAGE_ROOT.rglob("*.py"))


def _non_docstring_strings(source: str) -> list[str]:
    """Every string literal that is not a docstring.

    Docstrings are stripped rather than searched, so this package may EXPLAIN
    the vocabulary it hosts — `spi.py`'s `'ticket.observation.v1'` example is
    documentation, not a declaration — without the explanation defeating the
    guard. Comments never reach the AST at all.
    """
    tree = ast.parse(textwrap.dedent(source))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def capability_literals(source: str) -> list[str]:
    """Capability ids written down as code in this package."""
    return [s for s in _non_docstring_strings(source) if CAPABILITY_ID_RE.fullmatch(s)]


# ── Guard 1: the host writes down no member of its vocabulary ──────────────


def test_the_module_contains_no_capability_id_literal() -> None:
    offenders: dict[str, list[str]] = {}
    for path in _package_sources():
        found = capability_literals(path.read_text(encoding="utf-8"))
        if path.name == CONFORMANCE_MODULE:
            # The exemption's PREMISE, enforced: synthetic ids only.
            found = [s for s in found if not s.startswith(RESERVED_FAKE_DOMAIN)]
        if found:
            offenders[path.relative_to(PACKAGE_ROOT).as_posix()] = sorted(found)
    assert offenders == {}, (
        "dotmac-integration hosts the capability vocabulary and must not "
        f"enumerate it: {offenders}. A capability id names a business contract "
        "the owning application declares — the Integrator validates and binds "
        "it, and a connector implements it. Supply the declaration through "
        "install_capability_registry(...) instead of writing the id here"
    )


def test_the_capability_literal_guard_bites() -> None:
    """Sensitivity proof — including the near-misses it must NOT flag."""
    assert capability_literals('X = "messaging.receive.v1"') == ["messaging.receive.v1"]
    assert capability_literals('def f():\n    return {"crm.ticket.v2": 1}') == [
        "crm.ticket.v2"
    ]
    # A docstring EXAMPLE is documentation, not a declaration.
    assert capability_literals('"""Like `ticket.observation.v1`."""\nX = 1') == []
    # The validating regex itself is not a capability id.
    assert capability_literals(r'RE = r"^[a-z][a-z0-9_]*(\.[a-z]+)+\.v[1-9]$"') == []
    # An audit action has the dotted shape and no version suffix.
    assert capability_literals('A = "integration.delivery.replayed"') == []


def test_the_conformance_exemption_premise_is_enforced() -> None:
    """The exemption is not a hole: only the reserved synthetic domain passes.

    A `conformance.py` that started declaring `messaging.receive.v1` would be
    the kit certifying connectors against a real business contract it does not
    own — which is precisely what the exemption must not permit.
    """
    real = capability_literals('X = "messaging.receive.v1"')
    assert [s for s in real if not s.startswith(RESERVED_FAKE_DOMAIN)] == [
        "messaging.receive.v1"
    ]
    fake = capability_literals('X = "conformance.echo.v1"')
    assert [s for s in fake if not s.startswith(RESERVED_FAKE_DOMAIN)] == []


# ── Guard 2: the destination module holds no transport ─────────────────────

#: A destination binding says WHERE something lands. The assembly supplies the
#: authenticated client that gets it there. A transport import in this module
#: would mean the Integrator held a credential and a base URL for a destination
#: application, which is the coupling the profile seam exists to remove.
FORBIDDEN_TRANSPORTS: Final[frozenset[str]] = frozenset(
    {"httpx", "requests", "aiohttp", "urllib", "urllib3", "http", "socket", "ssl"}
)


def imported_roots(source: str) -> frozenset[str]:
    roots: set[str] = set()
    for node in ast.walk(ast.parse(textwrap.dedent(source))):
        if isinstance(node, ast.Import):
            roots |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return frozenset(roots)


def test_the_destination_module_imports_no_transport() -> None:
    source = (PACKAGE_ROOT / "destination_binding.py").read_text(encoding="utf-8")
    assert imported_roots(source) & FORBIDDEN_TRANSPORTS == frozenset(), (
        "destination_binding decides WHERE an observation lands; the composing "
        "assembly supplies the authenticated client that takes it there. A "
        "transport import here means the Integrator is holding a destination "
        "application's credentials"
    )


def test_the_transport_import_guard_bites() -> None:
    """Sensitivity proof, in both import forms."""
    assert imported_roots("import httpx") & FORBIDDEN_TRANSPORTS == {"httpx"}
    assert imported_roots("from urllib import request") & FORBIDDEN_TRANSPORTS == {
        "urllib"
    }
    assert imported_roots(
        "from dataclasses import dataclass"
    ) & FORBIDDEN_TRANSPORTS == (frozenset())


# ── Guard 3: importing the package installs no vocabulary ──────────────────


def test_importing_the_package_declares_nothing() -> None:
    """Import must not be a declaration.

    A package that arrived with a populated registry would let a capability
    enter the fleet's vocabulary by being installed, with no owner having
    published anything — the exact failure the registry exists to prevent, hidden
    behind an import statement.
    """
    from dotmac_integration import EMPTY_REGISTRY
    from dotmac_integration.capability_registry import (
        _INSTALLED,
        CapabilityRegistryNotInstalled,
        capability_registry,
    )

    assert EMPTY_REGISTRY.contracts == ()
    if _INSTALLED is None:  # nothing else in this session installed one
        with pytest.raises(CapabilityRegistryNotInstalled):
            capability_registry()
