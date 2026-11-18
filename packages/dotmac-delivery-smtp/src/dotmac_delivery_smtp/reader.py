"""The reader of the five SMTP settings: resolves them into an `SmtpConfig`."""

from __future__ import annotations

from uuid import UUID

from dotmac_kernel.settings_resolver import resolve
from sqlalchemy.orm import Session

from dotmac_delivery_smtp import settings
from dotmac_delivery_smtp.config import SmtpConfig

_SPECS = {
    "host": settings.HOST,
    "port": settings.PORT,
    "starttls": settings.STARTTLS,
    "username": settings.USERNAME,
    "from_address": settings.FROM_ADDRESS,
}


def resolve_config(
    db: Session, tenant_id: UUID | None, *, production: bool
) -> SmtpConfig:
    """Resolve the five settings into a validated-on-use `SmtpConfig`.

    `production` is the product's own environment fact, passed in: this package
    reads no environment.
    """
    return SmtpConfig(
        host=resolve(db, _SPECS["host"], tenant_id=tenant_id),
        port=resolve(db, _SPECS["port"], tenant_id=tenant_id),
        starttls=resolve(db, _SPECS["starttls"], tenant_id=tenant_id),
        username=resolve(db, _SPECS["username"], tenant_id=tenant_id),
        from_address=resolve(db, _SPECS["from_address"], tenant_id=tenant_id),
        production=production,
    )


__all__ = ["resolve_config"]
