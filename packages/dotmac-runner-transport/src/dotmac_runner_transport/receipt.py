"""Typed completion evidence; local command success alone is insufficient."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from .canonical import canonical_bytes, typed_sha256
from .render import RunnerEgressBundleV1

__all__ = ["EvidenceStatus", "LifecycleEvidenceV1", "RunnerTransportReceiptV1"]


class EvidenceStatus(StrEnum):
    EXECUTED_PASSED = "executed_passed"
    EXECUTED_FAILED = "executed_failed"
    BLOCKED = "blocked"
    NOT_EXECUTED = "not_executed"
    HAND_MEASURED = "hand_measured"
    VACUOUS = "vacuous"


REQUIRED_ITEMS = (
    "provider_selected",
    "policy_projected",
    "configuration_applied",
    "runner_queued",
    "runner_acquired",
    "workload_executed",
    "result_uploaded",
    "provider_recorded_success",
    "direct_egress_refused",
    "allowed_transport_succeeded",
)


@dataclass(frozen=True, slots=True)
class LifecycleEvidenceV1:
    item: str
    status: EvidenceStatus
    evidence_digest: str
    mutated: bool = False

    def __post_init__(self) -> None:
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.evidence_digest):
            raise ValueError("evidence digest must be typed")


@dataclass(frozen=True, slots=True)
class RunnerTransportReceiptV1:
    schema: str
    source_revision: str
    repository: str
    runner_name: str
    required_labels: tuple[str, ...]
    specification_digest: str
    authorized_plan_digest: str
    execution_policy_digest: str
    adapter_key: str
    adapter_version: str
    adapter_declaration_digest: str
    binding_digest: str
    rendered_squid_digest: str
    rendered_nftables_digest: str
    runner_environment_digest: str
    workload_environment_digest: str | None
    items: tuple[LifecycleEvidenceV1, ...]

    def __post_init__(self) -> None:
        if self.schema != "RunnerTransportReceipt.v1":
            raise ValueError("runner transport receipt schema must be v1")
        if not re.fullmatch(r"[0-9a-f]{40}", self.source_revision):
            raise ValueError("receipt source revision must be a lowercase Git SHA")
        required_text = {
            "repository": self.repository,
            "runner name": self.runner_name,
            "adapter key": self.adapter_key,
            "adapter version": self.adapter_version,
        }
        for name, value in required_text.items():
            if not value.strip():
                raise ValueError(f"receipt {name} cannot be empty")
        if tuple(sorted(set(self.required_labels))) != self.required_labels:
            raise ValueError("receipt required labels must be unique and sorted")
        digest_fields = {
            "specification": self.specification_digest,
            "authorized plan": self.authorized_plan_digest,
            "execution policy": self.execution_policy_digest,
            "adapter declaration": self.adapter_declaration_digest,
            "binding": self.binding_digest,
            "rendered Squid": self.rendered_squid_digest,
            "rendered nftables": self.rendered_nftables_digest,
            "runner environment": self.runner_environment_digest,
        }
        if self.workload_environment_digest is not None:
            digest_fields["workload environment"] = self.workload_environment_digest
        for name, value in digest_fields.items():
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
                raise ValueError(f"receipt {name} digest must be typed")

    def assert_accepted(self, expected_bundle: RunnerEgressBundleV1) -> None:
        """Accept only evidence bound to one expected render and runner identity."""

        expected_bundle.assert_valid()
        digests = {
            self.specification_digest,
            self.authorized_plan_digest,
            self.execution_policy_digest,
        }
        if len(digests) != 1:
            raise ValueError(
                "specification, authorization and execution digests differ"
            )
        if self.execution_policy_digest != expected_bundle.policy_digest:
            raise ValueError("receipt policy differs from the expected rendered bundle")
        if self.binding_digest != expected_bundle.binding_digest:
            raise ValueError(
                "receipt binding differs from the expected rendered bundle"
            )
        if self.rendered_squid_digest != expected_bundle.squid_sha256:
            raise ValueError(
                "receipt Squid bytes differ from the expected rendered bundle"
            )
        if self.rendered_nftables_digest != expected_bundle.nftables_sha256:
            raise ValueError(
                "receipt nftables bytes differ from the expected rendered bundle"
            )
        environments = tuple(
            item
            for item in expected_bundle.runner_environments
            if item.runner_name == self.runner_name
        )
        if len(environments) != 1:
            raise ValueError(
                "receipt runner is missing or duplicated in the expected "
                "rendered bundle"
            )
        environment = environments[0]
        if self.runner_environment_digest != environment.sha256:
            raise ValueError(
                "receipt runner environment differs from the expected rendered bundle"
            )
        if self.workload_environment_digest != environment.workload_sha256:
            raise ValueError(
                "receipt workload environment differs from the expected rendered bundle"
            )
        names = [item.item for item in self.items]
        if sorted(names) != sorted(REQUIRED_ITEMS):
            raise ValueError(
                "receipt lifecycle rows are missing, duplicated or unknown"
            )
        failed = [
            item.item
            for item in self.items
            if item.status is not EvidenceStatus.EXECUTED_PASSED
        ]
        if failed:
            raise ValueError("receipt is not accepted: " + ", ".join(failed))

    @property
    def digest(self) -> str:
        return typed_sha256(canonical_bytes(self))
