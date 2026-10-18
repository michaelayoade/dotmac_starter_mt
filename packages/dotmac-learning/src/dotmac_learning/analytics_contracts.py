"""Detached item-analysis counts; product adapters render question labels."""

from dataclasses import dataclass
from typing import TypeAlias

# Source skips None and otherwise uses the JSON identifier without coercion.
# Unhashable identifiers still raise the source TypeError during tallying.
ItemIdentifier: TypeAlias = str | int | float | bool  # noqa: UP040 - Python 3.11 floor


@dataclass(frozen=True, slots=True)
class ItemAnalysis:
    id: ItemIdentifier
    responses: int
    correct: int
    p_value: float
