"""Conformance checks for Integrator-backed hosting semantic adapters."""

from __future__ import annotations

from dotmac_hosting.contracts import (
    HOSTING_ACCOUNT_CAPABILITY,
    HOSTING_ACCOUNT_OPERATIONS,
    ChangeHostingPackageV1,
    ChangeHostingSuspensionV1,
    HostingAccountIdentityV1,
    HostingAccountCapabilityV1,
    ObserveHostingAccountV1,
    ProvisionHostingAccountV1,
    ReconcileHostingAccountV1,
    SuspensionAction,
    TerminateHostingAccountV1,
)


class HostingConformanceError(AssertionError):
    pass


def check_hosting_account_capability_v1(
    candidate: HostingAccountCapabilityV1,
) -> None:
    if candidate.capability_id != HOSTING_ACCOUNT_CAPABILITY:
        raise HostingConformanceError("candidate declares the wrong capability id")
    if candidate.supported_operations != HOSTING_ACCOUNT_OPERATIONS:
        raise HostingConformanceError(
            "candidate operation declaration differs from hosting account V1"
        )
    provisioned = candidate.provision(
        ProvisionHostingAccountV1(
            operation_reference="conformance-provision",
            package_ref="package:v1",
            primary_domain="conformance-customer.ng",
            account_identity=HostingAccountIdentityV1(
                account_label="Conformance Customer",
                administrative_email="admin@conformance-customer.ng",
                country_code="NG",
            ),
        )
    )
    if provisioned.operation_reference != "conformance-provision":
        raise HostingConformanceError("provision acknowledgement lost correlation")
    if provisioned.provider_account_ref is None:
        raise HostingConformanceError("provision omitted the provider account reference")
    if hasattr(provisioned, "lifecycle_state"):
        raise HostingConformanceError(
            "provider acknowledgement must not assign Dotmac lifecycle state"
        )
    account_ref = provisioned.provider_account_ref
    package = candidate.change_package(
        ChangeHostingPackageV1(
            operation_reference="conformance-package",
            account_ref=account_ref,
            target_package_ref="package:v2",
        )
    )
    suspended = candidate.change_suspension(
        ChangeHostingSuspensionV1(
            operation_reference="conformance-suspend",
            account_ref=account_ref,
            action=SuspensionAction.SUSPEND,
            reason_ref="reason:abuse",
        )
    )
    restored = candidate.change_suspension(
        ChangeHostingSuspensionV1(
            operation_reference="conformance-restore",
            account_ref=account_ref,
            action=SuspensionAction.RESTORE,
            reason_ref="reason:abuse-cleared",
        )
    )
    observation = candidate.observe(
        ObserveHostingAccountV1(
            operation_reference="conformance-observation",
            account_ref=account_ref,
        )
    )
    reconciled = candidate.reconcile(
        ReconcileHostingAccountV1(
            operation_reference="conformance-reconcile",
            account_ref=account_ref,
        )
    )
    terminated = candidate.terminate(
        TerminateHostingAccountV1(
            operation_reference="conformance-termination",
            account_ref=account_ref,
        )
    )
    expected = (
        (package.operation_reference, "conformance-package"),
        (suspended.operation_reference, "conformance-suspend"),
        (restored.operation_reference, "conformance-restore"),
        (terminated.operation_reference, "conformance-termination"),
    )
    if any(actual != wanted for actual, wanted in expected):
        raise HostingConformanceError("a write acknowledgement lost correlation")
    if observation.provider_account_ref != account_ref or not observation.provider_event_id:
        raise HostingConformanceError("observation is not independently identified")
    if observation.operation_reference != "conformance-observation":
        raise HostingConformanceError("observation lost poll correlation")
    if reconciled.provider_account_ref != account_ref or reconciled.source_mode != "poll":
        raise HostingConformanceError("reconcile did not return a poll observation")
    if reconciled.operation_reference != "conformance-reconcile":
        raise HostingConformanceError("reconcile observation lost poll correlation")


__all__ = ["HostingConformanceError", "check_hosting_account_capability_v1"]
