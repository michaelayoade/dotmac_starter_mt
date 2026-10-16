"""Held-key authentication for product queries; no request-path I/O.

Extracted from Integrator's product authenticator. The assembly supplies its
caller declarations and a lookup of already-held material; this module does
not load settings, retrieve secrets or grant health-source authority.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


class ProductCaller(Protocol):
    """The existing caller declaration, without its assembly settings type."""

    @property
    def application(self) -> str: ...

    @property
    def api_key_ref(self) -> str: ...

    @property
    def scopes(self) -> tuple[str, ...]: ...

    @property
    def binding_ids(self) -> tuple[UUID, ...]: ...


@dataclass(frozen=True, slots=True)
class ProductPrincipal:
    application: str
    scopes: frozenset[str]
    binding_ids: frozenset[UUID]


class ProductAuthenticationFailed(RuntimeError):
    """Opaque refusal for every authentication failure mode."""


class ProductAuthenticator:
    """Authenticate against material already held by the assembly."""

    def __init__(
        self,
        callers: Iterable[ProductCaller],
        *,
        secret_lookup: Callable[[str], str | None],
    ) -> None:
        self._callers = tuple(callers)
        self._lookup = secret_lookup

    def authenticate(self, presented: str, *, required_scope: str) -> ProductPrincipal:
        matched: ProductCaller | None = None
        for caller in self._callers:
            held = self._lookup(caller.api_key_ref)
            if held is not None and secrets.compare_digest(presented, held):
                matched = caller
        if matched is None or required_scope not in matched.scopes:
            raise ProductAuthenticationFailed("product authentication failed")
        return ProductPrincipal(
            matched.application,
            frozenset(matched.scopes),
            frozenset(matched.binding_ids),
        )
