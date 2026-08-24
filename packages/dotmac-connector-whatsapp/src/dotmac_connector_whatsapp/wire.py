"""Provider identity, typed refusals and HTTP outcome classification shared by
the message send, the media upload and the template-catalogue read.

Three call sites now speak to `graph.facebook.com`, and all three must classify
a provider response the same way. Keeping the classification in one module is
what stops a catalogue timeout being called terminal in one place and retryable
in another; a connector that disagrees with itself about retryability produces a
delivery queue nobody can reason about.

Nothing here reads a database, holds state, or decides what happens next. It
returns :class:`dotmac_integration.retry.Outcome` values and lets the engine own
retry, dead-lettering and reconciliation.
"""

from __future__ import annotations

from typing import Final

import httpx
from dotmac_integration.retry import Outcome, OutcomeStatus

__all__ = [
    "CHANNEL",
    "GRAPH_HOST",
    "PROVIDER",
    "DeliveryContractError",
    "MediaUploadFailure",
    "graph_client",
    "request_failure",
    "retry_after_seconds",
]

GRAPH_HOST: Final = "graph.facebook.com"
PROVIDER: Final = "meta_cloud_api"
CHANNEL: Final = "whatsapp"


class DeliveryContractError(ValueError):
    """A product command cannot be translated into the provider contract.

    `code` is the CONNECTOR's vocabulary. It becomes an `Outcome` `error_code`;
    the engine stores it and never branches on it.

    `detail` is optional and deliberately narrow. It exists so a refusal that
    has a machine-readable REASON — a template's provider status, say — can
    carry it without inventing a code per value. Whatever is passed here is
    stored by the engine and read by an operator, so a caller supplying
    provider-controlled text validates its shape first.
    """

    def __init__(self, code: str, detail: str | None = None) -> None:
        self.code = code if code.isidentifier() else "delivery_contract_invalid"
        self.detail = detail
        super().__init__(self.code)


class MediaUploadFailure(RuntimeError):
    """A typed outcome that must bypass payload-validation handling.

    Raised where a helper on the payload-building path has already reached the
    provider, so its result is a real attempt outcome rather than a contract
    refusal.
    """

    def __init__(self, outcome: Outcome) -> None:
        self.outcome = outcome
        super().__init__(outcome.error_code or "media_upload_failed")


def graph_client(
    *, timeout_seconds: float, transport: httpx.BaseTransport | None
) -> httpx.Client:
    """One construction for every provider call.

    `follow_redirects=False` is deliberate: a redirect off the declared egress
    host would take the request somewhere the manifest never authorized, and the
    bearer token would travel on it.
    """
    return httpx.Client(
        base_url=f"https://{GRAPH_HOST}",
        timeout=timeout_seconds,
        transport=transport,
        follow_redirects=False,
    )


def retry_after_seconds(response: httpx.Response) -> int | None:
    value = response.headers.get("retry-after")
    if value is None or not value.isdigit():
        return None
    return int(value)


def request_failure(exc: httpx.RequestError) -> Outcome:
    """Classify a transport failure.

    A connect failure means the request never started, so a retry cannot
    duplicate an effect. Anything later — a read timeout above all — means the
    provider may have acted, and only a human or a repair command can decide.
    """
    if isinstance(exc, httpx.ConnectTimeout | httpx.ConnectError):
        return Outcome(
            status=OutcomeStatus.RETRYABLE,
            error_code="provider_connect_failed",
        )
    return Outcome(
        status=OutcomeStatus.RECONCILIATION_REQUIRED,
        error_code="provider_outcome_ambiguous",
    )
