"""`refuse_resolved_material=False` stays a renderer-only escape.

`build_canonical_document` normally refuses a descriptor that has resolved
material in it — an address literal, a concrete endpoint — because such a
document must not be SENT to `dotmac-deployment-control`.

The compose renderer needs the same document's digest as a service's
configuration identity, and it needs it for descriptors that trip that check:
`uvicorn --host 0.0.0.0` is an in-container bind, not topology, and a container
with NO identity is strictly worse than a container whose descriptor would have
been rejected at a boundary it is not crossing.

So the flag exists. The danger is not the flag; it is the flag's SECOND caller.
The refusal is a boundary check, and the moment something that actually sends a
document to Control passes `False`, the boundary is gone and nothing about the
call site looks unusual — one keyword argument, in a file that legitimately
builds documents.

This pins the exemption to exactly one call site by count. It is deliberately
crude: a count is checkable, whereas "only the renderer should do this" is a
sentence in a docstring. If the renderer is refactored and the count moves, the
right response is to look at the new call site and update this number on
purpose — which is the whole point.
"""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE = (
    PROJECT_ROOT
    / "packages"
    / "dotmac-deployment-foundation"
    / "src"
    / "dotmac_deployment_foundation"
)

#: The renderer, and nothing else.
EXPECTED_BYPASS_SITES = 1


def _bypass_sites() -> list[str]:
    """Every call passing `refuse_resolved_material=False`, by file.

    Walks the AST rather than grepping, so a mention in a docstring or comment
    — of which this module and `document.py` have several — is not counted as a
    call. A text search here would report the explanations as violations.
    """
    found: list[str] = []
    for path in sorted(SOURCE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "refuse_resolved_material":
                    continue
                value = keyword.value
                if isinstance(value, ast.Constant) and value.value is False:
                    found.append(f"{path.relative_to(SOURCE)}:{node.lineno}")
    return found


def test_the_boundary_bypass_has_exactly_one_call_site() -> None:
    sites = _bypass_sites()
    assert len(sites) == EXPECTED_BYPASS_SITES, (
        f"expected {EXPECTED_BYPASS_SITES} caller of "
        f"`refuse_resolved_material=False`, found {sites}. Every additional "
        "one removes the boundary check for whatever it builds — and if that "
        "document is then sent to deployment control, resolved material "
        "crosses a boundary this facility exists to hold"
    )


def test_the_bypass_is_in_the_renderer() -> None:
    """Count alone would be satisfied by moving the exemption somewhere worse."""
    assert _bypass_sites() == [
        site for site in _bypass_sites() if site.startswith("render/")
    ]


def test_the_detector_sees_a_planted_second_site(tmp_path: Path) -> None:
    """Sensitivity: a guard never observed failing may not be able to fail.

    Driven over a synthetic tree, because the real one must stay at one.
    """
    planted = tmp_path / "sender.py"
    planted.write_text(
        "build_canonical_document(spec, refuse_resolved_material=False)\n",
        encoding="utf-8",
    )
    tree = ast.parse(planted.read_text(encoding="utf-8"))
    hits = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg == "refuse_resolved_material"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value is False
    ]
    assert hits, "the detector cannot see a bypass it is meant to catch"


def test_the_detector_ignores_the_default_and_the_prose() -> None:
    """The other half: `=True` and docstring mentions must not be counted.

    Without this the guard could pass by counting nothing at all, and would
    also fire on the paragraphs above explaining itself.
    """
    tree = ast.parse(
        "build_canonical_document(spec)\n"
        "build_canonical_document(spec, refuse_resolved_material=True)\n"
        '"""prose mentioning refuse_resolved_material=False"""\n'
    )
    hits = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg == "refuse_resolved_material"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value is False
    ]
    assert not hits
