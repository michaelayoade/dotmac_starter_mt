"""One closed refusal vocabulary: every refusal the agent can raise has a code here.

A code is a short machine word, never a message built from a value, so a
refusal cannot carry a secret into a log or an observation.
"""

from __future__ import annotations

import re
from typing import Final

CODES: Final = frozenset(
    {
        # configuration and environment
        "config_invalid",
        "db_environment_present",
        "runtime_dir_unsafe",
        # materials and parameters
        "materials_invalid",
        "materials_target_mismatch",
        "dispatch_identity_invalid",
        "dispatch_envelope_invalid",
        "dispatch_signature_invalid",
        "dispatch_plan_mismatch",
        "dispatch_replayed",
        "dispatch_superseded",
        "dispatch_journal_invalid",
        "dispatch_stale",
        "secrets_naming_mismatch",
        "accessor_state_invalid",
        "job_role_refused",
        "job_window_closed",
        "job_files_invalid",
        "job_mint_refused",
        "job_image_absent",
        "orphan_job_container",
        "job_receipt_invalid",
        "job_apply_class_mismatch",
        "job_input_mismatch",
        "foundation_job_entry_unavailable",
        "params_value_invalid",
        "params_secret_shaped",
        "params_digest_mismatch",
        "dotenv_present",
        "compose_override_present",
        "integrity_violation",
        # M11 legacy bridge
        "legacy_policy_off",
        "legacy_expired",
        "legacy_path_invalid",
        "legacy_source_unsafe",
        "legacy_credential_missing",
        "legacy_sha_mismatch",
        "legacy_names_mismatch",
        "legacy_name_collision",
        "legacy_malformed",
        # secret_id delivery and OpenBao Agent config
        "secret_delivery_failed",
        "secret_wrap_unexpected_path",
        "secret_wrap_already_unwrapped",
        "openbao_config_invalid",
        # CP API
        "response_signature_invalid",
        "response_binding_invalid",
        "response_malformed",
        "clock_skew",
        "cp_refused",
        "cp_unreachable",
        # runner
        "runner_start_failed",
        "runner_timeout",
        # lease (lane B)
        "lease_refused",
        "lease_ttl_invalid",
        "lease_unavailable",
        # image transfer (lane B)
        "transfer_inbox_unsafe",
        "transfer_archive_missing",
        "transfer_binary_unpinned",
        "transfer_registry_not_loopback",
        "transfer_registry_left_running",
        "transfer_repository_mismatch",
        "transfer_digest_mismatch",
        "transfer_command_failed",
        "transfer_deadline_exceeded",
        # reconcile (lane B)
        "reconcile_unavailable",
    }
)
_DETAIL: Final = re.compile(r"[a-z0-9_.-]{1,64}")


class Refusal(Exception):
    """A fail-closed stop. `code` is closed; `detail` is an optional CP word."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        if code not in CODES:
            raise ValueError("unknown_refusal_code")
        if detail is not None and not _DETAIL.fullmatch(detail):
            detail = "unrepresentable"
        super().__init__(code)
        self.code = code
        self.detail = detail

    def __str__(self) -> str:
        return self.code if self.detail is None else f"{self.code}:{self.detail}"


def require(condition: bool, code: str) -> None:
    if not condition:
        raise Refusal(code)
