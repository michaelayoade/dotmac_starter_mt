"""Privacy normalisation before any observation becomes persistent."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from dotmac_web_analytics.contracts import CollectionRefused

_EMAIL = re.compile(r"(?i)(?:[a-z0-9.!#$%&'*+/=?^_`{|}~-]+)@(?:[a-z0-9-]+\.)+[a-z]{2,}")
_BEARER = re.compile(r"(?i)\b(?:bearer|basic)\s+[a-z0-9._~+/=-]{8,}")
_INLINE_SECRET = re.compile(
    r"(?i)\b(?:access_token|api[_-]?key|authorization|customer[_-]?id|"
    r"password|refresh_token|secret|session|subscriber[_-]?id|token)\s*[:=]"
)
_SENSITIVE_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "authorization",
        "customer_id",
        "email",
        "form_value",
        "jwt",
        "password",
        "phone",
        "refresh_token",
        "secret",
        "session",
        "subscriber_id",
        "token",
    }
)


@dataclass(frozen=True, slots=True)
class CanonicalLocation:
    origin: str
    path: str


def sensitive_text(value: str) -> bool:
    decoded = unquote(value)
    return bool(
        _EMAIL.search(decoded)
        or _BEARER.search(decoded)
        or _INLINE_SECRET.search(decoded)
    )


def canonicalize_url(
    value: str, *, allowed_origins: tuple[str, ...]
) -> CanonicalLocation:
    """Return origin/path only; query and fragment never survive this function."""
    try:
        parsed = urlsplit(value)
        parsed_port = parsed.port
    except ValueError as exc:
        raise CollectionRefused("URL is malformed") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise CollectionRefused("URL must use HTTP(S) and contain a host")
    if parsed.username is not None or parsed.password is not None:
        raise CollectionRefused("sensitive URL user information is forbidden")
    if sensitive_text(value):
        raise CollectionRefused("sensitive URL value is forbidden")
    for pair in parsed.query.split("&") if parsed.query else ():
        key = unquote(pair.partition("=")[0]).strip().lower()
        if key in _SENSITIVE_KEYS or any(term in key for term in _SENSITIVE_KEYS):
            raise CollectionRefused("sensitive query-string key is forbidden")

    default_port = (parsed.scheme.lower() == "https" and parsed_port == 443) or (
        parsed.scheme.lower() == "http" and parsed_port == 80
    )
    port = "" if parsed_port is None or default_port else f":{parsed_port}"
    origin = f"{parsed.scheme.lower()}://{parsed.hostname.lower()}{port}"
    normalized_allowed = {item.rstrip("/").lower() for item in allowed_origins}
    if origin.lower() not in normalized_allowed:
        raise CollectionRefused("origin is not registered for this property")
    path = re.sub(r"/{2,}", "/", unquote(parsed.path or "/"))
    if sensitive_text(path):
        raise CollectionRefused("sensitive URL path value is forbidden")
    if not path.startswith("/"):
        path = f"/{path}"
    return CanonicalLocation(origin, path[:2048])


def validate_safe_scalar(value: str) -> None:
    if sensitive_text(value):
        raise CollectionRefused("sensitive event attribute value is forbidden")


__all__ = [
    "CanonicalLocation",
    "canonicalize_url",
    "sensitive_text",
    "validate_safe_scalar",
]
