"""Typed completion evidence; local command success alone is insufficient."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Self

from .canonical import canonical_bytes, typed_sha256
from .contracts import AdapterIdentityV1
from .render import RunnerEgressBundleV1

__all__ = [
    "EvidenceFieldV1",
    "EvidenceStatus",
    "LifecycleEvidenceDocumentV1",
    "LifecycleEvidenceV1",
    "ProviderRunnerIdentityV1",
    "RunnerTransportReceiptV1",
]


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
class ProviderRunnerIdentityV1:
    """One logical host runner bound to its provider-side acquisition identity."""

    logical_runner_name: str
    provider_runner_name: str
    repository: str
    required_labels: tuple[str, ...]

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9-]{1,63}", self.logical_runner_name):
            raise ValueError("logical runner name must be a lowercase host identifier")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", self.provider_runner_name):
            raise ValueError("provider runner name is invalid")
        if not re.fullmatch(
            r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}", self.repository
        ):
            raise ValueError("provider repository must be an owner/name coordinate")
        if not self.required_labels:
            raise ValueError("provider runner identity requires at least one label")
        if tuple(sorted(set(self.required_labels))) != self.required_labels:
            raise ValueError("provider runner labels must be unique and sorted")
        if any(
            not label.strip() or "\n" in label or len(label) > 128
            for label in self.required_labels
        ):
            raise ValueError("provider runner labels must be bounded nonempty lines")


@dataclass(frozen=True, slots=True, order=True)
class EvidenceFieldV1:
    """One provider-neutral, non-secret field in a retained evidence document."""

    name: str
    value: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", self.name):
            raise ValueError("evidence field name is invalid")
        if not self.value or "\n" in self.value or len(self.value) > 4096:
            raise ValueError("evidence field value must be one bounded nonempty line")


@dataclass(frozen=True, slots=True)
class LifecycleEvidenceDocumentV1:
    """Canonical retained bytes supporting one lifecycle verdict.

    The core deliberately does not interpret a provider's payload. It does bind
    every document to one typed runner identity and requires a sorted set of
    bounded public fields, so the exact retained bytes can be hashed and later
    reconstructed without a provider branch in this package.
    """

    schema: str
    item: str
    status: EvidenceStatus
    mutated: bool
    source: str
    observed_at: str
    source_revision: str
    adapter: AdapterIdentityV1
    runner_identity: ProviderRunnerIdentityV1
    fields: tuple[EvidenceFieldV1, ...]

    def __post_init__(self) -> None:
        if self.schema != "RunnerTransportLifecycleEvidence.v1":
            raise ValueError("runner transport lifecycle evidence schema must be v1")
        if self.item not in REQUIRED_ITEMS:
            raise ValueError("runner transport lifecycle evidence item is unknown")
        if not isinstance(self.mutated, bool):
            raise ValueError("runner transport evidence mutated flag must be boolean")
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{1,127}", self.source):
            raise ValueError("runner transport evidence source is invalid")
        if not self.observed_at.strip() or "\n" in self.observed_at:
            raise ValueError("runner transport evidence observation time is required")
        if not re.fullmatch(r"[0-9a-f]{40}", self.source_revision):
            raise ValueError(
                "runner transport evidence source revision must be a lowercase Git SHA"
            )
        if not self.fields:
            raise ValueError("runner transport lifecycle evidence requires fields")
        if (
            len({field.name for field in self.fields}) != len(self.fields)
            or tuple(sorted(self.fields, key=lambda field: field.name)) != self.fields
        ):
            raise ValueError(
                "runner transport evidence fields must be unique and sorted"
            )

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self)

    @property
    def digest(self) -> str:
        return typed_sha256(self.canonical_bytes)

    @classmethod
    def from_canonical_bytes(cls, value: bytes) -> Self:
        """Parse retained bytes and refuse any non-canonical or widened shape."""

        try:
            raw = json.loads(value.decode("ascii"))
            identity = raw["runner_identity"]
            fields = raw["fields"]
            document = cls(
                schema=raw["schema"],
                item=raw["item"],
                status=EvidenceStatus(raw["status"]),
                mutated=raw["mutated"],
                source=raw["source"],
                observed_at=raw["observed_at"],
                source_revision=raw["source_revision"],
                adapter=AdapterIdentityV1(
                    key=raw["adapter"]["key"],
                    version=raw["adapter"]["version"],
                    declaration_digest=raw["adapter"]["declaration_digest"],
                ),
                runner_identity=ProviderRunnerIdentityV1(
                    logical_runner_name=identity["logical_runner_name"],
                    provider_runner_name=identity["provider_runner_name"],
                    repository=identity["repository"],
                    required_labels=tuple(identity["required_labels"]),
                ),
                fields=tuple(
                    EvidenceFieldV1(name=field["name"], value=field["value"])
                    for field in fields
                ),
            )
        except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("runner transport evidence bytes are malformed") from error
        if document.canonical_bytes != value:
            raise ValueError("runner transport evidence bytes are not canonical")
        return document


@dataclass(frozen=True, slots=True)
class LifecycleEvidenceV1:
    item: str
    status: EvidenceStatus
    evidence_digest: str
    mutated: bool = False

    def __post_init__(self) -> None:
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.evidence_digest):
            raise ValueError("evidence digest must be typed")
        if not isinstance(self.mutated, bool):
            raise ValueError("evidence mutated flag must be boolean")


@dataclass(frozen=True, slots=True)
class RunnerTransportReceiptV1:
    schema: str
    source_revision: str
    runner_identity: ProviderRunnerIdentityV1
    specification_digest: str
    authorized_plan_digest: str
    execution_policy_digest: str
    adapter: AdapterIdentityV1
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
        digest_fields = {
            "specification": self.specification_digest,
            "authorized plan": self.authorized_plan_digest,
            "execution policy": self.execution_policy_digest,
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

    def assert_accepted(
        self,
        expected_bundle: RunnerEgressBundleV1,
        expected_source_revision: str,
        expected_runner_identity: ProviderRunnerIdentityV1,
        retained_evidence_documents: tuple[bytes, ...],
    ) -> None:
        """Accept only evidence bound to one expected source, render and runner."""

        expected_bundle.assert_valid()
        if not re.fullmatch(r"[0-9a-f]{40}", expected_source_revision):
            raise ValueError("expected source revision must be a lowercase Git SHA")
        if self.source_revision != expected_source_revision:
            raise ValueError("receipt source revision differs from expected")
        if self.runner_identity != expected_runner_identity:
            raise ValueError("receipt provider runner identity differs from expected")
        if self.adapter != expected_bundle.adapter:
            raise ValueError("receipt adapter identity differs from expected bundle")
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
            if item.runner_name == self.runner_identity.logical_runner_name
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
        evidence_documents: list[tuple[LifecycleEvidenceDocumentV1, bytes]] = []
        for value in retained_evidence_documents:
            if not isinstance(value, bytes):
                raise ValueError("retained evidence document must be canonical bytes")
            evidence_documents.append(
                (LifecycleEvidenceDocumentV1.from_canonical_bytes(value), value)
            )
        document_names = [document.item for document, _ in evidence_documents]
        if sorted(document_names) != sorted(REQUIRED_ITEMS):
            raise ValueError(
                "retained evidence documents are missing, duplicated or unknown"
            )
        documents = {
            document.item: (document, value) for document, value in evidence_documents
        }
        for item in self.items:
            document, retained_bytes = documents[item.item]
            if document.status is not item.status:
                raise ValueError(
                    f"retained evidence for {item.item} names the wrong verdict"
                )
            if document.mutated is not item.mutated:
                raise ValueError(
                    f"retained evidence for {item.item} names the wrong mutation flag"
                )
            if document.source_revision != expected_source_revision:
                raise ValueError(
                    f"retained evidence for {item.item} names the wrong source revision"
                )
            if document.adapter != expected_bundle.adapter:
                raise ValueError(
                    f"retained evidence for {item.item} names the wrong "
                    "adapter identity"
                )
            if document.runner_identity != expected_runner_identity:
                raise ValueError(
                    f"retained evidence for {item.item} names the wrong runner identity"
                )
            if item.evidence_digest != typed_sha256(retained_bytes):
                raise ValueError(
                    f"retained evidence bytes differ for lifecycle item {item.item}"
                )

    @property
    def digest(self) -> str:
        return typed_sha256(canonical_bytes(self))
