"""Sensitivity proof for every hardened OCI image rule.

The image audit is a pure function over evidence collected from Docker.  That
seam exists so the guard can prove both directions without a daemon: a complete
conforming fixture must pass, and one isolated planted defect must make each
stable rule code fail.  ``RULES`` and ``PLANTERS`` are compared directly so a
new rule cannot ship as an unexercised decoration (ADR-0018).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

import pytest
from dotmac_deployment_foundation.image.audit import (
    RULES,
    AuditReport,
    audit_image,
    rule_declares_no_ports_it_does_not_serve,
    rule_no_build_tooling,
    rule_no_migration_on_boot,
    rule_no_secret_in_history,
    rule_no_shell_form_entrypoint,
    rule_non_root,
    rule_required_labels,
)

REVISION = "a" * 40

GOOD_INSPECT: Mapping[str, Any] = {
    "Config": {
        "User": "10001:10001",
        "Entrypoint": ["uvicorn"],
        "Cmd": ["app.main:app", "--host", "127.0.0.1"],
        "Labels": {
            "org.dotmac.product.manifest.digest": "sha256:" + "d" * 64,
            "org.opencontainers.image.revision": REVISION,
            "org.opencontainers.image.source": "https://example.invalid/product",
            "org.opencontainers.image.version": "1.2.3",
        },
        "ExposedPorts": {"8000/tcp": {}},
    }
}
GOOD_HISTORY = ("COPY --from=runtime /opt/app /opt/app",)
GOOD_LAYERS = (
    "/etc/passwd",
    "/opt/app/app/main.py",
    "/usr/local/bin/python",
)


def _inspect(**changes: object) -> Mapping[str, Any]:
    image = deepcopy(GOOD_INSPECT)
    image["Config"].update(changes)
    return image


@dataclass(frozen=True, slots=True)
class Evidence:
    inspect: Mapping[str, Any] = field(default_factory=lambda: GOOD_INSPECT)
    history: Sequence[str] = GOOD_HISTORY
    layers: Sequence[str] = GOOD_LAYERS


def _secret_history() -> Evidence:
    # Build the assignment in two pieces so a synthetic planted value cannot
    # be mistaken for a usable credential by repository push protection.
    planted = "TOKEN" + "=not-a-real-value"
    return Evidence(history=(f"RUN export {planted}",))


PLANTERS: Mapping[str, Callable[[], Evidence]] = {
    "non-root": lambda: Evidence(inspect=_inspect(User="0:0")),
    "no-migration-on-boot": lambda: Evidence(
        inspect=_inspect(Cmd=["alembic", "upgrade", "heads"])
    ),
    "no-build-tooling": lambda: Evidence(
        layers=(*GOOD_LAYERS, "/usr/local/bin/pytest")
    ),
    "no-build-context": lambda: Evidence(
        layers=(*GOOD_LAYERS, "/app/tests/test_runtime.py")
    ),
    "no-secret-in-history": _secret_history,
    "required-labels": lambda: Evidence(
        inspect=_inspect(
            Labels={
                "org.opencontainers.image.revision": REVISION,
                "org.opencontainers.image.version": "1.2.3",
            }
        )
    ),
    "exec-form-entrypoint": lambda: Evidence(
        inspect=_inspect(Entrypoint=["/bin/sh", "-c", "uvicorn app.main:app"])
    ),
    "expose-hygiene": lambda: Evidence(
        inspect=_inspect(ExposedPorts={f"{port}/tcp": {} for port in range(8000, 8005)})
    ),
}


def _report(evidence: Evidence = Evidence()) -> AuditReport:
    return audit_image(
        "registry.example.invalid/product@sha256:" + "b" * 64,
        evidence.inspect,
        history=evidence.history,
        layers=evidence.layers,
    )


def test_a_complete_conforming_image_passes_every_rule() -> None:
    report = _report()

    assert report.passed
    assert report.findings == ()
    assert report.render().endswith(f"image contract satisfied ({len(RULES)} rules)\n")


def test_each_individual_predicate_accepts_the_conforming_evidence() -> None:
    """Discriminating negative control for the planted cases below."""
    assert rule_non_root(GOOD_INSPECT) == []
    assert rule_no_migration_on_boot(GOOD_INSPECT) == []
    assert rule_no_build_tooling(GOOD_INSPECT, layers=GOOD_LAYERS) == []
    assert rule_no_secret_in_history(GOOD_INSPECT, history=GOOD_HISTORY) == []
    assert rule_required_labels(GOOD_INSPECT) == []
    assert rule_no_shell_form_entrypoint(GOOD_INSPECT) == []
    assert rule_declares_no_ports_it_does_not_serve(GOOD_INSPECT) == []


def test_every_declared_rule_has_exactly_one_planted_case() -> None:
    assert tuple(PLANTERS) == RULES


@pytest.mark.parametrize("rule", RULES)
def test_audit_image_catches_each_rule_in_isolation(rule: str) -> None:
    report = _report(PLANTERS[rule]())

    assert not report.passed
    assert [finding.rule for finding in report.findings] == [rule]


@pytest.mark.parametrize(
    ("user", "detail"),
    [
        ("", "declares no USER"),
        ("app", "a NAME"),
        ("0:0", "which is root"),
        ("999:999", "system range"),
    ],
)
def test_non_root_refuses_every_unsafe_user_shape(user: str, detail: str) -> None:
    findings = rule_non_root(_inspect(User=user))

    assert len(findings) == 1
    assert findings[0].rule == "non-root"
    assert detail in findings[0].detail


def test_filesystem_and_history_evidence_fail_closed_when_missing() -> None:
    report = _report(Evidence(history=(), layers=()))

    assert [finding.rule for finding in report.findings] == [
        "no-build-tooling",
        "no-secret-in-history",
    ]
    assert all(
        "could not be evaluated" in finding.detail for finding in report.findings
    )


def test_build_context_detection_matches_files_below_a_forbidden_directory() -> None:
    findings = rule_no_build_tooling(
        GOOD_INSPECT,
        layers=(*GOOD_LAYERS, "/app/.git/objects/ab/cdef"),
    )

    assert [finding.rule for finding in findings] == ["no-build-context"]
    assert "/app/.git" in findings[0].detail


def test_a_detected_history_assignment_never_echoes_its_value() -> None:
    value = "not-a-real-value"
    findings = rule_no_secret_in_history(
        GOOD_INSPECT,
        history=("RUN export TOKEN" + f"={value}",),
    )

    assert [finding.rule for finding in findings] == ["no-secret-in-history"]
    assert "TOKEN=…" in findings[0].detail
    assert value not in findings[0].detail


@pytest.mark.parametrize(
    "labels",
    [
        [],
        {
            "org.opencontainers.image.revision": "short-sha",
            "org.opencontainers.image.source": "https://example.invalid/product",
            "org.opencontainers.image.version": "1.2.3",
        },
    ],
)
def test_required_labels_refuses_invalid_mapping_and_revision_shapes(
    labels: object,
) -> None:
    findings = rule_required_labels(_inspect(Labels=labels))

    assert findings
    assert {finding.rule for finding in findings} == {"required-labels"}


def test_docker_config_block_and_direct_config_shapes_are_equivalent() -> None:
    block = GOOD_INSPECT["Config"]

    assert _report(Evidence(inspect=block)).passed


def test_report_renders_every_finding_with_its_stable_rule_code() -> None:
    evidence = Evidence(
        inspect=_inspect(User="0:0"),
        history=(),
        layers=(),
    )
    rendered = _report(evidence).render()

    assert "3 image-contract violation(s)" in rendered
    for rule in ("non-root", "no-build-tooling", "no-secret-in-history"):
        assert f"  - {rule}:" in rendered
