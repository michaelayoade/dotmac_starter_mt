"""M11 legacy env file bridge, agent side (DESIGN-rev3 s2.8 L1, L2, L5, L6, L7).

Existing apps only. The agent never opens the relocated source file: systemd
copies it into `$CREDENTIALS_DIRECTORY/legacy` (`LoadCredential=legacy:<path>`,
installed by the governed relocation op). The agent only `lstat`s the source to
check the L6 rules, reads the credential, checks it against the policy fixed by
Michael (sha, names, expiry) and copies it to a tmpfs `legacy.env`, 0400, that
the bindings pass as an extra env file. The copy is deleted after the run.
"""

from __future__ import annotations

import re
import stat
from collections.abc import Collection, Mapping
from datetime import datetime
from pathlib import Path
from typing import Final

from .canon import digest
from .files import remove_file, require_private_dir, write_private
from .materials import LegacyPolicy
from .params import APP_IMAGE
from .refusal import Refusal
from .systemd_creds import read_credential

LEGACY_DIR: Final = Path("/etc/dotmac/legacy")
CREDENTIAL_NAME: Final = "legacy"
LEGACY_FILE: Final = "legacy.env"
_LINE: Final = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)")


def check_source(
    source: Path,
    *,
    deploy_dir: Path,
    allowed_dir: Path = LEGACY_DIR,
    required_uid: int = 0,
) -> None:
    """L6 path rules on the relocated original. `lstat` only; never read here."""
    if (
        not source.is_absolute()
        or source.parent != allowed_dir
        or source.name == ".env"
        or not source.name.endswith(".env")
        or source == deploy_dir / ".env"
    ):
        raise Refusal("legacy_path_invalid")
    try:
        info = source.lstat()
        parent = allowed_dir.lstat()
    except OSError:
        raise Refusal("legacy_source_unsafe") from None
    if (
        not stat.S_ISREG(info.st_mode)  # not a symlink, not a directory
        or info.st_uid != required_uid
        or stat.S_IMODE(info.st_mode) != 0o600
        or not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != required_uid
        or parent.st_mode & 0o022
    ):
        raise Refusal("legacy_source_unsafe")


def parse_names(content: bytes) -> tuple[str, ...]:
    """Names only. A malformed or duplicated line refuses; values are never returned."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise Refusal("legacy_malformed") from None
    names: list[str] = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = _LINE.fullmatch(line)
        if match is None:
            raise Refusal("legacy_malformed")
        names.append(match[1])
    if len(names) != len(set(names)):
        raise Refusal("legacy_malformed")
    return tuple(sorted(names))


def legacy_copy_path(deploy_run_dir: Path) -> Path:
    return deploy_run_dir / LEGACY_FILE


def prepare_legacy(
    policy: LegacyPolicy,
    *,
    source: Path | None,
    deploy_dir: Path,
    deploy_run_dir: Path,
    environ: Mapping[str, str],
    cp_now: datetime,
    param_materials: Collection[str],
    binding_names: Collection[str],
    allowed_dir: Path = LEGACY_DIR,
    required_uid: int = 0,
) -> Path:
    """Verify every L-rule, then write the tmpfs copy. Returns the copy's path."""
    if cp_now >= policy.expires_at:  # L5, on CP time and not the host clock
        raise Refusal("legacy_expired")
    if source is None:
        raise Refusal("legacy_path_invalid")
    check_source(
        source,
        deploy_dir=deploy_dir,
        allowed_dir=allowed_dir,
        required_uid=required_uid,
    )
    content = read_credential(
        environ, CREDENTIAL_NAME, code="legacy_credential_missing"
    )
    if (
        digest(content) != policy.sha256
    ):  # L1: the sha Michael fixed, never an observation
        raise Refusal("legacy_sha_mismatch")
    names = parse_names(content)
    if names != policy.names:  # L2: names equal the policy exactly
        raise Refusal("legacy_names_mismatch")
    taken = set(param_materials) | set(binding_names) | {APP_IMAGE}
    if taken & set(names):  # F1: a later file would silently override a typed param
        raise Refusal("legacy_name_collision")
    require_private_dir(deploy_run_dir)
    target = legacy_copy_path(deploy_run_dir)
    write_private(target, content, 0o400)
    if digest(target.read_bytes()) != policy.sha256:
        remove_file(target)
        raise Refusal("legacy_sha_mismatch")
    return target


def remove_legacy_copy(deploy_run_dir: Path) -> None:
    remove_file(legacy_copy_path(deploy_run_dir))
