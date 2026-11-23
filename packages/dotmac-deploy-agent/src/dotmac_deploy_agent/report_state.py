"""Small durable memory that keeps the agent from repeating itself every poll.

`refusals_reported`: one refused observation per (dispatch, reason). `reconcile_posted`:
one reconcile observation per (expired dispatch, digest of the observed containers);
it is posted again only when that digest changes. Neither moves the lease standing
(only CP's acquire with Control's marker does). Bounded: the oldest keys are dropped.
"""

from __future__ import annotations

import json
from pathlib import Path

from .canon import canonical_json
from .files import ensure_private_dir, read_regular, write_private
from .refusal import Refusal


class SeenStore:
    def __init__(self, directory: Path, name: str, *, limit: int = 200) -> None:
        ensure_private_dir(directory)
        self._path = directory / f"{name}.json"
        self._limit = limit

    def _load(self) -> dict[str, list[str]]:
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(read_regular(self._path, limit=1 << 20))
        except (Refusal, ValueError):
            return {}  # unreadable memory only costs a repeat, never a skip
        if not isinstance(raw, dict):
            return {}
        return {
            k: [v for v in vs if isinstance(v, str)]
            for k, vs in raw.items()
            if isinstance(k, str) and isinstance(vs, list)
        }

    def seen(self, key: str, value: str) -> bool:
        return value in self._load().get(key, [])

    def remember(self, key: str, value: str, *, replace: bool = False) -> None:
        data = self._load()
        values = [] if replace else data.pop(key, [])
        data.pop(key, None)
        if value not in values:
            values.append(value)
        data[key] = values  # newest key last
        while len(data) > self._limit:
            data.pop(next(iter(data)))
        write_private(self._path, canonical_json(data), 0o600)
