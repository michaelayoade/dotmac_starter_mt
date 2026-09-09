"""Seed platform-default rows for every registered setting spec.

Called from `app.main`'s lifespan handler, guarded by
`settings.seed_on_startup` (env-overridable — set `SEED_ON_STARTUP=false` to
disable, e.g. on a read replica or when a separate deploy step seeds
instead). Idempotent: `ensure_by_key` never overwrites an existing row (an
operator's prior change included), so re-running on every app boot is safe.

Must NOT run at import time — only from the lifespan callback — so that a
bare `import app.main` (used by CI's docker-build health check and the
`python -c "import app.main"` smoke check) never touches the database.

Resolves THIS PROCESS's bound runtime
(`dotmac_kernel.session_runtime.resolve_database_runtime`, the
kernel-runtime-composition seam) rather than the request-scoped
`get_platform_db` FastAPI dependency, because this runs outside a request
against the `platform_api` DB role — the only role permitted to write
NULL-tenant rows on `domain_settings` (see the settings migration's RLS
policy) — and its non-request platform-session boundary. A product that
sealed its own `DatabaseRuntime` (`ProductAssemblySpec.database_runtime`) has
its settings seeded through THAT runtime; a deferred import of
`dotmac_kernel.db.platform_session` would still reach the reference
assembly's own instance regardless of what the product bound, which is the
wrong authority for a product deployment. Session construction itself stays
in `dotmac_kernel/session_runtime.py`/`dotmac_kernel/db.py` — the one
transaction authority (`tests/architecture/test_session_authority.py`).
`resolve_database_runtime` builds no engine at import (its own
`dotmac_kernel.db` reach, for the reference-assembly fallback, is
function-local), so importing it here at module scope is safe and does not
touch the database.
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
