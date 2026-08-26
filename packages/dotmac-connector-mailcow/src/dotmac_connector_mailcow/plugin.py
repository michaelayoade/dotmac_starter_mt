"""Stateless Mailcow API translation for managed email lifecycle."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Final
from urllib.parse import quote, urlsplit

from dotmac_integration.spi import (
    ConnectorManifest,
    ConnectorMode,
    Diagnostic,
    ProvisionApplyRequest,
    ProvisionCancelRequest,
    ProvisioningHandler,
    ProvisioningResult,
    ProvisionObserveRequest,
    ProvisionPlanRequest,
    ProvisionPlanResult,
    ProvisionResultStatus,
)

from .declaration import MANIFEST
from .transport import (
    FailureKind,
    HttpxMailcowTransport,
    MailcowRequest,
    MailcowResponse,
    MailcowTransport,
    MailcowTransportError,
    normalize_admin_endpoint,
)

_MIB: Final = 1_048_576
_ACTIVE_STATES: Final = frozenset({"enabled", "present"})


class MailcowContractError(RuntimeError):
    """Provider data cannot prove the product-owned lifecycle contract."""

    def __init__(self, code: str, *, ambiguous: bool = False) -> None:
        self.code = code
        self.ambiguous = ambiguous
        super().__init__(code)


def _string(document: Mapping[str, object], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        raise MailcowContractError(f"{key}_required")
    return value


def _optional_string(document: Mapping[str, object], key: str) -> str | None:
    value = document.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise MailcowContractError(f"{key}_invalid")
    return value


def _boolean(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str) and value in {"0", "1"}:
        return value == "1"
    raise MailcowContractError("provider_boolean_invalid")


def _integer(value: object, *, code: str) -> int:
    if isinstance(value, bool):
        raise MailcowContractError(code)
    try:
        result = int(str(value))
    except (TypeError, ValueError):
        raise MailcowContractError(code) from None
    if result < 0:
        raise MailcowContractError(code)
    return result


def _target_boolean(document: Mapping[str, object], key: str) -> bool:
    value = document.get(key)
    if not isinstance(value, bool):
        raise MailcowContractError(f"{key}_invalid")
    return value


def _mebibytes(document: Mapping[str, object], key: str) -> int:
    value = _integer(document.get(key), code=f"{key}_invalid")
    if value == 0 or value % _MIB:
        raise MailcowContractError(f"{key}_must_be_positive_whole_mebibytes")
    return value // _MIB


def _canonical_digest(document: Mapping[str, object]) -> str:
    encoded = json.dumps(
        dict(document),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _json(response: MailcowResponse) -> object:
    try:
        return json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise MailcowContractError("provider_response_invalid") from None


def _error_for_status(response: MailcowResponse, *, mutating: bool) -> None:
    status = response.status_code
    if 200 <= status < 300:
        return
    if 300 <= status < 400:
        raise MailcowTransportError("provider_redirect_refused", FailureKind.TERMINAL)
    if status == 404:
        raise MailcowTransportError("provider_not_found", FailureKind.NOT_FOUND)
    if status == 429:
        raise MailcowTransportError("provider_rate_limited", FailureKind.RETRYABLE)
    if status >= 500:
        kind = FailureKind.AMBIGUOUS if mutating else FailureKind.RETRYABLE
        code = "provider_outcome_unknown" if mutating else "provider_unavailable"
        raise MailcowTransportError(code, kind)
    if status == 401:
        raise MailcowTransportError(
            "provider_authentication_failed", FailureKind.TERMINAL
        )
    if status == 403:
        raise MailcowTransportError(
            "provider_authorization_failed", FailureKind.TERMINAL
        )
    raise MailcowTransportError("provider_rejected", FailureKind.TERMINAL)


def _transport_result(error: MailcowTransportError) -> ProvisioningResult:
    status = {
        FailureKind.AMBIGUOUS: ProvisionResultStatus.AMBIGUOUS,
        FailureKind.NOT_FOUND: ProvisionResultStatus.NOT_FOUND,
        FailureKind.RETRYABLE: ProvisionResultStatus.RETRYABLE,
        FailureKind.TERMINAL: ProvisionResultStatus.TERMINAL,
    }[error.kind]
    return ProvisioningResult(status=status, error_code=error.code)


def _contract_result(error: MailcowContractError) -> ProvisioningResult:
    return ProvisioningResult(
        status=(
            ProvisionResultStatus.AMBIGUOUS
            if error.ambiguous
            else ProvisionResultStatus.TERMINAL
        ),
        error_code=error.code,
    )


def _mailbox_address(target: Mapping[str, object]) -> str:
    return f"{_string(target, 'mailbox_local_part')}@{_string(target, 'domain_name')}"


def _mail_hostname(endpoint: str) -> str:
    host = urlsplit(normalize_admin_endpoint(endpoint)).hostname
    if host is None:
        raise MailcowContractError("admin_endpoint_invalid")
    return host


def _dns_requirements(domain: str, hostname: str) -> list[dict[str, object]]:
    return [
        {
            "owner_name": domain,
            "record_type": "MX",
            "required": True,
            "requirement_kind": "mx",
            "ttl": 300,
            "values": [f"10 {hostname}"],
        },
        {
            "owner_name": domain,
            "record_type": "TXT",
            "required": True,
            "requirement_kind": "spf",
            "ttl": 300,
            "values": ["v=spf1 mx -all"],
        },
        {
            "owner_name": f"_dmarc.{domain}",
            "record_type": "TXT",
            "required": True,
            "requirement_kind": "dmarc",
            "ttl": 300,
            "values": [f"v=DMARC1; p=none; rua=mailto:postmaster@{domain}"],
        },
        {
            "owner_name": f"autoconfig.{domain}",
            "record_type": "CNAME",
            "required": True,
            "requirement_kind": "autoconfig",
            "ttl": 300,
            "values": [hostname],
        },
        {
            "owner_name": f"autodiscover.{domain}",
            "record_type": "CNAME",
            "required": True,
            "requirement_kind": "autodiscover",
            "ttl": 300,
            "values": [hostname],
        },
    ]


class MailcowProvisioningHandler:
    """One exact email lifecycle over the supported API and safe refusals."""

    def __init__(self, transport: MailcowTransport) -> None:
        self._transport = transport

    def _request(
        self,
        request: ProvisionApplyRequest | ProvisionObserveRequest,
        *,
        method: str,
        path: str,
        document: object | None = None,
        mutating: bool = False,
    ) -> object:
        endpoint = _string(request.config, "admin_endpoint")
        material = _string(request.secrets, "admin_secret_ref")
        response = self._transport.request(
            MailcowRequest(
                method=method,
                base_endpoint=endpoint,
                path=path,
                api_key=material,
                document=document,
                mutating=mutating,
            )
        )
        _error_for_status(response, mutating=mutating)
        payload = _json(response)
        if mutating:
            self._require_mutation_success(payload)
        return payload

    @staticmethod
    def _require_mutation_success(payload: object) -> None:
        rows = payload if isinstance(payload, list) else [payload]
        if not rows or not all(isinstance(row, Mapping) for row in rows):
            raise MailcowContractError("provider_outcome_unknown", ambiguous=True)
        statuses = {str(row.get("type")) for row in rows if isinstance(row, Mapping)}
        if "danger" in statuses or "error" in statuses:
            raise MailcowContractError("provider_rejected")
        if "success" not in statuses:
            raise MailcowContractError("provider_outcome_unknown", ambiguous=True)

    def _get_one(
        self,
        request: ProvisionApplyRequest | ProvisionObserveRequest,
        path: str,
        *,
        predicate: tuple[str, str] | None = None,
    ) -> Mapping[str, object] | None:
        payload = self._request(request, method="GET", path=path)
        if isinstance(payload, Mapping):
            return payload
        if not isinstance(payload, list):
            raise MailcowContractError("provider_response_invalid")
        rows = [item for item in payload if isinstance(item, Mapping)]
        if predicate is not None:
            key, expected = predicate
            rows = [row for row in rows if str(row.get(key) or "") == expected]
        if len(rows) > 1:
            raise MailcowContractError("provider_identity_ambiguous")
        return rows[0] if rows else None

    def plan(self, request: ProvisionPlanRequest) -> ProvisionPlanResult:
        if len(request.steps) != 1:
            raise MailcowContractError("plan_requires_exactly_one_step")
        step = request.steps[0]
        return ProvisionPlanResult(
            plan_hash=request.plan_hash,
            steps=request.steps,
            evidence={
                "changes": ["reconcile"],
                "resource_kind": _string(step.input, "resource_kind"),
                "resource_ref": _string(step.input, "resource_ref"),
            },
        )

    def apply(self, request: ProvisionApplyRequest) -> ProvisioningResult:
        try:
            return self._apply(request, request.step.input)
        except MailcowTransportError as exc:
            return _transport_result(exc)
        except MailcowContractError as exc:
            return _contract_result(exc)

    def _apply(
        self, request: ProvisionApplyRequest, target: Mapping[str, object]
    ) -> ProvisioningResult:
        kind = _string(target, "resource_kind")
        state = _string(target, "desired_lifecycle_state")
        if kind == "application":
            raise MailcowContractError("immutable_subject_mapping_unverified")
        if kind == "app_password" and state in _ACTIVE_STATES:
            raise MailcowContractError("secret_write_boundary_required")
        if kind in {"mailbox", "quota", "delivery"}:
            return self._apply_mailbox(request, target, kind, state)
        if kind == "domain":
            return self._apply_domain(request, target, state)
        if kind == "alias":
            return self._apply_alias(request, target, state)
        if kind == "app_password":
            return self._delete_app_password(request, target)
        if kind == "dkim":
            return self._apply_dkim(request, target, state)
        raise MailcowContractError("resource_kind_unsupported")

    def _apply_mailbox(
        self,
        request: ProvisionApplyRequest,
        target: Mapping[str, object],
        kind: str,
        state: str,
    ) -> ProvisioningResult:
        address = _mailbox_address(target)
        path = f"/api/v1/get/mailbox/{quote(address, safe='')}"
        current = self._get_one(request, path)
        if current is None:
            if state == "absent":
                return self._success(request, target, None, provider_ref=address)
            if kind != "mailbox":
                raise MailcowContractError("mailbox_precreation_required")
            quota_mib = _mebibytes(target, "quota_bytes")
            local_part = _string(target, "mailbox_local_part")
            protocol_access = [
                protocol
                for protocol, field in (
                    ("imap", "imap_access_enabled"),
                    ("pop3", "pop3_access_enabled"),
                    ("smtp", "smtp_access_enabled"),
                    ("sieve", "sieve_access_enabled"),
                    ("eas", "eas_access_enabled"),
                    ("dav", "dav_access_enabled"),
                )
                if _target_boolean(target, field)
            ]
            self._request(
                request,
                method="POST",
                path="/api/v1/add/mailbox",
                document={
                    "active": "1"
                    if state in _ACTIVE_STATES
                    and _target_boolean(target, "delivery_enabled")
                    else "0",
                    "authsource": "generic-oidc",
                    "domain": _string(target, "domain_name"),
                    "force_pw_update": "0",
                    "force_tfa": "0",
                    "local_part": local_part,
                    "name": local_part,
                    "protocol_access": protocol_access,
                    "quota": str(quota_mib),
                    "sogo_access": "1"
                    if _target_boolean(target, "webmail_access_enabled")
                    else "0",
                    "template": "",
                },
                mutating=True,
            )
            current = self._get_one(request, path)
            if current is None:
                raise MailcowContractError("provider_outcome_unknown", ambiguous=True)
            return self._success(request, target, current, provider_ref=address)
        if state == "absent":
            self._request(
                request,
                method="POST",
                path="/api/v1/delete/mailbox",
                document=[address],
                mutating=True,
            )
            return self._success(request, target, None, provider_ref=address)
        quota_bytes = (
            _integer(target.get("quota_bytes"), code="quota_bytes_invalid")
            if kind in {"mailbox", "quota"}
            else _integer(current.get("quota", 0), code="quota_invalid")
        )
        if quota_bytes % _MIB:
            raise MailcowContractError("quota_must_be_whole_mebibytes")
        active = (
            _boolean(current.get("active", False))
            if state == "present"
            else state == "enabled"
        )
        if kind == "delivery":
            active = (
                bool(target.get("delivery_enabled")) if state != "disabled" else False
            )
        document = {
            "attr": {
                "active": "1" if active else "0",
                "quota": str(quota_bytes // _MIB),
            },
            "items": [address],
        }
        self._request(
            request,
            method="POST",
            path="/api/v1/edit/mailbox",
            document=document,
            mutating=True,
        )
        observed = self._get_one(request, path)
        return self._success(request, target, observed, provider_ref=address)

    def _apply_domain(
        self,
        request: ProvisionApplyRequest,
        target: Mapping[str, object],
        state: str,
    ) -> ProvisioningResult:
        domain = _string(target, "domain_name")
        path = f"/api/v1/get/domain/{quote(domain, safe='')}"
        current = self._get_one(request, path)
        if current is None:
            if state == "absent":
                return self._success(request, target, None, provider_ref=domain)
            alias_limit = _integer(
                target.get("domain_alias_limit"), code="domain_alias_limit_invalid"
            )
            mailbox_limit = _integer(
                target.get("domain_mailbox_limit"),
                code="domain_mailbox_limit_invalid",
            )
            if mailbox_limit == 0:
                raise MailcowContractError("domain_mailbox_limit_invalid")
            default_quota_mib = _mebibytes(target, "mailbox_quota_default_bytes")
            max_quota_mib = _mebibytes(target, "mailbox_quota_max_bytes")
            domain_quota_mib = _mebibytes(target, "domain_quota_bytes")
            if default_quota_mib > max_quota_mib:
                raise MailcowContractError("mailbox_default_quota_exceeds_maximum")
            if max_quota_mib > domain_quota_mib:
                raise MailcowContractError("mailbox_maximum_quota_exceeds_domain")
            self._request(
                request,
                method="POST",
                path="/api/v1/add/domain",
                document={
                    "active": "1" if state == "enabled" else "0",
                    "aliases": alias_limit,
                    "backupmx": "1"
                    if _target_boolean(target, "backup_mx_enabled")
                    else "0",
                    "defquota": default_quota_mib,
                    "description": domain,
                    "domain": domain,
                    "gal": "1"
                    if _target_boolean(target, "global_address_list_enabled")
                    else "0",
                    "key_size": 0,
                    "mailboxes": mailbox_limit,
                    "maxquota": max_quota_mib,
                    "quota": domain_quota_mib,
                    "relay_all_recipients": "1"
                    if _target_boolean(target, "relay_all_recipients_enabled")
                    else "0",
                    "relay_unknown_only": "1"
                    if _target_boolean(target, "relay_unknown_recipients_enabled")
                    else "0",
                    "restart_sogo": "0",
                    "tags": [],
                    "template": "",
                },
                mutating=True,
            )
            current = self._get_one(request, path)
            if current is None:
                raise MailcowContractError("provider_outcome_unknown", ambiguous=True)
            return self._success(request, target, current, provider_ref=domain)
        if state == "absent":
            mutation_path = "/api/v1/delete/domain"
            document: object = [domain]
        else:
            mutation_path = "/api/v1/edit/domain"
            document = {
                "attr": {"active": "1" if state == "enabled" else "0"},
                "items": [domain],
            }
        self._request(
            request,
            method="POST",
            path=mutation_path,
            document=document,
            mutating=True,
        )
        observed = None if state == "absent" else self._get_one(request, path)
        return self._success(request, target, observed, provider_ref=domain)

    def _apply_alias(
        self,
        request: ProvisionApplyRequest,
        target: Mapping[str, object],
        state: str,
    ) -> ProvisioningResult:
        address = (
            f"{_string(target, 'alias_local_part')}@{_string(target, 'domain_name')}"
        )
        current = self._get_one(
            request,
            "/api/v1/get/alias/all",
            predicate=("address", address),
        )
        provider_id = str(current.get("id")) if current is not None else None
        if state == "absent":
            if provider_id is not None:
                self._request(
                    request,
                    method="POST",
                    path="/api/v1/delete/alias",
                    document=[provider_id],
                    mutating=True,
                )
            return self._success(request, target, None, provider_ref=address)
        destinations = target.get("alias_targets")
        if not isinstance(destinations, list) or not all(
            isinstance(item, str) and item for item in destinations
        ):
            raise MailcowContractError("alias_targets_invalid")
        document: object
        if provider_id is None:
            path = "/api/v1/add/alias"
            document = {
                "active": "1" if state == "enabled" else "0",
                "address": address,
                "goto": ",".join(destinations),
            }
        else:
            path = "/api/v1/edit/alias"
            document = {
                "attr": {
                    "active": "1" if state == "enabled" else "0",
                    "address": address,
                    "goto": ",".join(destinations),
                },
                "items": [provider_id],
            }
        self._request(
            request,
            method="POST",
            path=path,
            document=document,
            mutating=True,
        )
        observed = self._get_one(
            request,
            "/api/v1/get/alias/all",
            predicate=("address", address),
        )
        return self._success(request, target, observed, provider_ref=address)

    def _delete_app_password(
        self, request: ProvisionApplyRequest, target: Mapping[str, object]
    ) -> ProvisioningResult:
        provider_id = _string(target, "resource_ref")
        mailbox_ref = _mailbox_address(target)
        self._request(
            request,
            method="POST",
            path="/api/v1/delete/app-passwd",
            document=[provider_id],
            mutating=True,
        )
        return self._success(
            request,
            target,
            None,
            provider_ref=f"{mailbox_ref}#{provider_id}",
        )

    def _apply_dkim(
        self,
        request: ProvisionApplyRequest,
        target: Mapping[str, object],
        state: str,
    ) -> ProvisioningResult:
        domain = _string(target, "domain_name")
        path = f"/api/v1/get/dkim/{quote(domain, safe='')}"
        current = self._get_one(request, path)
        if state == "absent":
            if current is not None:
                self._request(
                    request,
                    method="POST",
                    path="/api/v1/delete/dkim",
                    document=[domain],
                    mutating=True,
                )
            return self._success(request, target, None, provider_ref=domain)
        if state == "disabled":
            raise MailcowContractError("dkim_disable_unsupported")
        if current is None:
            self._request(
                request,
                method="POST",
                path="/api/v1/add/dkim",
                document={"domains": domain, "key_size": 2048},
                mutating=True,
            )
            current = self._get_one(request, path)
        return self._success(request, target, current, provider_ref=domain)

    def observe(self, request: ProvisionObserveRequest) -> ProvisioningResult:
        try:
            kind = _string(request.target, "resource_kind")
            if kind == "application":
                raise MailcowContractError("immutable_subject_mapping_unverified")
            provider_ref = request.provider_operation_ref
            if kind in {"mailbox", "quota", "delivery"}:
                current = self._get_one(
                    request,
                    f"/api/v1/get/mailbox/{quote(provider_ref, safe='')}",
                )
            elif kind == "domain":
                current = self._get_one(
                    request,
                    f"/api/v1/get/domain/{quote(provider_ref, safe='')}",
                )
            elif kind == "dkim":
                current = self._get_one(
                    request,
                    f"/api/v1/get/dkim/{quote(provider_ref, safe='')}",
                )
            elif kind == "alias":
                current = self._get_one(
                    request,
                    "/api/v1/get/alias/all",
                    predicate=("address", provider_ref),
                )
            elif kind == "app_password":
                try:
                    mailbox_ref, provider_id = provider_ref.rsplit("#", 1)
                except ValueError:
                    raise MailcowContractError(
                        "app_password_provider_ref_invalid"
                    ) from None
                current = self._get_one(
                    request,
                    f"/api/v1/get/app-passwd/all/{quote(mailbox_ref, safe='')}",
                    predicate=("id", provider_id),
                )
            else:
                raise MailcowContractError("resource_kind_unsupported")
            target = {**dict(request.target), "desired_lifecycle_state": "present"}
            return self._success(request, target, current, provider_ref=provider_ref)
        except MailcowTransportError as exc:
            return _transport_result(exc)
        except MailcowContractError as exc:
            return _contract_result(exc)

    def cancel(self, request: ProvisionCancelRequest) -> ProvisioningResult:
        kind = _string(request.target, "resource_kind")
        resource_ref = _string(request.target, "resource_ref")
        return ProvisioningResult(
            status=ProvisionResultStatus.CANCELLED,
            provider_operation_ref=request.provider_operation_ref,
            evidence={
                "cancelled": True,
                "resource_kind": kind,
                "resource_ref": resource_ref,
            },
        )

    def _success(
        self,
        request: ProvisionApplyRequest | ProvisionObserveRequest,
        target: Mapping[str, object],
        current: Mapping[str, object] | None,
        *,
        provider_ref: str,
    ) -> ProvisioningResult:
        evidence = self._evidence(request, target, current, provider_ref=provider_ref)
        return ProvisioningResult(
            status=ProvisionResultStatus.SUCCEEDED,
            provider_operation_ref=provider_ref,
            evidence=evidence,
        )

    def _evidence(
        self,
        request: ProvisionApplyRequest | ProvisionObserveRequest,
        target: Mapping[str, object],
        current: Mapping[str, object] | None,
        *,
        provider_ref: str,
    ) -> dict[str, object]:
        kind = _string(target, "resource_kind")
        resource_ref = _string(target, "resource_ref")
        application_ref = _string(target, "application_ref")
        active = False if current is None else _boolean(current.get("active", True))
        lifecycle = (
            "absent" if current is None else ("enabled" if active else "disabled")
        )
        evidence: dict[str, object] = {
            "application_ref": application_ref,
            "healthy": True,
            "lifecycle_state": lifecycle,
            "resource_kind": kind,
            "resource_ref": resource_ref,
        }
        if kind in {"mailbox", "quota", "delivery"}:
            address = provider_ref
            domain = address.rsplit("@", 1)[-1]
            quota = (
                0
                if current is None
                else _integer(current.get("quota", 0), code="quota_invalid")
            )
            evidence.update(
                {
                    "delivery_enabled": active,
                    "domain_name": domain,
                    "mailbox_ref": address,
                    "quota_bytes": quota,
                }
            )
            if kind == "delivery":
                evidence["smtp_delivery_observed"] = False
        elif kind == "domain":
            domain = provider_ref
            hostname = _mail_hostname(_string(request.config, "admin_endpoint"))
            evidence.update(
                {
                    "dns_requirements": _dns_requirements(domain, hostname),
                    "domain_name": domain,
                    "mail_hostname": hostname,
                }
            )
        elif kind == "alias":
            evidence.update(
                {
                    "alias_ref": provider_ref,
                    "domain_name": provider_ref.rsplit("@", 1)[-1],
                }
            )
        elif kind == "app_password":
            if "#" in provider_ref:
                mailbox, _provider_id = provider_ref.rsplit("#", 1)
                domain = mailbox.rsplit("@", 1)[-1]
            else:
                domain = _string(target, "domain_name")
                mailbox = f"{_string(target, 'mailbox_local_part')}@{domain}"
            evidence.update(
                {
                    "app_password_configured": current is not None,
                    "domain_name": domain,
                    "mailbox_ref": mailbox,
                }
            )
        elif kind == "dkim":
            domain = provider_ref
            hostname = _mail_hostname(_string(request.config, "admin_endpoint"))
            evidence.update({"domain_name": domain, "mail_hostname": hostname})
            if current is None:
                evidence["observed_configuration_digest"] = _canonical_digest(evidence)
                return evidence
            selector = _string(current, "dkim_selector")
            record = _string(current, "dkim_txt")
            dns = _dns_requirements(domain, hostname)
            dns.append(
                {
                    "owner_name": f"{selector}._domainkey.{domain}",
                    "record_type": "TXT",
                    "required": True,
                    "requirement_kind": "dkim",
                    "ttl": 300,
                    "values": [record],
                }
            )
            evidence.update(
                {
                    "dkim_public_key_digest": "sha256:"
                    + hashlib.sha256(record.encode()).hexdigest(),
                    "dkim_record_name": f"{selector}._domainkey.{domain}",
                    "dkim_record_value": record,
                    "dkim_selector": selector,
                    "dns_requirements": dns,
                }
            )
        evidence["observed_configuration_digest"] = _canonical_digest(evidence)
        return evidence


class MailcowConnector:
    """Metadata-discovered connector with an injectable stateless transport."""

    def __init__(self, transport: MailcowTransport | None = None) -> None:
        self._handler: ProvisioningHandler = MailcowProvisioningHandler(
            transport if transport is not None else HttpxMailcowTransport()
        )

    @property
    def manifest(self) -> ConnectorManifest:
        return MANIFEST

    @property
    def historical_manifests(self) -> tuple[ConnectorManifest, ...]:
        return ()

    @property
    def modes(self) -> frozenset[ConnectorMode]:
        return frozenset({ConnectorMode.PROVISION})

    def provisioning_handler_for(self, capability_id: str) -> ProvisioningHandler:
        MANIFEST.require_declares(capability_id)
        return self._handler

    def validate_connection(
        self,
        *,
        config: dict[str, object],
        secrets: dict[str, object],
    ) -> tuple[Diagnostic, ...]:
        try:
            normalize_admin_endpoint(config.get("admin_endpoint"))
        except ValueError as exc:
            return (Diagnostic(ok=False, code=str(exc)),)
        material = secrets.get("admin_secret_ref")
        if not isinstance(material, str) or not material:
            return (Diagnostic(ok=False, code="required_material_unavailable"),)
        return ()


PLUGIN: Final = MailcowConnector()

__all__ = [
    "MailcowConnector",
    "MailcowContractError",
    "MailcowProvisioningHandler",
    "PLUGIN",
]
