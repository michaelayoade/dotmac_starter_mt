"""systemd credentials: how secret-zero, the PoP key and the legacy file are read."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from .files import read_regular
from .refusal import Refusal

_NAME: Final = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
LIMIT: Final = 1024 * 1024


def credentials_dir(environ: Mapping[str, str]) -> Path:
    value = environ.get("CREDENTIALS_DIRECTORY")
    if not value or not os.path.isabs(value):
        raise Refusal("legacy_credential_missing")
    return Path(value)


def read_credential(environ: Mapping[str, str], name: str, *, code: str) -> bytes:
    """Read `$CREDENTIALS_DIRECTORY/<name>`. `code` is the refusal for 'not there'."""
    if not _NAME.fullmatch(name):
        raise Refusal("config_invalid")
    try:
        directory = credentials_dir(environ)
    except Refusal:
        raise Refusal(code) from None
    path = directory / name
    if not os.path.lexists(path):
        raise Refusal(code)
    return read_regular(path, limit=LIMIT)
