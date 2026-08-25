"""The approved message-template catalogue: provider read, arity facts, and a
fail-closed cache.

Meta will only deliver a business-initiated message through a template it has
already approved, with exactly the parameters that template declares. Both of
those are provider facts the connector can hold before the wire call, so both
are checked here and returned as typed terminal outcomes rather than paid for
as a provider round trip.

## Two surfaces over one fetch

``messaging.templates.read.v1``
    Sub's production capability id, kept exactly. Served in
    :class:`dotmac_integration.spi.ConnectorMode.POLL`: the whole catalogue is
    read on the engine's schedule and emitted as typed observations, so the
    PRODUCT owns a rebuildable projection of what is approved. This is what the
    2026-08-14 inventory deferred with "the current delivery outcome cannot
    return arbitrary provider data safely" — a poll batch can, because an
    observation is the shape the engine already records.

the send pre-flight
    The same read, filtered to one template name, memoized per binding scope,
    used only to REFUSE a send. It answers one question — may this exact
    (name, language) go out with these parameters — and returns no provider
    data to anyone.

## The cache, stated exactly

Sub's `whatsapp_capability.list_approved_templates` is the qualifying source:
a process-local dict, `time.monotonic()` timestamps, a hard
``_TEMPLATE_CACHE_TTL_SECONDS = 300``, keyed by capability binding and config
revision. That policy comes across unchanged, with its key rebuilt from what a
connector actually holds and its two defects fixed:

=============== ================================================================
state           behaviour
=============== ================================================================
**fresh**       age < TTL: served from memory. No provider call.
**cold**        no entry: fetch synchronously, then answer.
**stale**       age >= TTL: treated exactly as cold. A stale entry is NEVER
                served, not even for one request while a refresh runs.
**fetch fails** the entry is EVICTED and the send is refused. Nothing stale is
                served and approval is never assumed.
=============== ================================================================

Stale-while-revalidate is deliberately absent. Meta moves a template out of
``APPROVED`` — to ``REJECTED``, ``PAUSED``, ``DISABLED`` — without warning the
sender, so a cached approval is a claim with a shelf life. Serving one past its
TTL because the refresh failed is precisely "assume it is still approved", and
the account-level consequence of sending against a revoked template is not a
failed message.

Sub's two defects that do not come across: its dict is unbounded and leaks an
entry per superseded config revision, and a failed refresh leaves the expired
tuple in place (unreachable, but resurrectable by any future change to the read
path). Here the store is bounded and least-recently-used, and a failed refresh
evicts.

The TTL is a knob, ``template_cache_ttl_seconds``, defaulting to Sub's 300.
``0`` disables reuse entirely: every send re-reads the catalogue.

## Scope

Sub keys its cache by ``{binding_id}:{config_revision_id}``. A connector holds
neither, so the key is rebuilt from the facts it does hold: the WABA, the Graph
API version, the template name, and a fingerprint of the access token. The
fingerprint is an HMAC under a per-PROCESS random key, so it identifies "the
same credential as last time" without being a value that survives the process,
correlates across processes, or means anything if it is ever seen. Rotating the
token or repointing the binding produces a new key rather than reusing another
installation's answer.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from secrets import token_bytes
from typing import Final

import httpx
from dotmac_integration.retry import Outcome, OutcomeStatus
from dotmac_integration.spi import InboundEvent

from dotmac_connector_whatsapp.wire import (
    CHANNEL,
    PROVIDER,
    DeliveryContractError,
    graph_client,
    retry_after_seconds,
)

__all__ = [
    "APPROVED_STATUS",
    "DEFAULT_TEMPLATE_CACHE_TTL_SECONDS",
    "DEFAULT_TEMPLATE_PAGE_SIZE",
    "MAX_CATALOGUE_PAGES",
    "MAX_TEMPLATE_CACHE_TTL_SECONDS",
    "TEMPLATE_EVENT_TYPE",
    "TEMPLATE_READ_CAPABILITY_ID",
    "TEMPLATE_VARIABLE_RE",
    "CatalogueReadError",
    "TemplateCatalogueCache",
    "TemplateCatalogueFailure",
    "TemplateVariant",
    "WhatsAppTemplateCatalogueHandler",
    "catalogue_events",
    "expected_parameters",
    "ordered_template_parameters",
    "parse_variants",
    "read_catalogue",
    "require_matching_arity",
    "require_sendable_template",
    "require_waba_id",
    "resolve_cache_ttl_seconds",
    "resolve_page_size",
]

#: Sub's production capability id, unchanged — the product owns the contract
#: name and the connector only claims to implement it.
TEMPLATE_READ_CAPABILITY_ID: Final = "messaging.templates.read.v1"
TEMPLATE_EVENT_TYPE: Final = "whatsapp.message_template.v1"

#: Sub's exact TTL. The knob's default, not a hidden constant.
DEFAULT_TEMPLATE_CACHE_TTL_SECONDS: Final = 300
MAX_TEMPLATE_CACHE_TTL_SECONDS: Final = 3600
#: Sub's exact page size for `GET /{waba_id}/message_templates`.
DEFAULT_TEMPLATE_PAGE_SIZE: Final = 200
#: Sub read ONE page and silently dropped the rest. This connector follows the
#: cursor, and refuses rather than truncating when a catalogue outruns the
#: bound — a silently short catalogue reads exactly like an unapproved template.
MAX_CATALOGUE_PAGES: Final = 20
#: The only status that may be sent against. Compared case-insensitively, as
#: Sub does; every other provider status is simply not this one.
APPROVED_STATUS: Final = "approved"

#: Sub's exact placeholder pattern.
TEMPLATE_VARIABLE_RE: Final[re.Pattern[str]] = re.compile(r"\{\{\s*(\d+)\s*\}\}")
#: A provider status is echoed into `error_detail` only when it looks like the
#: provider's own bounded vocabulary. Anything else is dropped rather than
#: relayed: this string is stored by the engine and read by an operator.
_STATUS_RE: Final[re.Pattern[str]] = re.compile(r"[A-Z][A-Z_]{0,39}")
#: A header format that carries exactly one non-text parameter.
_MEDIA_HEADER_FORMATS: Final[frozenset[str]] = frozenset(
    {"IMAGE", "VIDEO", "DOCUMENT", "LOCATION"}
)
#: Per-process, random, never persisted or logged. See the module docstring.
_FINGERPRINT_KEY: Final[bytes] = token_bytes(32)
_MAX_CACHE_ENTRIES: Final = 512


class CatalogueReadError(RuntimeError):
    """A catalogue read could not produce a trustworthy complete answer.

    Raised on the POLL path, where the engine owns the failure. The delivery
    path never sees this: it is given a typed :class:`Outcome` instead, because
    a send has a delivery ledger row waiting for one.
    """


class TemplateCatalogueFailure(RuntimeError):
    """A typed attempt outcome from the send path's catalogue read.

    Bypasses contract-refusal handling: the connector reached the provider, so
    the result is an attempt outcome and not a translation error.
    """

    def __init__(self, outcome: Outcome) -> None:
        self.outcome = outcome
        super().__init__(outcome.error_code or "template_catalogue_failed")


@dataclass(frozen=True, slots=True)
class TemplateVariant:
    """One (name, language) row of the provider's catalogue.

    `status` is the provider's, upper-cased. `approved` is the only question the
    send path asks, kept as its own field so no caller has to re-derive the
    comparison Sub does in one place and nobody else repeats.
    """

    name: str
    language: str
    status: str
    category: str | None
    body_parameter_indexes: tuple[int, ...]
    header_format: str | None
    header_parameter_count: int
    button_parameter_counts: tuple[int, ...]

    @property
    def approved(self) -> bool:
        return self.status.casefold() == APPROVED_STATUS

    @property
    def body_parameter_count(self) -> int:
        return len(self.body_parameter_indexes)

    def as_observation(self) -> dict[str, object]:
        return {
            "name": self.name,
            "language": self.language,
            "status": self.status,
            "category": self.category,
            "approved": self.approved,
            "body_parameter_indexes": list(self.body_parameter_indexes),
            "body_parameter_count": self.body_parameter_count,
            "header_format": self.header_format,
            "header_parameter_count": self.header_parameter_count,
            "button_parameter_counts": list(self.button_parameter_counts),
        }


def _placeholder_indexes(text: object) -> tuple[int, ...]:
    """Sub's derivation, unchanged: the sorted DISTINCT `{{n}}` index set."""
    if not isinstance(text, str):
        return ()
    return tuple(
        sorted({int(match.group(1)) for match in TEMPLATE_VARIABLE_RE.finditer(text)})
    )


def _header_facts(component: Mapping[str, object]) -> tuple[str | None, int]:
    raw_format = component.get("format")
    header_format = (
        raw_format.strip().upper()
        if isinstance(raw_format, str) and raw_format
        else None
    )
    if header_format in _MEDIA_HEADER_FORMATS:
        return header_format, 1
    return header_format, len(_placeholder_indexes(component.get("text")))


def _button_counts(component: Mapping[str, object]) -> tuple[int, ...]:
    buttons = component.get("buttons")
    if not isinstance(buttons, Sequence) or isinstance(buttons, str | bytes):
        return ()
    counts: list[int] = []
    for button in buttons:
        if not isinstance(button, Mapping):
            counts.append(0)
            continue
        # A URL button is the only kind that carries a send-time parameter, and
        # only when its url embeds one. Sub derives this client-side only.
        counts.append(len(_placeholder_indexes(button.get("url"))))
    return tuple(counts)


def _parse_variant(row: Mapping[str, object]) -> TemplateVariant | None:
    name = row.get("name")
    language = row.get("language")
    status = row.get("status")
    if not isinstance(name, str) or not name.strip():
        return None
    if not isinstance(language, str) or not language.strip():
        return None
    if not isinstance(status, str) or not status.strip():
        return None
    category = row.get("category")
    components = row.get("components")
    body_indexes: tuple[int, ...] = ()
    header_format: str | None = None
    header_count = 0
    button_counts: tuple[int, ...] = ()
    if isinstance(components, Sequence) and not isinstance(components, str | bytes):
        for component in components:
            if not isinstance(component, Mapping):
                continue
            kind = str(component.get("type") or "").strip().upper()
            if kind == "BODY":
                body_indexes = _placeholder_indexes(component.get("text"))
            elif kind == "HEADER":
                header_format, header_count = _header_facts(component)
            elif kind == "BUTTONS":
                button_counts = _button_counts(component)
    return TemplateVariant(
        name=name.strip(),
        language=language.strip(),
        status=status.strip().upper(),
        category=category.strip() if isinstance(category, str) and category else None,
        body_parameter_indexes=body_indexes,
        header_format=header_format,
        header_parameter_count=header_count,
        button_parameter_counts=button_counts,
    )


def parse_variants(document: object) -> tuple[TemplateVariant, ...]:
    """Read the `data` array of a catalogue response.

    A row the connector cannot identify — no name, language or status — is
    dropped rather than guessed at. Dropping is safe HERE and only here: an
    unreadable row can never make something approved, so the worst it can do is
    make a send fail closed.
    """
    if not isinstance(document, Mapping):
        raise CatalogueReadError("catalogue response is not an object")
    rows = document.get("data")
    if rows is None:
        rows = []
    if not isinstance(rows, Sequence) or isinstance(rows, str | bytes):
        raise CatalogueReadError("catalogue data is not a list")
    variants: list[TemplateVariant] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        variant = _parse_variant(row)
        if variant is not None:
            variants.append(variant)
    return tuple(variants)


def _next_cursor(document: Mapping[str, object]) -> str | None:
    paging = document.get("paging")
    if not isinstance(paging, Mapping):
        return None
    # A `next` URL is present on the last page too on some Graph versions; the
    # cursor is the authority, so it is the only thing followed.
    if "next" not in paging:
        return None
    cursors = paging.get("cursors")
    if not isinstance(cursors, Mapping):
        return None
    after = cursors.get("after")
    return after if isinstance(after, str) and after else None


def ordered_template_parameters(variables: Mapping[str, object]) -> list[str]:
    """Sub's ordering contract, unchanged.

    Numeric keys sort numerically; every other key trails in insertion order.
    """
    numbered: list[tuple[int, str]] = []
    trailing: list[str] = []
    for key, value in variables.items():
        rendered = "" if value is None else str(value)
        normalized = str(key).strip()
        if normalized.isdigit():
            numbered.append((int(normalized), rendered))
        else:
            trailing.append(rendered)
    return [value for _index, value in sorted(numbered)] + trailing


def expected_parameters(variant: TemplateVariant) -> dict[str, int]:
    """What the catalogue says this template needs, per send-time component.

    Keys are the send-side component identities — ``body``, ``header`` and
    ``button:<index>`` — because that is what a product command supplies, and a
    comparison across two different vocabularies is a comparison that will one
    day agree for the wrong reason.
    """
    expected: dict[str, int] = {"body": variant.body_parameter_count}
    if variant.header_parameter_count:
        expected["header"] = variant.header_parameter_count
    for index, count in enumerate(variant.button_parameter_counts):
        if count:
            expected[f"button:{index}"] = count
    return {key: value for key, value in expected.items() if value}


def _supplied_from_components(components: Sequence[object]) -> dict[str, int]:
    supplied: dict[str, int] = {}
    for component in components:
        if not isinstance(component, Mapping):
            raise DeliveryContractError("template_components_invalid")
        kind = str(component.get("type") or "").strip().casefold()
        if kind == "button":
            raw_index = component.get("index")
            try:
                index = int(str(raw_index).strip())
            except (TypeError, ValueError):
                raise DeliveryContractError("template_components_invalid") from None
            if index < 0:
                raise DeliveryContractError("template_components_invalid")
            key = f"button:{index}"
        elif kind in {"body", "header"}:
            key = kind
        else:
            raise DeliveryContractError("template_components_invalid")
        parameters = component.get("parameters")
        if parameters is None:
            count = 0
        elif isinstance(parameters, Sequence) and not isinstance(
            parameters, str | bytes
        ):
            count = len(parameters)
        else:
            raise DeliveryContractError("template_components_invalid")
        supplied[key] = supplied.get(key, 0) + count
    return {key: value for key, value in supplied.items() if value}


def require_matching_arity(
    variant: TemplateVariant,
    *,
    variables: Mapping[str, object],
    components: Sequence[object],
) -> None:
    """Refuse a parameter set the catalogue does not describe.

    Sub has no equivalent: nothing there ever compares a submission against the
    catalogue entry, so a body needing three values is sent with one and comes
    back as the generic `template_unavailable` classification with the count
    nowhere in the evidence.

    A flat `variables` map can only fill the BODY. A template that also needs a
    header or a button parameter is therefore a mismatch when addressed that
    way — fail closed, rather than send a template with a hole in it.
    """
    expected = expected_parameters(variant)
    if components:
        supplied = _supplied_from_components(components)
    else:
        count = len(ordered_template_parameters(variables))
        supplied = {"body": count} if count else {}
    if supplied != expected:
        raise DeliveryContractError("template_variable_arity_mismatch")


@dataclass(frozen=True, slots=True)
class _CacheKey:
    waba_id: str
    graph_api_version: str
    credential_fingerprint: str
    template_name: str


def _credential_fingerprint(token: str) -> str:
    return hmac.new(_FINGERPRINT_KEY, token.encode(), hashlib.sha256).hexdigest()


class TemplateCatalogueCache:
    """A bounded, least-recently-used memo of catalogue reads.

    Not a ledger and not a projection: it holds provider facts, it is
    rebuildable from the provider at any moment, it never outlives the process,
    and nothing reads it but the send pre-flight. A connector that persisted
    this would have made itself a second authority on what is approved.
    """

    __slots__ = ("_entries", "_lock", "_max_entries")

    def __init__(self, *, max_entries: int = _MAX_CACHE_ENTRIES) -> None:
        self._lock = threading.Lock()
        self._max_entries = max_entries
        self._entries: OrderedDict[
            _CacheKey, tuple[float, tuple[TemplateVariant, ...]]
        ] = OrderedDict()

    def fresh(
        self, key: _CacheKey, *, ttl_seconds: int, now: float
    ) -> tuple[TemplateVariant, ...] | None:
        """The entry if it is still inside the TTL, otherwise ``None``.

        A stale entry is dropped here rather than left to be found by a later
        reader: an expired approval that is still in the map is one refactor
        away from being served.
        """
        if ttl_seconds <= 0:
            return None
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            stored_at, variants = entry
            if now - stored_at >= ttl_seconds:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return variants

    def store(
        self, key: _CacheKey, variants: tuple[TemplateVariant, ...], *, now: float
    ) -> None:
        with self._lock:
            self._entries[key] = (now, variants)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def evict(self, key: _CacheKey) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


def resolve_cache_ttl_seconds(config: Mapping[str, object]) -> int:
    value = config.get("template_cache_ttl_seconds")
    if value is None:
        return DEFAULT_TEMPLATE_CACHE_TTL_SECONDS
    if isinstance(value, bool) or not isinstance(value, int):
        raise DeliveryContractError("template_cache_ttl_seconds_invalid")
    if not 0 <= value <= MAX_TEMPLATE_CACHE_TTL_SECONDS:
        raise DeliveryContractError("template_cache_ttl_seconds_invalid")
    return value


def resolve_page_size(config: Mapping[str, object]) -> int:
    value = config.get("template_page_size")
    if value is None:
        return DEFAULT_TEMPLATE_PAGE_SIZE
    if isinstance(value, bool) or not isinstance(value, int):
        raise DeliveryContractError("template_page_size_invalid")
    if not 1 <= value <= DEFAULT_TEMPLATE_PAGE_SIZE:
        raise DeliveryContractError("template_page_size_invalid")
    return value


def require_waba_id(config: Mapping[str, object]) -> str:
    value = config.get("waba_id")
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{1,40}", value) is None:
        raise DeliveryContractError("waba_id_required")
    return value


def _read_page(
    *,
    client: httpx.Client,
    graph_api_version: str,
    waba_id: str,
    token: str,
    page_size: int,
    template_name: str | None,
    after: str | None,
) -> httpx.Response:
    params: dict[str, str | int] = {
        # Sub's exact field list.
        "fields": "name,status,language,category,components",
        "limit": page_size,
    }
    if template_name is not None:
        params["name"] = template_name
    if after is not None:
        params["after"] = after
    return client.get(
        f"/{graph_api_version}/{waba_id}/message_templates",
        params=params,
        headers={"authorization": f"Bearer {token}"},
    )


def _response_document(response: httpx.Response) -> Mapping[str, object]:
    try:
        document = response.json()
    except (ValueError, json.JSONDecodeError):
        raise CatalogueReadError("catalogue response is not JSON") from None
    if not isinstance(document, Mapping):
        raise CatalogueReadError("catalogue response is not an object")
    return document


def read_catalogue(
    *,
    graph_api_version: str,
    waba_id: str,
    token: str,
    timeout_seconds: float,
    transport: httpx.BaseTransport | None,
    page_size: int = DEFAULT_TEMPLATE_PAGE_SIZE,
    template_name: str | None = None,
) -> tuple[TemplateVariant, ...]:
    """Every variant the provider reports, following the paging cursor.

    :raises CatalogueReadError: the answer cannot be trusted to be complete.
        A short catalogue is indistinguishable from a revoked template, so an
        unreadable page, a refused page or a catalogue longer than
        :data:`MAX_CATALOGUE_PAGES` is a failure, never a partial answer.
    """
    variants: list[TemplateVariant] = []
    after: str | None = None
    with graph_client(timeout_seconds=timeout_seconds, transport=transport) as client:
        for _page in range(MAX_CATALOGUE_PAGES):
            try:
                response = _read_page(
                    client=client,
                    graph_api_version=graph_api_version,
                    waba_id=waba_id,
                    token=token,
                    page_size=page_size,
                    template_name=template_name,
                    after=after,
                )
            except httpx.RequestError:
                raise CatalogueReadError("catalogue request failed") from None
            if response.status_code < 200 or response.status_code >= 300:
                raise CatalogueReadError("catalogue request was refused")
            document = _response_document(response)
            variants.extend(parse_variants(document))
            after = _next_cursor(document)
            if after is None:
                return tuple(variants)
    raise CatalogueReadError("catalogue is longer than the connector will follow")


def _transport_failure_outcome() -> Outcome:
    """Sub's `template_provider_unavailable`, and why it is not ambiguous.

    A send's read timeout is RECONCILIATION_REQUIRED because the message may
    have landed. A catalogue read is a GET with no effect to duplicate, so the
    same timeout is simply retryable — and it must be, because failing it any
    other way would dead-letter a message the provider never saw.
    """
    return Outcome(
        status=OutcomeStatus.RETRYABLE,
        error_code="template_provider_unavailable",
    )


def _read_for_send(
    *,
    graph_api_version: str,
    waba_id: str,
    token: str,
    timeout_seconds: float,
    transport: httpx.BaseTransport | None,
    page_size: int,
    template_name: str,
) -> tuple[TemplateVariant, ...]:
    """One name-filtered page, classified as a delivery attempt outcome.

    The name filter is why the send path needs no pagination bound: it asks for
    one template's variants, not the account's whole catalogue.
    """
    with graph_client(timeout_seconds=timeout_seconds, transport=transport) as client:
        try:
            response = _read_page(
                client=client,
                graph_api_version=graph_api_version,
                waba_id=waba_id,
                token=token,
                page_size=page_size,
                template_name=template_name,
                after=None,
            )
        except httpx.RequestError:
            raise TemplateCatalogueFailure(_transport_failure_outcome()) from None
        if response.status_code == 429 or response.status_code >= 500:
            raise TemplateCatalogueFailure(
                Outcome(
                    status=OutcomeStatus.RETRYABLE,
                    error_code="template_provider_retryable",
                    retry_after_seconds=retry_after_seconds(response),
                    provider_status_code=response.status_code,
                )
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise TemplateCatalogueFailure(
                Outcome(
                    status=OutcomeStatus.TERMINAL,
                    error_code="template_provider_rejected",
                    provider_status_code=response.status_code,
                )
            )
        try:
            return parse_variants(_response_document(response))
        except CatalogueReadError:
            raise TemplateCatalogueFailure(
                Outcome(
                    status=OutcomeStatus.TERMINAL,
                    error_code="template_response_invalid",
                    provider_status_code=response.status_code,
                )
            ) from None


def require_sendable_template(
    params: Mapping[str, object],
    *,
    config: Mapping[str, object],
    token: str,
    transport: httpx.BaseTransport | None,
    cache: TemplateCatalogueCache,
    timeout_seconds: float,
    graph_api_version: str,
    clock: Callable[[], float] = time.monotonic,
) -> TemplateVariant:
    """The whole pre-flight gate, in the order the refusals matter.

    Returns the catalogue row that authorizes this send. Every failure path
    raises before any message is put on the wire.
    """
    raw_name = params.get("template_name")
    if not isinstance(raw_name, str) or not raw_name.strip():
        raise DeliveryContractError("template_name_required")
    name = raw_name.strip()
    raw_language = params.get("language", "en")
    language_value = raw_language if raw_language else "en"
    if not isinstance(language_value, str) or not language_value.strip():
        raise DeliveryContractError("template_language_required")
    language = language_value.strip()

    waba_id = require_waba_id(config)
    ttl_seconds = resolve_cache_ttl_seconds(config)
    page_size = resolve_page_size(config)
    key = _CacheKey(
        waba_id=waba_id,
        graph_api_version=graph_api_version,
        credential_fingerprint=_credential_fingerprint(token),
        template_name=name,
    )
    now = clock()
    variants = cache.fresh(key, ttl_seconds=ttl_seconds, now=now)
    if variants is None:
        try:
            variants = _read_for_send(
                graph_api_version=graph_api_version,
                waba_id=waba_id,
                token=token,
                timeout_seconds=timeout_seconds,
                transport=transport,
                page_size=page_size,
                template_name=name,
            )
        except TemplateCatalogueFailure:
            # Fail CLOSED: whatever was held is now unverifiable, so it is
            # dropped rather than left where a later reader could serve it.
            cache.evict(key)
            raise
        if ttl_seconds > 0:
            cache.store(key, variants, now=now)

    named = [variant for variant in variants if variant.name == name]
    if not named:
        raise DeliveryContractError("template_not_found")
    matched = [variant for variant in named if variant.language == language]
    if not matched:
        raise DeliveryContractError("template_language_unavailable")
    variant = matched[0]
    if not variant.approved:
        detail = variant.status if _STATUS_RE.fullmatch(variant.status) else None
        raise DeliveryContractError("template_not_approved", detail=detail)

    raw_variables = params.get("variables", {})
    raw_components = params.get("components", [])
    if not isinstance(raw_variables, Mapping):
        raise DeliveryContractError("template_variables_invalid")
    if not isinstance(raw_components, list):
        raise DeliveryContractError("template_components_invalid")
    require_matching_arity(variant, variables=raw_variables, components=raw_components)
    return variant


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def catalogue_events(
    variants: Sequence[TemplateVariant], *, waba_id: str, observed_at: str
) -> tuple[InboundEvent, ...]:
    """One observation per catalogue row.

    The identity is derived from the row's CONTENT, so an unchanged catalogue
    redelivers ids the inbox already holds and a status change — the fact that
    matters — arrives as a new observation rather than as a silent overwrite.
    """
    events: dict[str, InboundEvent] = {}
    for index, variant in enumerate(variants):
        observation = variant.as_observation()
        digest = hashlib.sha256(_canonical(observation)).hexdigest()[:32]
        identity = f"template:{waba_id}:{variant.name}:{variant.language}:{digest}"
        events[identity] = InboundEvent(
            provider_event_id=identity,
            event_type=TEMPLATE_EVENT_TYPE,
            payload={
                "provider": PROVIDER,
                "provider_account_scope": waba_id,
                "provider_event_id": identity,
                "channel": CHANNEL,
                "observed_at": observed_at,
                "message_template": observation,
                "transport_evidence": {
                    "locator": f"/data/{index}",
                    "identity_source": "derived",
                },
            },
        )
    return tuple(events.values())


@dataclass(frozen=True, slots=True)
class WhatsAppTemplateCatalogueHandler:
    """POLL: read the approved-template catalogue and hand it over as facts.

    No cursor is returned. This is a full-snapshot read with no incremental
    position to remember, and inventing one would claim a resumability the
    provider's `after` cursor does not survive a schedule for.
    """

    transport: httpx.BaseTransport | None = field(default=None, repr=False)

    def poll(
        self,
        cursor: str | None,
        *,
        config: dict[str, object],
        secrets: dict[str, str],
    ) -> tuple[tuple[InboundEvent, ...], str | None]:
        del cursor
        waba_id = require_waba_id(config)
        graph_api_version = config.get("graph_api_version")
        if not isinstance(graph_api_version, str) or (
            re.fullmatch(r"v[0-9]{1,2}\.[0-9]+", graph_api_version) is None
        ):
            raise CatalogueReadError("graph_api_version is invalid")
        timeout_seconds = config.get("timeout_seconds")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int | float)
            or not 1 <= float(timeout_seconds) <= 60
        ):
            raise CatalogueReadError("timeout_seconds is invalid")
        token = secrets.get("access_token")
        if not isinstance(token, str) or not token:
            raise CatalogueReadError("required material is unavailable")
        variants = read_catalogue(
            graph_api_version=graph_api_version,
            waba_id=waba_id,
            token=token,
            timeout_seconds=float(timeout_seconds),
            transport=self.transport,
            page_size=resolve_page_size(config),
        )
        observed_at = datetime.now(UTC).isoformat()
        return catalogue_events(
            variants, waba_id=waba_id, observed_at=observed_at
        ), None
