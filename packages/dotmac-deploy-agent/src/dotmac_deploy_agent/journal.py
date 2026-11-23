"""Durable agent state: the executed-dispatch journal and per-role accessor state.

Both live in the agent's private, root-created, agent-owned state directory.

* **Executed dispatches (replay).** CP is trusted only for transport, so it can
  re-serve an old Control-signed dispatch. Before any lease, transfer or secret
  is touched the agent refuses a dispatch id it has already taken to the point of
  delivery or run. The record is a file named by the id's digest; it is written
  BEFORE the first irreversible step, so a crash makes the dispatch un-repeatable
  (a new dispatch is needed) rather than repeatable.
* **Accessor state (secret_id rotation).** Per role: the accessor of the id the
  unit is running (`current`), the one just minted (`candidate`), and older ones
  awaiting destruction (`retire`). Corrupt state is a refusal, never "empty":
  silently forgetting a still-valid accessor would leave a valid secret_id behind.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, cast

from .canon import sha256_hex
from .files import ensure_private_dir, write_private
from .refusal import Refusal

_ROLE: Final = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")


class ExecutedDispatches:
    def __init__(self, state_dir: Path) -> None:
        self._dir = state_dir / "executed"
        ensure_private_dir(self._dir)
        self._water = state_dir / "high_water.json"

    def _path(self, dispatch_id: str) -> Path:
        return self._dir / sha256_hex(dispatch_id.encode())

    def seen(self, dispatch_id: str) -> bool:
        return os.path.lexists(self._path(dispatch_id))

    def refuse_replay(self, dispatch_id: str) -> None:
        if self.seen(dispatch_id):
            raise Refusal("dispatch_replayed")

    def record(self, dispatch_id: str, sequence: tuple[int, int] | None = None) -> None:
        write_private(self._path(dispatch_id), b"executed\n", 0o600)
        if sequence is not None and sequence > self.high_water():
            write_private(
                self._water,
                json.dumps(
                    {"execution_sequence": sequence[0], "attempt_no": sequence[1]}
                ).encode(),
                0o600,
            )

    def high_water(self) -> tuple[int, int]:
        """`(execution_sequence, attempt_no)` of the newest executed dispatch."""
        if not os.path.lexists(self._water):
            return (0, 0)
        try:
            raw = json.loads(self._water.read_bytes())
            seq, attempt = raw["execution_sequence"], raw["attempt_no"]
            if type(seq) is not int or type(attempt) is not int:
                raise ValueError
        except (OSError, ValueError, KeyError, TypeError):
            raise Refusal("dispatch_journal_invalid") from None
        return (seq, attempt)

    def refuse_superseded(self, sequence: tuple[int, int]) -> None:
        """Refuse anything at or below the high-water mark. The per-id journal
        cannot order dispatches: an older one that was refused before delivery
        would otherwise still run after a newer one has executed."""
        if sequence <= self.high_water():
            raise Refusal("dispatch_superseded")


@dataclass
class Candidate:
    accessor: str
    stage: str  # "issued" (minted, file not yet confirmed running) | "restarted"
    at: int = 0
    nrestarts: str | None = None
    active_enter: str | None = None


@dataclass
class RoleAccessors:
    current: str | None = None
    candidate: Candidate | None = None
    retire: list[str] = field(default_factory=list)

    def as_json(self) -> bytes:
        return json.dumps(
            {
                "version": 1,
                "current": self.current,
                "candidate": None
                if self.candidate is None
                else {
                    "accessor": self.candidate.accessor,
                    "stage": self.candidate.stage,
                    "at": self.candidate.at,
                    "nrestarts": self.candidate.nrestarts,
                    "active_enter": self.candidate.active_enter,
                },
                "retire": self.retire,
            },
            sort_keys=True,
        ).encode()


class AccessorStore:
    def __init__(self, state_dir: Path, name: str = "accessors") -> None:
        self._dir = state_dir / name
        ensure_private_dir(self._dir)

    def path(self, role: str) -> Path:
        if not _ROLE.fullmatch(role):
            raise Refusal("accessor_state_invalid")
        return self._dir / f"{role}.json"

    def roles(self) -> list[str]:
        return sorted(p.stem for p in self._dir.glob("*.json"))

    def load(self, role: str) -> RoleAccessors:
        path = self.path(role)
        if not os.path.lexists(path):
            return RoleAccessors()
        try:
            raw = json.loads(path.read_bytes())
            if not isinstance(raw, dict) or raw.get("version") != 1:
                raise ValueError
            candidate = raw["candidate"]
            retire = raw["retire"]
            current = raw["current"]
            if (
                not isinstance(retire, list)
                or not all(isinstance(a, str) and a for a in retire)
                or not (current is None or (isinstance(current, str) and current))
            ):
                raise ValueError
            parsed: Candidate | None = None
            if candidate is not None:
                row = cast("dict[str, object]", candidate)
                if (
                    not isinstance(row["accessor"], str)
                    or row["stage"] not in ("issued", "restarted")
                    or type(row["at"]) is not int
                ):
                    raise ValueError
                parsed = Candidate(
                    row["accessor"],
                    str(row["stage"]),
                    int(cast("int", row["at"])),
                    cast("str | None", row["nrestarts"]),
                    cast("str | None", row["active_enter"]),
                )
            return RoleAccessors(current, parsed, list(retire))
        except (OSError, ValueError, KeyError, TypeError):
            raise Refusal("accessor_state_invalid") from None

    def save(self, role: str, state: RoleAccessors) -> None:
        write_private(self.path(role), state.as_json(), 0o600)
