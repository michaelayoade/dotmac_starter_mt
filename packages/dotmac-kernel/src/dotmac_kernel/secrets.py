"""Secret material a deployment holds in memory, loaded once at startup.

## The rule this module exists to serve

**A secret is held, never dereferenced.** Nothing in the kernel resolves a
secret from a network store while handling a request. A value that cannot be
held is not a setting: if it must live in a secret store, the product reads it
and installs it here, or seeds it as a real setting — it never enters
`domain_settings` as a reference the kernel dereferences on the read path.

That is a PROPERTY of the kernel rather than a policy it argues for, and
`tests/architecture/test_secrets_are_held.py` enforces it — settings resolution
is proven to complete with the network unavailable.

Why it is worth being absolute about:

* Settings resolution is a per-request read path. A store on it turns that
  store's outage into a total outage of everything that reads a setting, which
  is nearly every request.
* A store that answers slowly is worse than one that fails: latency lands on
  every request rather than one, and the failure looks like the application
  being slow rather than the store being down.
* Dereferencing means the value's provenance is elsewhere. `resolve_with_source`
  can say a row won, but not what the row meant, so the settings screen becomes
  a liar about the value actually in effect.

Loading at startup keeps every benefit of a secret store — key material stays
out of the database, rotation stays central, custody is auditable — and pays
none of that cost, because a store outage an hour after boot is invisible to a
process that already has what it needed.

## What this module is, and is not

It is a place to PUT secret material that a product resolved from somewhere the
kernel knows nothing about. It is not a client, a resolver, or a store: it
performs no I/O of its own and imports nothing that could. The product supplies
a `SecretSource`; the dependency on OpenBao, a cloud secret manager, or a
mounted file stays in the product.

`dotmac_kernel.settings_crypto.KeyProvider` is the same shape specialised to
settings encryption keys, which carry structure (an id, a rotation status, an
optional owning tenant) that plain named material does not. Deliberately two
protocols rather than one blurred one; identical semantics, so learning either
teaches the other.

## Semantics, all of them consequences of "held, not fetched"

* **Loaded once**, by `install_secret_source`. `get_secret` is a dict lookup.
* **Rotation is explicit** — `refresh_secrets()`, never a TTL. A rotation should
  take effect when an operator says so, not when a timer happens to fire.
* **A failed refresh keeps the working set**, so a store briefly unreachable
  during a rotation attempt leaves a working process working.
* **A failing source raises at install.** Installing one is a statement that
  these secrets are expected; starting without them would mean discovering it at
  the first request that needed one. There is no degraded-start option, on
  purpose: that is the flag switched on during an incident and never switched
  back.
* **A source raises when its store is unreachable** and never returns an empty
  mapping for it — empty is indistinguishable from "nothing is configured".
* **Values are never logged, repr'd, or put in an exception message.** Only
  names. A store client's own error can quote the payload it choked on, so even
  a wrapped exception is reported by TYPE alone.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


class SecretError(RuntimeError):
    """Base for secret-source failures."""


class SecretSourceError(SecretError):
    """An installed `SecretSource` could not supply secrets."""


class MissingSecretError(SecretError, KeyError):
    """A required secret is not among those loaded."""


@runtime_checkable
class SecretSource(Protocol):
    """Where a deployment's secret material comes from.

    One method, because a source has one job. Implementations live in the
    PRODUCT — that is what keeps the kernel free of any secret-store client.

    Called once by `install_secret_source`, and again only on an explicit
    `refresh_secrets()`, so it may be slow and may do I/O.
    """

    def load(self) -> Mapping[str, str]:
        """Every secret this deployment should hold, by name.

        Raise to signal the store could not be reached — do not return an empty
        mapping for that, which is indistinguishable from "nothing is
        configured" and would turn an outage into a silent misconfiguration.
        """
        ...


# The installed source and what it produced, held together so "a source is
# installed" and "we have its secrets" cannot disagree.
_source: SecretSource | None = None
_held: dict[str, str] = {}


def _load_from(source: SecretSource) -> dict[str, str]:
    try:
        loaded = source.load()
    except SecretError:
        raise
    except Exception as exc:
        # Only the TYPE: a store client's error message can quote the payload
        # it failed on, and that payload is the secret.
        raise SecretSourceError(
            f"{type(source).__name__} could not load secrets: {type(exc).__name__}"
        ) from exc
    if not isinstance(loaded, Mapping):
        raise SecretSourceError(
            f"{type(source).__name__}.load() must return a mapping of name to "
            f"value, got {type(loaded).__name__}"
        )
    held: dict[str, str] = {}
    for name, value in loaded.items():
        if not isinstance(name, str) or not name:
            raise SecretSourceError(
                f"{type(source).__name__}.load() returned a non-string name"
            )
        if not isinstance(value, str):
            # Named, because the name is safe to print and the value is not.
            raise SecretSourceError(
                f"{type(source).__name__}.load() returned a non-string value "
                f"for {name!r}"
            )
        held[name] = value
    return held


def install_secret_source(source: SecretSource) -> tuple[str, ...]:
    """Install `source` and load it NOW. Returns the names loaded, never values.

    Loading eagerly is the point: the secrets are in memory from this moment, so
    a later outage of whatever the source reads cannot reach request handling.
    It also puts any failure at a place whose traceback names the product's
    source, rather than at some unrelated read hours later.
    """
    global _source, _held
    loaded = _load_from(source)
    _source, _held = source, loaded
    logger.info(
        "Installed secret source %s holding %d secret(s): %s",
        type(source).__name__,
        len(loaded),
        ", ".join(sorted(loaded)) or "none",
    )
    return tuple(sorted(loaded))


def refresh_secrets() -> tuple[str, ...]:
    """Re-read the installed source — the rotation operation.

    The previously held set is kept if the reload fails.
    """
    global _held
    if _source is None:
        raise SecretSourceError("no secret source is installed — nothing to refresh")
    _held = _load_from(_source)
    logger.info("Refreshed %d secret(s): %s", len(_held), ", ".join(sorted(_held)))
    return tuple(sorted(_held))


def clear_secret_source() -> None:
    """Uninstall the source and drop what it held."""
    global _source, _held
    _source, _held = None, {}


def get_secret(name: str) -> str | None:
    """The held value, or None. A dict lookup — safe on a request path."""
    return _held.get(name)


def require_secret(name: str) -> str:
    """The held value, or raise naming what is missing and what is available.

    Both are names, never values, and listing what IS held is what makes a
    typo diagnosable without an operator going looking in the store.
    """
    try:
        return _held[name]
    except KeyError:
        available = ", ".join(sorted(_held)) or "none"
        raise MissingSecretError(
            f"secret {name!r} is not held (loaded: {available}). Install a "
            "`SecretSource` that supplies it — the kernel never fetches a "
            "secret while handling a request"
        ) from None


def secret_names() -> tuple[str, ...]:
    """Every held name. Names only — there is no accessor that dumps values."""
    return tuple(sorted(_held))


def secret_source_installed() -> bool:
    return _source is not None


__all__ = [
    "MissingSecretError",
    "SecretError",
    "SecretSource",
    "SecretSourceError",
    "clear_secret_source",
    "get_secret",
    "install_secret_source",
    "refresh_secrets",
    "require_secret",
    "secret_names",
    "secret_source_installed",
]
