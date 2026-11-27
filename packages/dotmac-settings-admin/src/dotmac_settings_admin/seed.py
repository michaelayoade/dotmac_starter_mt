"""Seed platform-default rows for every registered setting spec.

Moved verbatim in behaviour from the reference app's `settings` feature: called
from the app lifespan (gated by `settings.seed_on_startup`), idempotent
(`ensure_by_key` never overwrites an operator's row), and never at import time.
It resolves THIS PROCESS's bound runtime (`resolve_database_runtime`) and uses
its platform session, the only role permitted to write NULL-tenant rows.
"""

from __future__ import annotations

import logging

from dotmac_kernel.session_runtime import resolve_database_runtime
from dotmac_kernel.settings_admin import all_specs, ensure_by_key

logger = logging.getLogger(__name__)


def seed_platform_defaults() -> None:
    try:
        with resolve_database_runtime().platform_session() as db:
            for spec in all_specs():
                ensure_by_key(db, spec.domain, spec.key, spec.default, tenant_id=None)
    except Exception:
        logger.exception("Failed to seed platform setting defaults")
        raise
