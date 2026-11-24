"""Observation documents and the durable outbox.

An observation carries codes, digests, role and binding NAMES and exit codes.
It never carries a value, a path to a secret, a log or an exception message.
It is authenticated by the PoP-signed request that carries it (wire.py).
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from .canon import canonical_json
from .files import ensure_private_dir, remove_file, write_private
from .wire import utc_stamp

SCHEMA: Final = "dotmac.deploy_observation/1"
_SAFE_ID: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")


def safe_id(value: object) -> str:
    return value if isinstance(value, str) and _SAFE_ID.fullmatch(value) else "unknown"


def build_observation(
    *,
    kind: str,
    dispatch_id: str,
    target_ref: str,
    outcome: str,
    reason: str | None,
    version: str,
    details: dict[str, object] | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    return {
        "schema": SCHEMA,
        "kind": kind,
        "dispatch_id": safe_id(dispatch_id),
        "target_ref": target_ref,
        "outcome": outcome,
        "reason": reason,
        "details": details or {},
        "agent_version": version,
        "observed_at": utc_stamp(now or datetime.now(UTC)),
    }


class Outbox:
    """A sealed observation stays on disk until CP acknowledges it, so a crash
    after the run never loses the signed account of what happened."""

    def __init__(self, state_dir: Path) -> None:
        self._dir = state_dir / "outbox"
        ensure_private_dir(state_dir)
        ensure_private_dir(self._dir)

    def seal(self, observation: dict[str, object]) -> Path:
        name = f"{safe_id(observation['dispatch_id'])}.{observation['kind']}.json"
        path = self._dir / name.replace(":", "_")
        write_private(path, canonical_json(observation), 0o600)
        return path

    def pending(self) -> list[Path]:
        return sorted(p for p in self._dir.glob("*.json") if not p.name.startswith("."))

    def acknowledge(self, path: Path) -> None:
        remove_file(path)

    def dead_letter(self, path: Path) -> None:
        """Move a refused or unreadable observation aside, keeping a count."""
        dead = self._dir.parent / "outbox-dead"
        ensure_private_dir(dead)
        os.replace(path, dead / path.name)

    def dead_count(self) -> int:
        dead = self._dir.parent / "outbox-dead"
        return len(list(dead.glob("*.json"))) if dead.exists() else 0

    @staticmethod
    def read(path: Path) -> bytes:
        return path.read_bytes()


def inflight_path(state_dir: Path) -> Path:
    return state_dir / "inflight.json"


def mark_inflight(state_dir: Path, dispatch_id: str) -> None:
    write_private(
        inflight_path(state_dir), canonical_json({"dispatch_id": dispatch_id}), 0o600
    )


def clear_inflight(state_dir: Path) -> None:
    remove_file(inflight_path(state_dir))


def read_inflight(state_dir: Path) -> str | None:
    path = inflight_path(state_dir)
    if not os.path.lexists(path):
        return None
    try:
        return safe_id(json.loads(path.read_bytes()).get("dispatch_id"))
    except (ValueError, AttributeError):
        return "unknown"
