"""The assembly-bound audit writer (a seam, like Template Studio's test-send seam).

A STATELESS module owns no migration lineage, so it cannot declare the
`tenant_audit_log.v1` prerequisite that calling the kernel's audit writer
requires of an installable module (`requires` is refused for a module without a
lineage). The assembly, which runs the kernel lineage and so always has the
table, therefore binds the writer once:

    dotmac_settings_admin.configure_audit_writer(dotmac_kernel.write_audit_event)

Fail closed: with no writer bound every settings write refuses BEFORE storing
anything, so a wiring mistake can never produce an unaudited change. The writer
is called with the kernel's keyword arguments (explicit actor pair, action,
entity) unchanged; the action must still be declared on the manifest.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

AuditWriter = Callable[..., Any]

_writer: AuditWriter | None = None


class AuditWriterNotConfiguredError(RuntimeError):
    """No audit writer is bound; settings writes are refused."""


def configure_audit_writer(writer: AuditWriter | None) -> None:
    """Bind (or with None, unbind) the writer. Called by the assembly at import."""
    global _writer
    _writer = writer


def require_audit_writer() -> AuditWriter:
    if _writer is None:
        raise AuditWriterNotConfiguredError(
            "dotmac-settings-admin has no audit writer bound; the assembly must "
            "call configure_audit_writer(write_audit_event). Refusing to write "
            "settings without an audit trail."
        )
    return _writer
