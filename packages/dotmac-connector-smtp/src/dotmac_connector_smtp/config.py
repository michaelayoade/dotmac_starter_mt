"""Validated connection configuration (no password: that is a SecretSource name)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from email.utils import parseaddr


class SmtpConfigError(RuntimeError):
    """The SMTP configuration cannot be used. Not a delivery outcome: raised so
    no receipt records a recipient-side verdict for an operator-side fault."""


_DOMAIN_RE = re.compile(
    r"[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+"
)
_LOCAL_RE = re.compile(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]{1,64}")


def valid_from_address(value: str) -> bool:
    if any(c in value for c in "\r\n\x00") or len(value) > 320:
        return False
    _name, addr = parseaddr(value)
    local, sep, domain = addr.rpartition("@")
    return bool(sep and _LOCAL_RE.fullmatch(local) and _DOMAIN_RE.fullmatch(domain))


@dataclass(frozen=True, slots=True)
class SmtpConfig:
    host: str
    port: int
    starttls: bool
    username: str
    from_address: str
    #: The product's own "this is production" fact. Passed in; never read from
    #: the environment here.
    production: bool = True

    def validate(self) -> None:
        if not self.host or not self.host.strip():
            raise SmtpConfigError("SMTP host is not configured")
        if any(c in self.host for c in "\r\n\x00 "):
            raise SmtpConfigError("SMTP host is malformed")
        if not (1 <= self.port <= 65535):
            raise SmtpConfigError("SMTP port is out of range")
        if self.production and not self.starttls:
            raise SmtpConfigError(
                "STARTTLS must be required when sending in production"
            )
        if self.username and not self.starttls:
            raise SmtpConfigError("refusing to send credentials without STARTTLS")
        if not valid_from_address(self.from_address):
            raise SmtpConfigError("SMTP from address is missing or malformed")

    @property
    def from_domain(self) -> str:
        return parseaddr(self.from_address)[1].rpartition("@")[2].lower()


__all__ = ["SmtpConfig", "SmtpConfigError", "valid_from_address"]
