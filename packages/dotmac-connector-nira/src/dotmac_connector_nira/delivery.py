"""The DELIVERY adapter: one dispatched command, one classified outcome.

Thin by design. The frame XML lives in :mod:`frames`, the socket in
:mod:`epp`; this file resolves configuration, checks the binding is allowed to
issue the requested operation, runs one command inside a login/logout session,
and maps the registry's result code to the engine's :class:`Outcome`.

## One capability is one blast radius

``ACTIONS_BY_CAPABILITY`` is the same security shape as the Paystack connector:
a binding enabled for ``registry.host_provision.v1`` cannot be made to renew or
transfer a domain by a crafted payload, because the operation it names is not
in that capability's allowed set. The table is checked for TOTALITY at import —
an operation reachable through no capability is dead, one reachable through two
would widen every binding that names either.

## What this handler never does

It does not decide whether the command should have been issued, does not read a
product database, does not retry, and does not reschedule itself. The engine
hands it a materialized secret and an idempotency key and takes back a
classification; the retry curve, the dead-letter and reconciliation are the
engine's.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from dotmac_integration.retry import Outcome, OutcomeStatus
from dotmac_integration.spi import DispatchRequest

from dotmac_connector_nira import frames
from dotmac_connector_nira.epp import (
    EppProtocolError,
    EppResult,
    EppSession,
    EppTransportError,
    classify_result,
)

__all__ = [
    "ACTIONS_BY_CAPABILITY",
    "OUTBOUND_CAPABILITY_IDS",
    "OUTBOUND_CONFIG_SCHEMA",
    "EPP_PASSWORD",
    "CLIENT_PEM",
    "ALLOWED_EGRESS_HOSTS",
    "OTE_HOST",
    "NiraDeliveryHandler",
    "OperationContractError",
]

EPP_PASSWORD: Final = "epp_password"
CLIENT_PEM: Final = "client_pem"
OTE_HOST: Final = "ote.registry.ng"
ALLOWED_EGRESS_HOSTS: Final = (OTE_HOST,)

AVAILABILITY_CAPABILITY: Final = "registry.availability.v1"
DOMAIN_INFO_CAPABILITY: Final = "registry.domain_info.v1"
DOMAIN_REGISTER_CAPABILITY: Final = "registry.domain_register.v1"
DOMAIN_RENEW_CAPABILITY: Final = "registry.domain_renew.v1"
DOMAIN_UPDATE_CAPABILITY: Final = "registry.domain_update.v1"
DOMAIN_TRANSFER_CAPABILITY: Final = "registry.domain_transfer.v1"
HOST_PROVISION_CAPABILITY: Final = "registry.host_provision.v1"
CONTACT_PROVISION_CAPABILITY: Final = "registry.contact_provision.v1"


class OperationContractError(ValueError):
    """A payload does not satisfy the operation's required shape."""


#: Which operations each bound capability may issue. Data, not ``or`` clauses,
#: so widening one is a reviewable diff. An operation is the concrete EPP verb;
#: a capability is the authority a binding is granted.
ACTIONS_BY_CAPABILITY: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {
        AVAILABILITY_CAPABILITY: frozenset({"domain_check"}),
        DOMAIN_INFO_CAPABILITY: frozenset({"domain_info"}),
        DOMAIN_REGISTER_CAPABILITY: frozenset({"domain_create"}),
        DOMAIN_RENEW_CAPABILITY: frozenset({"domain_renew"}),
        DOMAIN_UPDATE_CAPABILITY: frozenset({"domain_update_ns"}),
        DOMAIN_TRANSFER_CAPABILITY: frozenset({"domain_transfer"}),
        HOST_PROVISION_CAPABILITY: frozenset({"host_create", "host_check"}),
        CONTACT_PROVISION_CAPABILITY: frozenset({"contact_check"}),
    }
)

OUTBOUND_CAPABILITY_IDS: Final[tuple[str, ...]] = tuple(ACTIONS_BY_CAPABILITY)


def _misallocated(
    operations: frozenset[str], allocation: Mapping[str, frozenset[str]]
) -> frozenset[str]:
    """Operations reachable through no capability, or through more than one."""
    counted = [op for ops in allocation.values() for op in ops]
    once = {op for op in counted if counted.count(op) == 1}
    return (operations - once) | (frozenset(counted) - operations)


_ALL_OPERATIONS: Final[frozenset[str]] = frozenset(
    {
        "domain_check",
        "domain_info",
        "domain_create",
        "domain_renew",
        "domain_update_ns",
        "domain_transfer",
        "host_create",
        "host_check",
        "contact_check",
    }
)

_MISALLOCATED: Final[frozenset[str]] = _misallocated(
    _ALL_OPERATIONS, ACTIONS_BY_CAPABILITY
)
if _MISALLOCATED:  # pragma: no cover - import-time guard, proved by test
    raise RuntimeError(
        f"nira operations {sorted(_MISALLOCATED)} are unreachable or reachable "
        "through more than one capability binding"
    )


#: Every outbound capability shares this configuration. ``connect_timeout`` and
#: ``read_timeout`` are REQUIRED, not defaulted — a hidden timeout on a registry
#: path is a deployment decision nobody made.
OUTBOUND_CONFIG_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["host", "port", "clid", "connect_timeout", "read_timeout"],
    "properties": {
        "host": {"type": "string", "enum": list(ALLOWED_EGRESS_HOSTS)},
        "port": {"type": "integer", "minimum": 1, "maximum": 65535},
        "clid": {"type": "string", "minLength": 1},
        "currency": {"type": "string", "pattern": "^[A-Z]{3}$"},
        "connect_timeout": {"type": "number", "minimum": 1, "maximum": 120},
        "read_timeout": {"type": "number", "minimum": 1, "maximum": 300},
    },
}

# EPP result-token -> engine OutcomeStatus. One table, so the mapping from
# registry semantics to retry policy is reviewable rather than scattered.
_STATUS_BY_TOKEN: Final[Mapping[str, OutcomeStatus]] = MappingProxyType(
    {
        "ok": OutcomeStatus.SUCCEEDED,
        "ok_no_messages": OutcomeStatus.RECONCILIATION_REQUIRED,
        # Neither code proves identity or prior ownership. A create that finds
        # an object may have found somebody else's object; an absent object may
        # contradict local state. The owning domain must reconcile both.
        "object_exists": OutcomeStatus.RECONCILIATION_REQUIRED,
        "object_absent": OutcomeStatus.RECONCILIATION_REQUIRED,
        "pending": OutcomeStatus.RECONCILIATION_REQUIRED,  # 1001 async completion
        "authorization": OutcomeStatus.TERMINAL,  # IP/creds — no retry helps
        "authentication": OutcomeStatus.TERMINAL,
        "client_error": OutcomeStatus.TERMINAL,  # our command was invalid
        "registry_failure": OutcomeStatus.RETRYABLE,  # transient registry fault
        "unknown": OutcomeStatus.RECONCILIATION_REQUIRED,
    }
)

_READ_OPERATIONS: Final = frozenset(
    {
        "domain_check",
        "domain_info",
        "domain_transfer_query",
        "host_check",
        "contact_check",
    }
)
_TRANSFER_OPERATIONS: Final = frozenset(
    {"request", "approve", "reject", "cancel", "query"}
)


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _material(secrets: Mapping[str, object], name: str) -> str | None:
    value = secrets.get(name)
    return value if isinstance(value, str) and value else None


class NiraDeliveryHandler:
    """Executes one registry command for one bound capability."""

    def __init__(self, capability_id: str) -> None:
        if capability_id not in ACTIONS_BY_CAPABILITY:
            raise OperationContractError(f"unknown capability {capability_id!r}")
        self._capability_id = capability_id

    def __call__(self, request: DispatchRequest) -> Outcome:
        if request.capability_id != self._capability_id:
            raise OperationContractError("capability mismatch on dispatch")
        operation = self._operation(request.payload)
        allowed = ACTIONS_BY_CAPABILITY[self._capability_id]
        if operation not in allowed:
            return Outcome(
                status=OutcomeStatus.TERMINAL, error_code="operation_not_allowed"
            )
        cltrid = request.idempotency_key
        pw = _material(request.secrets, EPP_PASSWORD)
        if pw is None:
            return Outcome(
                status=OutcomeStatus.TERMINAL,
                error_code="required_material_unavailable",
            )
        try:
            frame = self._frame(operation, request.payload, cltrid)
        except OperationContractError as exc:
            return Outcome(status=OutcomeStatus.TERMINAL, error_code=str(exc))
        result_operation = (
            "domain_transfer_query"
            if operation == "domain_transfer"
            and _text(request.payload.get("transfer_op")) == "query"
            else operation
        )
        return self._execute(
            frame,
            request.config,
            pw,
            _material(request.secrets, CLIENT_PEM),
            cltrid,
            result_operation,
        )

    # -- payload -> frame ----------------------------------------------------

    @staticmethod
    def _operation(payload: Mapping[str, object]) -> str:
        op = _text(payload.get("operation"))
        if op is None:
            raise OperationContractError("payload_missing_operation")
        return op

    def _frame(self, operation: str, payload: Mapping[str, object], cltrid: str) -> str:
        if operation == "domain_check":
            return frames.domain_check(
                self._names(payload),
                cltrid=cltrid,
                currency=_text(payload.get("currency")),
            )
        if operation == "domain_info":
            return frames.domain_info(self._name(payload), cltrid=cltrid)
        if operation == "host_create":
            return frames.host_create(
                self._name(payload),
                addrs=self._strs(payload.get("addrs")),
                cltrid=cltrid,
            )
        if operation == "host_check":
            return frames.host_check(self._names(payload), cltrid=cltrid)
        if operation == "contact_check":
            return frames.contact_check(self._names(payload, key="ids"), cltrid=cltrid)
        if operation == "domain_create":
            return frames.domain_create(
                self._name(payload),
                period_years=self._req_int(payload, "period_years"),
                registrant=self._req_str(payload, "registrant"),
                nameservers=self._strs(payload.get("nameservers")),
                auth_pw=self._req_str(payload, "auth_pw"),
                contacts=self._contacts(payload.get("contacts")),
                cltrid=cltrid,
            )
        if operation == "domain_renew":
            return frames.domain_renew(
                self._name(payload),
                current_expiry=self._req_str(payload, "current_expiry"),
                period_years=self._req_int(payload, "period_years"),
                cltrid=cltrid,
            )
        if operation == "domain_update_ns":
            return frames.domain_update_ns(
                self._name(payload),
                add=self._strs(payload.get("add")),
                remove=self._strs(payload.get("remove")),
                cltrid=cltrid,
            )
        if operation == "domain_transfer":
            transfer_op = self._req_str(payload, "transfer_op")
            if transfer_op not in _TRANSFER_OPERATIONS:
                raise OperationContractError("payload_invalid_transfer_op")
            return frames.domain_transfer(
                self._name(payload),
                op=transfer_op,
                auth_pw=self._req_str(payload, "auth_pw"),
                period_years=_int(payload.get("period_years")),
                cltrid=cltrid,
            )
        raise OperationContractError(f"unbuildable_operation:{operation}")

    # -- execution -----------------------------------------------------------

    def _execute(
        self,
        command_frame: str,
        config: Mapping[str, object],
        pw: str,
        client_pem: str | None,
        cltrid: str,
        operation: str,
    ) -> Outcome:
        host = _text(config.get("host"))
        port = _int(config.get("port"))
        clid = _text(config.get("clid"))
        if host is None or port is None or clid is None:
            return Outcome(
                status=OutcomeStatus.TERMINAL, error_code="config_incomplete"
            )
        if host not in ALLOWED_EGRESS_HOSTS:
            return Outcome(
                status=OutcomeStatus.TERMINAL, error_code="egress_host_not_allowed"
            )
        connect_timeout = _num(config.get("connect_timeout"))
        read_timeout = _num(config.get("read_timeout"))
        if connect_timeout is None or read_timeout is None:
            return Outcome(status=OutcomeStatus.TERMINAL, error_code="timeout_invalid")
        session = EppSession(
            host,
            port,
            client_pem=client_pem,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
        )
        logged_in = False
        try:
            try:
                session.connect()
                login = session.request(
                    frames.login(clid, pw, cltrid=f"{cltrid}-login")
                )
            except (EppTransportError, EppProtocolError) as exc:
                # No business command was attempted. Retrying a connection or
                # login cannot duplicate the requested domain effect.
                return Outcome(
                    status=OutcomeStatus.RETRYABLE,
                    error_code="epp_session_unavailable",
                    error_detail=str(exc)[:500],
                )
            if classify_result(login.code) != "ok":
                return self._outcome("login", login)
            logged_in = True
            try:
                result = session.request(command_frame)
            except (EppTransportError, EppProtocolError) as exc:
                # The command bytes may already have reached the registry. A
                # blind retry can double-create, renew or transfer; park it for
                # an explicit info/query reconciliation instead.
                return Outcome(
                    status=OutcomeStatus.RECONCILIATION_REQUIRED,
                    error_code="epp_command_outcome_unknown",
                    error_detail=str(exc)[:500],
                )
            return self._outcome(operation, result)
        finally:
            if logged_in:
                try:
                    session.request(frames.logout(cltrid=f"{cltrid}-logout"))
                except (EppProtocolError, EppTransportError):
                    pass
            session.close()

    @staticmethod
    def _outcome(operation: str, result: EppResult) -> Outcome:
        classification = classify_result(result.code)
        if classification == "ok" and operation in _READ_OPERATIONS:
            # The connector has no authority to invent a domain result schema.
            # Until the owning domain publishes one, a successful read is
            # evidence to reconcile, not a falsely body-less success.
            return Outcome(
                status=OutcomeStatus.RECONCILIATION_REQUIRED,
                error_code="domain_result_contract_unavailable",
                error_detail=result.message[:500] or None,
                provider_reference=f"epp:{result.code}",
            )
        status = _STATUS_BY_TOKEN.get(
            classification, OutcomeStatus.RECONCILIATION_REQUIRED
        )
        return Outcome(
            status=status,
            error_code=(None if status is OutcomeStatus.SUCCEEDED else classification),
            error_detail=result.message[:500] or None,
            provider_status_code=None,
            provider_reference=f"epp:{result.code}",
        )

    # -- payload helpers -----------------------------------------------------

    @staticmethod
    def _name(payload: Mapping[str, object]) -> str:
        name = _text(payload.get("name"))
        if name is None:
            raise OperationContractError("payload_missing_name")
        return name

    @staticmethod
    def _names(payload: Mapping[str, object], *, key: str = "names") -> tuple[str, ...]:
        raw = payload.get(key)
        if isinstance(raw, str) and raw.strip():
            return (raw,)
        names = tuple(v for v in NiraDeliveryHandler._strs(raw))
        if not names:
            raise OperationContractError(f"payload_missing_{key}")
        return names

    @staticmethod
    def _strs(raw: object) -> tuple[str, ...]:
        if not isinstance(raw, list | tuple):
            return ()
        return tuple(v for v in raw if isinstance(v, str) and v.strip())

    @staticmethod
    def _contacts(raw: object) -> dict[str, str]:
        if not isinstance(raw, Mapping):
            return {}
        return {str(k): v for k, v in raw.items() if isinstance(v, str) and v.strip()}

    @staticmethod
    def _req_str(payload: Mapping[str, object], key: str) -> str:
        value = _text(payload.get(key))
        if value is None:
            raise OperationContractError(f"payload_missing_{key}")
        return value

    @staticmethod
    def _req_int(payload: Mapping[str, object], key: str) -> int:
        value = _int(payload.get(key))
        if value is None or value < 1:
            raise OperationContractError(f"payload_missing_{key}")
        return value


def _num(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None
