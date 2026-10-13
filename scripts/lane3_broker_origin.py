"""Canonical broker origin and finite exact-origin policy for Lane 3.

``canonical_origin`` accepts exactly ``https://<lowercase-host>`` and refuses
everything else with a fixed label. It never normalises a refused input into an
accepted one (no lowercasing, no port stripping, no trailing-dot removal).

``OriginPolicy`` is a finite, independently approved, exact set of canonical
origins. Membership is exact string equality; there is no suffix, wildcard or
grammar match. An origin outside the set is simply not admitted.

Python standard library only.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
from collections.abc import Mapping
from typing import Any, Final

POLICY_SCHEMA: Final = "dotmac.lane3.broker-origin-policy.v1"
MAX_ORIGINS: Final = 64
MAX_HOST: Final = 253
MAX_LABEL: Final = 63
_SCHEME: Final = "https://"
_LABEL: Final = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


class OriginRefused(ValueError):
    """The origin or policy is refused. ``label`` is a fixed token, never input."""

    def __init__(self, label: str) -> None:
        super().__init__(label)
        self.label = label

    def __str__(self) -> str:
        return self.label


def canonical_origin(value: Any) -> str:
    """Return ``value`` unchanged if it is a canonical origin, else refuse."""
    if type(value) is not str:
        raise OriginRefused("origin.type")
    if not value or any(ord(c) <= 0x20 or ord(c) == 0x7F for c in value):
        raise OriginRefused("origin.whitespace")
    if not value.isascii():
        raise OriginRefused("origin.non_ascii")
    if not value.startswith(_SCHEME):
        raise OriginRefused("origin.scheme")
    host = value[len(_SCHEME) :]
    if "%" in host:
        raise OriginRefused("origin.percent")
    if "\\" in host:
        raise OriginRefused("origin.path")
    if "@" in host:
        raise OriginRefused("origin.userinfo")
    if "#" in host:
        raise OriginRefused("origin.fragment")
    if "?" in host:
        raise OriginRefused("origin.query")
    if "/" in host:
        raise OriginRefused("origin.path")
    if "[" in host or "]" in host:
        raise OriginRefused("origin.ip_literal")
    if ":" in host:
        raise OriginRefused("origin.port")
    if not host:
        raise OriginRefused("origin.empty")
    if host.endswith("."):
        raise OriginRefused("origin.trailing_dot")
    if host != host.lower():
        raise OriginRefused("origin.uppercase")
    if len(host) > MAX_HOST:
        raise OriginRefused("origin.length")
    labels = host.split(".")
    if len(labels) < 2:
        raise OriginRefused("origin.label")
    for label in labels:
        if len(label) > MAX_LABEL:
            raise OriginRefused("origin.length")
        if _LABEL.fullmatch(label) is None:
            raise OriginRefused("origin.label")
        if label.startswith("xn--"):
            raise OriginRefused("origin.idna")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise OriginRefused("origin.ip_literal")
    if labels[-1].isdigit():
        # Numeric last label: legacy IPv4 spellings such as 0x7f.1 or 2130706433.
        raise OriginRefused("origin.ip_literal")
    return value


def host_of(origin: str) -> str:
    """The host part of an already canonical origin (re-validates)."""
    return canonical_origin(origin)[len(_SCHEME) :]


def _policy_digest(origins: list[str]) -> str:
    """SHA-256 over the schema id and the sorted origins, one per line (LF).

    Origins are canonical ASCII without whitespace, so the text form is
    unambiguous. It is deliberately not a JSON document.
    """
    text = "\n".join([POLICY_SCHEMA, *origins]) + "\n"
    return hashlib.sha256(text.encode("ascii")).hexdigest()


class OriginPolicy:
    """A finite exact set of canonical origins plus a digest of that set."""

    __slots__ = ("_digest", "_origins")

    def __init__(self, origins: frozenset[str], digest: str) -> None:
        self._origins = origins
        self._digest = digest

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> OriginPolicy:
        if not isinstance(mapping, Mapping) or set(mapping) != {"schema", "origins"}:
            raise OriginRefused("policy.shape")
        if mapping["schema"] != POLICY_SCHEMA:
            raise OriginRefused("policy.schema")
        raw = mapping["origins"]
        if type(raw) not in (list, tuple) or not raw or len(raw) > MAX_ORIGINS:
            raise OriginRefused("policy.origins")
        seen: list[str] = []
        for item in raw:
            try:
                seen.append(canonical_origin(item))
            except OriginRefused:
                raise OriginRefused("policy.origin") from None
        if len(set(seen)) != len(seen):
            raise OriginRefused("policy.duplicate")
        ordered = sorted(seen)
        return cls(frozenset(ordered), _policy_digest(ordered))

    @property
    def origins(self) -> frozenset[str]:
        return self._origins

    @property
    def digest(self) -> str:
        return self._digest

    def admits(self, origin: Any) -> bool:
        """Exact membership only. A non-canonical input is never admitted."""
        return type(origin) is str and origin in self._origins

    def __repr__(self) -> str:
        return f"OriginPolicy(count={len(self._origins)}, digest={self._digest[:12]})"
