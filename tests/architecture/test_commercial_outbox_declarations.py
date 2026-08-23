"""Commercial modules declare exactly the outbox vocabulary they emit."""

from __future__ import annotations

import ast
from collections.abc import Mapping
from pathlib import Path

from dotmac_billing.contracts import DOCUMENT_FACT_CONTRACT
from dotmac_billing.manifest import module as billing_module
from dotmac_collections.manifest import module as collections_module

REPO_ROOT = Path(__file__).resolve().parents[2]


def _emitted_event_types(
    source: str, *, constants: Mapping[str, str] | None = None
) -> frozenset[str]:
    """Extract every statically named ``_emit(..., event_type=...)`` code."""

    resolved = constants or {}
    emitted: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "_emit":
            continue
        event_type = next(
            (keyword.value for keyword in node.keywords if keyword.arg == "event_type"),
            None,
        )
        if isinstance(event_type, ast.Constant) and isinstance(event_type.value, str):
            emitted.add(event_type.value)
        elif isinstance(event_type, ast.Name) and event_type.id in resolved:
            emitted.add(resolved[event_type.id])
        else:
            raise AssertionError("every _emit call must use a statically declared type")
    return frozenset(emitted)


def _source(package: str) -> str:
    return (
        REPO_ROOT / f"packages/{package}/src/{package.replace('-', '_')}/service.py"
    ).read_text(encoding="utf-8")


def test_detector_rejects_an_undeclared_event_type() -> None:
    emitted = _emitted_event_types(
        "def probe():\n    _emit(db, event_type='commercial.undeclared.v1')\n"
    )
    assert emitted - {"commercial.declared.v1"} == {"commercial.undeclared.v1"}


def test_billing_declares_exactly_the_events_its_service_emits() -> None:
    emitted = _emitted_event_types(
        _source("dotmac-billing"),
        constants={"DOCUMENT_FACT_CONTRACT": DOCUMENT_FACT_CONTRACT},
    )
    assert (
        frozenset(billing_module.outbox_event_types)
        == emitted
        == {
            "billing.accounting.fact.v1",
            "billing.document.artifact.recorded.v1",
            "billing.document.artifact.repaired.v1",
            "billing.invoice.document.fact.v1",
            "billing.obligation.accepted.v1",
            "billing.receivable.exposure.v1",
            "billing.receivable.position.v1",
            "billing.settlement.accepted.v1",
        }
    )


def test_collections_declares_exactly_the_events_its_service_emits() -> None:
    emitted = _emitted_event_types(_source("dotmac-collections"))
    assert emitted == {
        "collections.action.requested.v1",
        "collections.notice.requested.v1",
    }
    assert frozenset(collections_module.outbox_event_types) == emitted | {
        "collections.case.step_due.v1"
    }
