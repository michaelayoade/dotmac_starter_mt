"""The tenant audit effect must remain named, supplied, bound and enforced."""

from __future__ import annotations

import pytest
from dotmac_kernel.migrations import verify
from dotmac_kernel.namespaces import KERNEL_MIGRATION_OWNER
from dotmac_kernel.prerequisites import (
    KERNEL_PREREQUISITES,
    TENANT_AUDIT_LOG_V1,
    install_prerequisite_bindings,
    installed_bindings,
)

from app.migration_bindings import ASSEMBLY_PREREQUISITE_BINDINGS


def test_tenant_audit_contract_is_named_provided_bound_and_enforced() -> None:
    name = TENANT_AUDIT_LOG_V1.name
    assert name == "tenant_audit_log.v1"
    assert name in {spec.name for spec in KERNEL_PREREQUISITES}
    assert name in KERNEL_MIGRATION_OWNER.provides
    assert verify.registered_verifiers()[name] is verify.verify_tenant_audit_log
    assert "verify_tenant_audit_log" in verify.__all__
    bindings = [b for b in ASSEMBLY_PREREQUISITE_BINDINGS if b.prerequisite == name]
    assert len(bindings) == 1
    assert bindings[0].provider_owner == "kernel"
    assert bindings[0].provider_revision == "0029_audit_history_shape"


def test_missing_verifier_refuses_and_real_dispatch_is_reachable(monkeypatch) -> None:
    """Sensitivity: removing the mapping refuses; restoring dispatch calls proof."""
    name = TENANT_AUDIT_LOG_V1.name
    previous = tuple(installed_bindings())
    install_prerequisite_bindings(ASSEMBLY_PREREQUISITE_BINDINGS)
    try:
        monkeypatch.delitem(verify._VERIFIERS, name)
        with pytest.raises(
            verify.PrerequisiteVerifierMissingError, match="no verifier"
        ):
            verify.require_prerequisites(None, (name,))
        observed = []
        sentinel = object()
        monkeypatch.setitem(verify._VERIFIERS, name, observed.append)
        verify.require_prerequisites(sentinel, (name,))
        assert observed == [sentinel]
    finally:
        install_prerequisite_bindings(previous)


def test_safe_helper_body_identity_preserves_literals_and_rejects_marker_spoofs() -> (
    None
):
    body = "\n".join(verify._TENANT_AUDIT_FUNCTION_BODY)
    assert verify._tenant_audit_function_body_matches(body)
    assert verify._tenant_audit_function_body_matches(
        "\n" + "\n".join("    " + line for line in body.splitlines()) + "\n"
    )
    for mutation in (
        body.replace("'app.current_tenant'", "'app. current_tenant'"),
        body.replace("''", "' '"),
        body + "\n-- expected markers",
        "BEGIN\nRETURN NULL;\n" + body + "\nEND;",
    ):
        assert not verify._tenant_audit_function_body_matches(mutation)
