"""Provider-neutral AI commands, execution observations and intents."""
from __future__ import annotations
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class AIOperationIntent:
    intent_key: str
    operation_id: UUID
    capability: str
    input_ref: str
    input_digest: str
    policy_digest: str


@dataclass(frozen=True, slots=True)
class AttemptInput:
    attempt_key: str
    outcome: str
    output_ref: str | None
    output_digest: str | None
    provider_observation: str | None
    model_observation: str | None
    request_observation: str | None
    error_code: str | None


@dataclass(frozen=True, slots=True)
class InsightInput:
    insight_key: str
    insight_type: str
    advisory_value: str
    confidence: float | None
    source_output_digest: str

