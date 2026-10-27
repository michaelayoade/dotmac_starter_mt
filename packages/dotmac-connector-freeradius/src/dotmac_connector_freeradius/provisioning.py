"""PROVISION handlers: projection apply/observe/cancel and CoA disconnect.

Result discipline (the engine's settlement table is built around it):

* A refused secret fetch is TERMINAL with the closed
  `secret_fetch_refused` code — retrying would re-fetch, which ADR-0076
  forbids; the engine parks the step in reconciliation for a product-side
  reissue. An unreachable fetch is RETRYABLE.
* A database write that raises propagates: the engine maps a raised apply to
  AMBIGUOUS and follows with observe, which is the only honest next step
  when the write may have landed.
* Apply verifies by read-back before claiming success. Observed fingerprint
  in evidence is the step input's `expected_fingerprint`, asserted only
  after the rendered rows were read back exactly (password by presence).
* Disconnect answers are business outcomes in evidence, not engine
  failures: acknowledged/refused/unsupported all settle SUCCEEDED — the
  command was executed and the NAS answered. Silence is AMBIGUOUS.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Final

from dotmac_integration.spi import (
    NetworkTargetError,
    ProvisionApplyRequest,
    ProvisionCancelRequest,
    ProvisioningResult,
    ProvisionObserveRequest,
    ProvisionPlanRequest,
    ProvisionPlanResult,
    ProvisionResultStatus,
    bound_network_targets,
    require_network_target,
)

from dotmac_connector_freeradius.config import target_config
from dotmac_connector_freeradius.fetch import HttpSecretFetch
from dotmac_connector_freeradius.ports import (
    CoaPort,
    RadiusDbPort,
    SecretFetchPort,
    SecretFetchRefused,
    SecretFetchUnavailable,
    UnconfiguredSecretFetch,
)
from dotmac_connector_freeradius.render import (
    RenderRefused,
    render_projection,
    rows_match,
)

PROJECTION_CAPABILITY: Final = "network.access_projection.v1"
DISCONNECT_CAPABILITY: Final = "network.session_disconnect.v1"

DbFactory = Callable[[Mapping[str, object], Mapping[str, object]], RadiusDbPort]


def _result(
    status: ProvisionResultStatus,
    *,
    error_code: str | None = None,
    evidence: Mapping[str, object] | None = None,
    provider_operation_ref: str | None = None,
) -> ProvisioningResult:
    return ProvisioningResult(
        status=status,
        error_code=error_code,
        evidence=dict(evidence or {}),
        provider_operation_ref=provider_operation_ref,
    )


class FreeRadiusProvisioningHandler:
    def __init__(
        self,
        *,
        db_factory: DbFactory,
        secret_fetch: SecretFetchPort | None = None,
        coa_port: CoaPort,
    ) -> None:
        self._db_factory = db_factory
        self._secret_fetch = secret_fetch
        self._coa = coa_port

    def _fetch_for(self, request: ProvisionApplyRequest) -> SecretFetchPort:
        """The fetch transport for this request.

        An injected port (tests, assembly doubles) wins. Otherwise the
        installation's own config names the owner's endpoint and its token
        arrives as a materialized secret binding — the assembly never names
        this connector, so the transport choice has to be config, not code
        on the assembly side. No configured endpoint is the loud default.
        """
        if self._secret_fetch is not None:
            return self._secret_fetch
        settings = target_config(request.config)
        if settings.secret_fetch_base_url:
            token = request.secrets.get("secret_fetch_token")
            return HttpSecretFetch(
                base_url=settings.secret_fetch_base_url,
                token=token if isinstance(token, str) else None,
                timeout_seconds=settings.secret_fetch_timeout_seconds,
            )
        return UnconfiguredSecretFetch()

    # ── plan ────────────────────────────────────────────────────────────

    def plan(self, request: ProvisionPlanRequest) -> ProvisionPlanResult:
        # Owner-authored plans are validated, never rewritten. Shape errors
        # surface as TERMINAL at apply, where the failing step is named;
        # refusing here could only say "somewhere in the plan".
        return ProvisionPlanResult(steps=request.steps, plan_hash=request.plan_hash)

    # ── apply ───────────────────────────────────────────────────────────

    def apply(self, request: ProvisionApplyRequest) -> ProvisioningResult:
        if request.capability_id == DISCONNECT_CAPABILITY:
            return self._apply_disconnect(request)
        return self._apply_projection(request)

    def _apply_projection(self, request: ProvisionApplyRequest) -> ProvisioningResult:
        config = target_config(request.config)
        step_input = dict(request.step.input)
        username = str(step_input.get("username") or "")
        desired_state = str(step_input.get("desired_state") or "")

        cleartext: str | None = None
        # Reject renders no password row: naming a credential there is a
        # contract violation, and fetching would burn the single-use fetch.
        if desired_state == "enabled" and (
            str(step_input.get("policy_code") or "") != "reject"
        ):
            credential_id = step_input.get("credential_id")
            secret_version = step_input.get("secret_version")
            request_ref = step_input.get("projection_request_ref")
            if not (
                isinstance(credential_id, str)
                and isinstance(secret_version, int)
                and isinstance(request_ref, str)
            ):
                return _result(
                    ProvisionResultStatus.TERMINAL,
                    error_code="credential_identity_missing",
                )
            try:
                cleartext = self._fetch_for(request).fetch_secret(
                    credential_id=credential_id,
                    secret_version=secret_version,
                    projection_request_ref=request_ref,
                    operation_ref=request.operation_ref,
                )
            except SecretFetchRefused:
                return _result(
                    ProvisionResultStatus.TERMINAL,
                    error_code="secret_fetch_refused",
                )
            except SecretFetchUnavailable:
                return _result(
                    ProvisionResultStatus.RETRYABLE,
                    error_code="secret_fetch_unavailable",
                )

        try:
            rendered = render_projection(
                step_input, config=config.render, cleartext_secret=cleartext
            )
        except RenderRefused as exc:
            return _result(
                ProvisionResultStatus.TERMINAL,
                error_code="render_refused",
                evidence={"detail_code": str(exc)[:120]},
            )

        db = self._db_factory(request.config, request.secrets)
        usernames = [username]
        previous = step_input.get("previous_username")
        if isinstance(previous, str) and previous and previous != username:
            usernames.append(previous)
        written = db.apply_projection(
            usernames=usernames,
            rendered=rendered,
            lock_key=f"{config.lock_key_prefix}:{username}",
        )
        check, reply, groups = db.read_projection(username)
        if not rows_match(
            rendered,
            check_rows=check,
            reply_rows=reply,
            group_rows=groups,
            password_attribute=config.render.password_attribute,
        ):
            return _result(
                ProvisionResultStatus.AMBIGUOUS,
                error_code="readback_mismatch",
                provider_operation_ref=username,
            )
        applied_state = (
            desired_state
            if desired_state == "enabled"
            else ("absent" if rendered.rows_written == 0 else desired_state)
        )
        return _result(
            ProvisionResultStatus.SUCCEEDED,
            provider_operation_ref=username,
            evidence={
                "applied_state": applied_state,
                "credential_id": step_input.get("credential_id"),
                "secret_version": step_input.get("secret_version"),
                "observed_fingerprint": step_input.get("expected_fingerprint"),
                "rows_written": written,
                "target_ref": config.target_ref,
            },
        )

    def _apply_disconnect(self, request: ProvisionApplyRequest) -> ProvisioningResult:
        config = target_config(request.config)
        step_input = dict(request.step.input)
        address = str(step_input.get("nas_address") or "")
        port_value = step_input.get("coa_port")
        port = port_value if isinstance(port_value, int) else 3799
        try:
            require_network_target(
                bound_network_targets(request.config),
                address=address,
                port=port,
                protocol="udp",
            )
        except NetworkTargetError:
            return _result(
                ProvisionResultStatus.TERMINAL,
                error_code="network_target_refused",
            )
        db = self._db_factory(request.config, request.secrets)
        secret = db.nas_secret(
            nas_ref=str(step_input.get("nas_ref") or ""), nas_address=address
        )
        if secret is None:
            return _result(
                ProvisionResultStatus.TERMINAL,
                error_code="nas_secret_missing",
            )
        attributes: dict[str, str] = {
            "User-Name": str(step_input.get("username") or "")
        }
        if step_input.get("session_ref"):
            attributes["Acct-Session-Id"] = str(step_input["session_ref"])
        if step_input.get("framed_ipv4_address"):
            attributes["Framed-IP-Address"] = str(step_input["framed_ipv4_address"])
        outcome = self._coa.disconnect(
            address=address,
            port=port,
            secret=secret,
            attributes=attributes,
            timeout_seconds=config.coa_timeout_seconds,
        )
        evidence: dict[str, object] = {
            "disconnect_outcome": outcome.outcome,
            "error_cause": outcome.error_cause,
            "target_ref": config.target_ref,
        }
        if outcome.outcome == "unanswered":
            return _result(
                ProvisionResultStatus.AMBIGUOUS,
                error_code="disconnect_unanswered",
                evidence=evidence,
                provider_operation_ref=str(step_input.get("username") or ""),
            )
        return _result(
            ProvisionResultStatus.SUCCEEDED,
            evidence=evidence,
            provider_operation_ref=str(step_input.get("username") or ""),
        )

    # ── observe ─────────────────────────────────────────────────────────

    def observe(self, request: ProvisionObserveRequest) -> ProvisioningResult:
        if request.capability_id == DISCONNECT_CAPABILITY:
            # A disconnect has no readable target state; its truth arrives as
            # accounting. NOT_FOUND parks the step in reconciliation.
            return _result(ProvisionResultStatus.NOT_FOUND, error_code="not_observable")
        config = target_config(request.config)
        step_input = dict(request.target)
        username = str(step_input.get("username") or "")
        try:
            rendered = render_projection(
                step_input,
                config=config.render,
                # Presence-masked by rows_match; the value is never compared.
                cleartext_secret="x",  # noqa: S106 — presence sentinel, not a secret
            )
        except RenderRefused:
            return _result(ProvisionResultStatus.TERMINAL, error_code="render_refused")
        db = self._db_factory(request.config, request.secrets)
        check, reply, groups = db.read_projection(username)
        if rows_match(
            rendered,
            check_rows=check,
            reply_rows=reply,
            group_rows=groups,
            password_attribute=config.render.password_attribute,
        ):
            desired_state = str(step_input.get("desired_state") or "")
            return _result(
                ProvisionResultStatus.SUCCEEDED,
                provider_operation_ref=username,
                evidence={
                    "applied_state": (
                        desired_state
                        if desired_state == "enabled"
                        else "absent"
                        if rendered.rows_written == 0
                        else desired_state
                    ),
                    "credential_id": step_input.get("credential_id"),
                    "secret_version": step_input.get("secret_version"),
                    "observed_fingerprint": step_input.get("expected_fingerprint"),
                    "rows_written": rendered.rows_written,
                    "target_ref": config.target_ref,
                },
            )
        return _result(
            ProvisionResultStatus.TERMINAL,
            error_code="projection_drift",
            provider_operation_ref=username,
            evidence={
                "observed_check_rows": len(check),
                "observed_reply_rows": len(reply),
                "observed_group_rows": len(groups),
                "target_ref": config.target_ref,
            },
        )

    # ── cancel ──────────────────────────────────────────────────────────

    def cancel(self, request: ProvisionCancelRequest) -> ProvisioningResult:
        if request.capability_id == DISCONNECT_CAPABILITY:
            # Nothing to undo: a disconnect either happened or did not.
            return _result(ProvisionResultStatus.CANCELLED)
        config = target_config(request.config)
        username = str(dict(request.target).get("username") or "")
        db = self._db_factory(request.config, request.secrets)
        empty = render_projection(
            {"username": username, "desired_state": "disabled", "attributes": []},
            config=config.render,
            cleartext_secret=None,
        )
        db.apply_projection(
            usernames=[username],
            rendered=empty,
            lock_key=f"{config.lock_key_prefix}:{username}",
        )
        return _result(ProvisionResultStatus.CANCELLED, provider_operation_ref=username)


__all__ = [
    "DISCONNECT_CAPABILITY",
    "DbFactory",
    "FreeRadiusProvisioningHandler",
    "PROJECTION_CAPABILITY",
]
