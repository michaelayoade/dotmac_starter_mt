"""Transactional fence for human-triggered delivery resends.

A product declares a :class:`ResendFence` — its consequence tables, its
authority models, and which tables may be written in each named phase —
and its command layer runs inside the fence: caller-transaction
classification and refusal, SQL authority admission, native
flush/rollback abort handling, and atomic receipt pinning under fresh
authority re-reads. Authority checks, enqueue, and audit stay in the
product. The package owns no tables and issues no engine or policy.
"""

from __future__ import annotations

from typing import Final

from .fence import (
    ReminderResendError,
    ReminderUnit,
    ResendFence,
    WritePhase,
    authority_preflight,
    caller_configuration,
    configure_default_fence,
    enter_resend,
    finish_resend,
    pending_authority,
    prepare_resend,
    recover_previous,
    values,
)
from .manifest import module

__version__: Final = "0.1.0a1"

__all__ = [
    "ReminderResendError",
    "ReminderUnit",
    "ResendFence",
    "WritePhase",
    "__version__",
    "authority_preflight",
    "caller_configuration",
    "configure_default_fence",
    "enter_resend",
    "finish_resend",
    "module",
    "pending_authority",
    "prepare_resend",
    "recover_previous",
    "values",
]
