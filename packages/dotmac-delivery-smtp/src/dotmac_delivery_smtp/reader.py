"""The reader of the five SMTP settings: resolves them into an `SmtpConfig`.

PLATFORM SCOPE ONLY. The password is one platform secret held in the kernel
`SecretSource`, so every setting that decides WHERE it is sent (host, port,
starttls, username, from address) must be a platform decision too. Starter's
generic settings editor lets a tenant admin write a tenant row for any
registered spec; if those rows were honoured, a tenant could point `host` at a
server of its own (with a valid certificate) and `login()` would hand it the
platform password. Reads therefore name `SettingScope.platform()` explicitly,
which never consults a tenant row, so a tenant-written value is inert. (The
tenant editor can still store such a row and display it; it never takes effect
here. A product that wants the editor to refuse the write must deny these keys
there, which is product/app work and not part of this package.)
"""

from __future__ import annotations

from typing import Any

from dotmac_kernel.setting_scopes import SettingScope
from dotmac_kernel.settings_resolver import SettingSpec, resolve
from sqlalchemy.orm import Session

from dotmac_delivery_smtp import settings
from dotmac_delivery_smtp.config import SmtpConfig

_SPECS: dict[str, SettingSpec[Any]] = {
    "host": settings.HOST,
    "port": settings.PORT,
    "starttls": settings.STARTTLS,
    "username": settings.USERNAME,
    "from_address": settings.FROM_ADDRESS,
}


def resolve_config(db: Session, *, production: bool) -> SmtpConfig:
    """Resolve the five settings at platform scope into an `SmtpConfig`.

    `production` is the product's own environment fact, passed in: this package
    reads no environment. The config is validated again on every send.
    """
    scope = SettingScope.platform()
    return SmtpConfig(
        host=resolve(db, _SPECS["host"], scope=scope),
        port=resolve(db, _SPECS["port"], scope=scope),
        starttls=resolve(db, _SPECS["starttls"], scope=scope),
        username=resolve(db, _SPECS["username"], scope=scope),
        from_address=resolve(db, _SPECS["from_address"], scope=scope),
        production=production,
    )


__all__ = ["resolve_config"]
