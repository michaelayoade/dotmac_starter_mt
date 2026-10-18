"""Calendar domain events with semantic targets; no rendered labels or URLs."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from .delivery_contracts import ClassSessionFact


@dataclass(frozen=True, slots=True)
class AgendaTarget:
    kind: Literal["course", "activity", "session"]
    id: UUID
    course_slug: str | None = None


@dataclass(frozen=True, slots=True)
class AgendaItem:
    when: datetime
    kind: Literal["opens", "closes", "due", "session"]
    title: str
    course_title: str
    target: AgendaTarget
    session: ClassSessionFact | None = None
    joinable: bool = False
