"""Deterministic provider-free fake for hosting.account.v1."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from dotmac_hosting.contracts import (
    HOSTING_ACCOUNT_CAPABILITY,
    HOSTING_ACCOUNT_OPERATIONS,
    ChangeHostingPackageV1,
    ChangeHostingSuspensionV1,
    HostingAcknowledgementV1,
    HostingObservationV1,
    ObserveHostingAccountV1,
    ProvisionHostingAccountV1,
    ReconcileHostingAccountV1,
    SuspensionAction,
    TerminateHostingAccountV1,
)


@dataclass(slots=True)
class FakeHostingAccountCapabilityV1:
    now: datetime = field(
        default_factory=lambda: datetime(2026, 8, 19, 10, 0, tzinfo=UTC)
    )
    capability_id: str = HOSTING_ACCOUNT_CAPABILITY
    supported_operations: frozenset[str] = HOSTING_ACCOUNT_OPERATIONS
    state_by_account: dict[str, str] = field(default_factory=dict)
    package_by_account: dict[str, str] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)

    def provision(
        self, request: ProvisionHostingAccountV1
    ) -> HostingAcknowledgementV1:
        account_ref = f"fake-account:{request.operation_reference}"
        self.calls.append(("provision", account_ref))
        self.state_by_account[account_ref] = "active"
        self.package_by_account[account_ref] = request.package_ref
        return HostingAcknowledgementV1(
            operation_reference=request.operation_reference,
            provider_account_ref=account_ref,
            accepted_at=self.now,
        )

    def change_package(
        self, request: ChangeHostingPackageV1
    ) -> HostingAcknowledgementV1:
        self.calls.append(("package", request.account_ref))
        self.package_by_account[request.account_ref] = request.target_package_ref
        return HostingAcknowledgementV1(
            operation_reference=request.operation_reference,
            provider_account_ref=request.account_ref,
            accepted_at=self.now,
        )

    def change_suspension(
        self, request: ChangeHostingSuspensionV1
    ) -> HostingAcknowledgementV1:
        self.calls.append(("suspension", request.account_ref))
        self.state_by_account[request.account_ref] = (
            "suspended"
            if request.action is SuspensionAction.SUSPEND
            else "active"
        )
        return HostingAcknowledgementV1(
            operation_reference=request.operation_reference,
            provider_account_ref=request.account_ref,
            accepted_at=self.now,
        )

    def terminate(
        self, request: TerminateHostingAccountV1
    ) -> HostingAcknowledgementV1:
        self.calls.append(("termination", request.account_ref))
        self.state_by_account[request.account_ref] = "terminated"
        return HostingAcknowledgementV1(
            operation_reference=request.operation_reference,
            provider_account_ref=request.account_ref,
            accepted_at=self.now,
        )

    def observe(self, request: ObserveHostingAccountV1) -> HostingObservationV1:
        self.calls.append(("observation", request.account_ref))
        return self._observation(
            request.account_ref,
            event_prefix=f"fake-observe:{request.operation_reference}",
            operation_reference=request.operation_reference,
        )

    def reconcile(self, request: ReconcileHostingAccountV1) -> HostingObservationV1:
        self.calls.append(("reconcile", request.account_ref))
        return self._observation(
            request.account_ref,
            event_prefix="fake-reconcile",
            operation_reference=request.operation_reference,
        )

    def _observation(
        self,
        account_ref: str,
        *,
        event_prefix: str,
        operation_reference: str,
    ) -> HostingObservationV1:
        state = self.state_by_account.get(account_ref, "active")
        return HostingObservationV1(
            provider_account_ref=account_ref,
            provider_event_id=f"{event_prefix}:{account_ref}:{self.now.isoformat()}",
            capability_binding_ref="fake-hosting-binding",
            observation_kind=state,
            provider_statuses=(state,),
            observed_at=self.now,
            operation_reference=operation_reference,
            observed_package_ref=self.package_by_account.get(account_ref),
            source_mode="poll",
        )


__all__ = ["FakeHostingAccountCapabilityV1"]
