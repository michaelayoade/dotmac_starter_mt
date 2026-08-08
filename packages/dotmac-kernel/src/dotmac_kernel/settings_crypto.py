"""At-rest encryption for secret domain settings.

`DomainSetting.value_text` is a plain `Text` column, so without this every
secret the settings table holds — provider keys, SMTP passwords, webhook
signing secrets — sits in the database in plaintext and `is_secret` is only a
display hint. The settings admin API masks such a value on the way out, which
protects the screen and nothing else: a database dump, a replica, or a backup
still carries the plaintext.

Encryption is applied at the THREE call sites in
`dotmac_kernel.settings_resolver` — one reader (`_extract_raw`) and two writers
(`upsert_by_key`, `ensure_by_key`). All three are in one module, so a mapper
listener would hide the behaviour without removing any real risk of a missed
path.

**Fail closed on write, tolerant on read.** Writing a secret with no usable key
RAISES `SettingsEncryptionError`: an operator storing a credential gets a clean
error at that moment rather than a plaintext row and a log line nobody reads.
Reading tolerates plaintext, so rows written before a key existed keep resolving
and the next write converts them.

## Keyring and rotation

The stored form is `enc:<key_id>:<token>`. **The key id is in the ciphertext
because a Fernet token does not carry one**, and without it rotation is not an
operation: the new key cannot read old values, and this module's read path
degrades an unreadable value to the spec default — which would silently
substitute a default for a credential.

Keys are a keyring with the same rotation semantics as
`dotmac_kernel.licensing`'s verification keys, deliberately, so there is one
mental model for rotation in this codebase:

* `active` — decrypts, and is what new writes use. **Exactly one**, or writing
  would have to guess which key to encrypt with.
* `retired` — still decrypts (the rotation overlap, for rows not yet rewritten)
  but encrypts nothing new.
* `revoked` — decrypts nothing. For key material believed compromised: values
  under it become unreadable ON PURPOSE, which is the point.

Rotation is therefore: add a new `active` key and mark the old one `retired`,
run `reencrypt_secrets`, then drop the retired key. `reencrypt_secrets` is
resumable and idempotent — it rewrites only rows not already on the active key.

A value written by `dotmac_erp`'s scheme has no key id (`enc:<token>`). Those
are tried against every non-revoked key, so a deployment adopting this reads
rows its own code wrote, and the next write moves them onto the keyring.

## Where key material comes from

The environment (`SETTINGS_ENCRYPTION_KEYS`, or `SETTINGS_ENCRYPTION_KEY` /
`SETTINGS_ENCRYPTION_KEY_FILE` for the single-key case) — never a network fetch.
Settings resolution is a per-request read path, and making it depend on a secret
store being reachable would turn that store's outage into a total outage. Every
secret manager (OpenBao Agent templates, Kubernetes mounted secrets, cloud
secret CSI drivers, Docker secrets) can render to an environment variable or a
file, so env-or-file is the one contract that excludes no deployment profile.
Choosing the store is a deployment decision; the kernel does not know which.

Dependency: Fernet needs the `cryptography` package, installed by the
`settings-crypto` extra. The import is LAZY, exactly as
`dotmac_kernel.licensing` imports Ed25519, so a deployment that declares no
`is_secret` spec — the reference assembly declares none — carries no compiled
crypto stack for a path it never reaches. Absence is not silent: writing a
secret without it raises `SettingsEncryptionError` naming the extra.
"""

from __future__ import annotations

import enum
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # the runtime import is lazy — see the module docstring
    from cryptography.fernet import Fernet
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Marks a stored value as ciphertext. Shared with `dotmac_erp`'s scheme.
ENCRYPTED_PREFIX = "enc:"

KEYRING_ENV_VAR = "SETTINGS_ENCRYPTION_KEYS"
KEY_ENV_VAR = "SETTINGS_ENCRYPTION_KEY"
KEY_FILE_ENV_VAR = "SETTINGS_ENCRYPTION_KEY_FILE"

# `key_id` is a ciphertext field delimited by ":", so it may not contain one.
# Restricted further to characters that survive a log line, an env var and a
# migration script unambiguously.
_KEY_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# The id given to a key supplied through the single-key variables. Stable,
# because it is written into every value those deployments encrypt.
DEFAULT_KEY_ID = "default"


class KeyStatus(enum.StrEnum):
    """Rotation state of an encryption key — see the module docstring."""

    ACTIVE = "active"
    RETIRED = "retired"
    REVOKED = "revoked"


class SettingsEncryptionError(RuntimeError):
    """A secret setting cannot be encrypted, so it must not be written."""


class KeyringError(ValueError):
    """The configured keyring is not usable as configured."""


@dataclass(frozen=True)
class EncryptionKey:
    key_id: str
    material: str
    status: KeyStatus = KeyStatus.ACTIVE


@dataclass(frozen=True)
class Keyring:
    """Every configured key, by id, with at most one `active`.

    Construction IS validation, the same posture as the declaration registries:
    a duplicate id, a malformed id, or two active keys raise here rather than at
    the write that trips over them.
    """

    keys: tuple[EncryptionKey, ...]

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for key in self.keys:
            if not _KEY_ID.fullmatch(key.key_id):
                raise KeyringError(
                    f"key_id {key.key_id!r} must match {_KEY_ID.pattern} — it is "
                    "written into every value this key encrypts"
                )
            if key.key_id in seen:
                raise KeyringError(f"duplicate key_id in keyring: {key.key_id!r}")
            seen.add(key.key_id)
        active = [key for key in self.keys if key.status is KeyStatus.ACTIVE]
        if len(active) > 1:
            raise KeyringError(
                "keyring has more than one active key "
                f"({sorted(key.key_id for key in active)}) — exactly one key "
                "encrypts new values; mark the others `retired`"
            )

    @property
    def active(self) -> EncryptionKey | None:
        for key in self.keys:
            if key.status is KeyStatus.ACTIVE:
                return key
        return None

    def get(self, key_id: str) -> EncryptionKey | None:
        for key in self.keys:
            if key.key_id == key_id:
                return key
        return None

    def decrypting(self) -> tuple[EncryptionKey, ...]:
        """Keys permitted to decrypt: everything not revoked."""
        return tuple(key for key in self.keys if key.status is not KeyStatus.REVOKED)


def _keyring_from_env() -> Keyring:
    """Build the keyring from the environment. Empty when nothing is configured.

    Read on every call rather than captured at import, so a rotated variable and
    a test that sets one both take effect. `_fernet` memoises the expensive part.
    """
    raw = os.environ.get(KEYRING_ENV_VAR, "").strip()
    if raw:
        try:
            entries = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise KeyringError(f"{KEYRING_ENV_VAR} is not valid JSON") from exc
        if not isinstance(entries, list):
            raise KeyringError(f"{KEYRING_ENV_VAR} must be a JSON list")
        keys: list[EncryptionKey] = []
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise KeyringError(f"{KEYRING_ENV_VAR}[{index}] must be an object")
            missing = {"key_id", "key"} - entry.keys()
            if missing:
                raise KeyringError(
                    f"{KEYRING_ENV_VAR}[{index}] missing {sorted(missing)}"
                )
            try:
                status = KeyStatus(str(entry.get("status", "active")))
            except ValueError as exc:
                raise KeyringError(
                    f"{KEYRING_ENV_VAR}[{index}].status must be one of "
                    f"{[member.value for member in KeyStatus]}"
                ) from exc
            keys.append(
                EncryptionKey(
                    key_id=str(entry["key_id"]),
                    material=str(entry["key"]),
                    status=status,
                )
            )
        return Keyring(tuple(keys))

    single = os.environ.get(KEY_ENV_VAR, "").strip()
    if not single:
        path = os.environ.get(KEY_FILE_ENV_VAR, "").strip()
        if path:
            try:
                single = Path(path).read_text(encoding="utf-8").strip()
            except OSError as exc:
                logger.error("Cannot read %s at %s: %s", KEY_FILE_ENV_VAR, path, exc)
                single = ""
    if not single:
        return Keyring(())
    return Keyring((EncryptionKey(key_id=DEFAULT_KEY_ID, material=single),))


# Constructing a Fernet derives and validates key material; memoised on the key
# STRING so rotation is picked up while a hot read path is not re-deriving per
# call. Bounded by how many distinct keys a process sees — one or two in
# practice, more only across tests.
_FERNET_CACHE: dict[str, Fernet] = {}


def _fernet(key: EncryptionKey) -> Fernet | None:
    cached = _FERNET_CACHE.get(key.material)
    if cached is not None:
        return cached
    try:
        from cryptography.fernet import Fernet as _Fernet
    except ImportError:
        logger.error(
            "A settings encryption key is configured but `cryptography` is not "
            "installed — install the `settings-crypto` extra"
        )
        return None
    try:
        fernet = _Fernet(key.material.encode())
    except (ValueError, TypeError):
        logger.error(
            "Settings encryption key %r is not a valid Fernet key (expect 32 "
            "url-safe base64-encoded bytes, as `Fernet.generate_key()` produces)",
            key.key_id,
        )
        return None
    _FERNET_CACHE[key.material] = fernet
    return fernet


def keyring() -> Keyring:
    """The configured keyring, or an empty one. Raises `KeyringError` on a
    keyring that is configured but malformed — a typo must not read as
    "encryption is off"."""
    return _keyring_from_env()


def encryption_configured() -> bool:
    """True when a key exists that can encrypt a new value."""
    try:
        active = keyring().active
    except KeyringError:
        return False
    return active is not None and _fernet(active) is not None


def is_encrypted(value: str | None) -> bool:
    return bool(value) and str(value).startswith(ENCRYPTED_PREFIX)


def _split(value: str) -> tuple[str | None, str]:
    """`enc:<key_id>:<token>` -> (key_id, token); `enc:<token>` -> (None, token).

    A Fernet token is url-safe base64 and never contains ":", so the presence of
    one is an unambiguous discriminator between this format and the id-less form
    `dotmac_erp` writes.
    """
    body = value[len(ENCRYPTED_PREFIX) :]
    key_id, separator, token = body.partition(":")
    return (key_id, token) if separator else (None, body)


def encrypted_key_id(value: str | None) -> str | None:
    """Which key a stored value is under, or None (plaintext, or id-less)."""
    if not is_encrypted(value):
        return None
    return _split(str(value))[0]


def encrypt_value(value: str) -> str:
    """Encrypt a secret setting's value for storage under the active key.

    Idempotent only for a value ALREADY under the active key: a value under a
    retired key is re-encrypted onto the active one, which is what makes
    `reencrypt_secrets` a rewrite rather than a no-op.

    Raises `SettingsEncryptionError` when no usable active key is configured —
    see the module docstring for why this fails rather than storing plaintext.
    """
    try:
        active = keyring().active
    except KeyringError as exc:
        raise SettingsEncryptionError(f"cannot store a secret setting: {exc}") from exc
    if active is None:
        raise SettingsEncryptionError(
            "cannot store a secret setting: no active settings encryption key. "
            f"Set {KEY_ENV_VAR} (or {KEY_FILE_ENV_VAR}, or {KEYRING_ENV_VAR} "
            "with exactly one `active` entry) to a key from "
            "`Fernet.generate_key()`, and install the `settings-crypto` extra"
        )
    if is_encrypted(value) and encrypted_key_id(value) == active.key_id:
        return value
    if is_encrypted(value):
        # Under a retired key: decrypt first so the rewrite is a real rotation
        # and not a nested encryption nobody can unwrap.
        plaintext = decrypt_value(value)
        if plaintext is None:
            raise SettingsEncryptionError(
                "cannot re-encrypt a value whose key is retired or revoked and "
                "no longer configured"
            )
        value = plaintext
    fernet = _fernet(active)
    if fernet is None:
        raise SettingsEncryptionError(
            f"cannot store a secret setting: key {active.key_id!r} is unusable "
            "(invalid material, or `cryptography` is not installed — install "
            "the `settings-crypto` extra)"
        )
    token = fernet.encrypt(value.encode()).decode()
    return f"{ENCRYPTED_PREFIX}{active.key_id}:{token}"


def decrypt_value(value: str | None) -> str | None:
    """Decrypt a stored value. Plaintext passes through; undecryptable is None.

    `None` rather than an exception, because the caller is the read path:
    `resolve_with_source` degrades a value it cannot read to the spec default,
    the same safe behaviour a corrupted plaintext value already gets. A rotated
    or missing key must not take down every request that touches settings.
    """
    if not value or not is_encrypted(value):
        # Written before a key existed, or on a deployment with none. Readable
        # as-is; the next write through `upsert_by_key` converts it.
        return value
    try:
        ring = keyring()
    except KeyringError as exc:
        logger.error("Cannot decrypt a settings value: %s", exc)
        return None

    key_id, token = _split(str(value))
    if key_id is not None:
        key = ring.get(key_id)
        if key is None:
            logger.error(
                "A settings value is encrypted under key %r, which is not in "
                "the configured keyring — add it, or the value is unreadable",
                key_id,
            )
            return None
        if key.status is KeyStatus.REVOKED:
            logger.error(
                "A settings value is encrypted under REVOKED key %r; refusing "
                "to decrypt it",
                key_id,
            )
            return None
        candidates: tuple[EncryptionKey, ...] = (key,)
    else:
        # `dotmac_erp`'s id-less form: try every key permitted to decrypt.
        candidates = ring.decrypting()
        if not candidates:
            logger.error(
                "A settings value is encrypted but no key is configured — set %s",
                KEY_ENV_VAR,
            )
            return None

    from cryptography.fernet import InvalidToken

    for candidate in candidates:
        fernet = _fernet(candidate)
        if fernet is None:
            continue
        try:
            return fernet.decrypt(token.encode()).decode()
        except InvalidToken:
            continue
    logger.error(
        "Could not decrypt a settings value: wrong key, or it was encrypted "
        "under a key that has since been rotated out of the keyring"
    )
    return None


def reencrypt_secrets(db: Session) -> tuple[int, int]:
    """Rewrite every secret row onto the ACTIVE key. Returns (rewritten, failed).

    The second half of a rotation: add a new `active` key, mark the old one
    `retired`, run this, then drop the retired key. Idempotent and resumable —
    rows already on the active key are skipped, so an interrupted run is
    continued by running it again.

    Failures are counted and logged, never raised: one row encrypted under a key
    nobody has any more must not stop the rest of the rotation. A non-zero
    second element means the rotation is INCOMPLETE and the retired key must
    stay configured.

    Callers hold the transaction. This flushes but never commits — the same rule
    every other writer in `settings_resolver` follows.
    """
    from sqlalchemy import select

    from dotmac_kernel.settings_models import DomainSetting

    active = keyring().active
    if active is None:
        raise SettingsEncryptionError(
            "cannot re-encrypt: no active settings encryption key configured"
        )

    rewritten = 0
    failed = 0
    rows = db.scalars(
        select(DomainSetting).where(DomainSetting.is_secret == True)  # noqa: E712
    ).all()
    for row in rows:
        stored = row.value_text
        if not stored or encrypted_key_id(stored) == active.key_id:
            continue
        try:
            row.value_text = encrypt_value(stored)
        except SettingsEncryptionError as exc:
            failed += 1
            logger.error("Could not re-encrypt %s/%s: %s", row.domain, row.key, exc)
            continue
        rewritten += 1
    db.flush()
    logger.info(
        "Re-encrypted %d secret setting(s) onto key %r; %d failed",
        rewritten,
        active.key_id,
        failed,
    )
    return rewritten, failed


__all__ = [
    "DEFAULT_KEY_ID",
    "ENCRYPTED_PREFIX",
    "KEYRING_ENV_VAR",
    "KEY_ENV_VAR",
    "KEY_FILE_ENV_VAR",
    "EncryptionKey",
    "KeyStatus",
    "Keyring",
    "KeyringError",
    "SettingsEncryptionError",
    "decrypt_value",
    "encrypt_value",
    "encrypted_key_id",
    "encryption_configured",
    "is_encrypted",
    "keyring",
    "reencrypt_secrets",
]
