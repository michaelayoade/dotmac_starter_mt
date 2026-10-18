"""Detached item-analysis counts; product adapters render question labels."""

from dataclasses import dataclass

# Source skips None and otherwise uses the JSON identifier without coercion.
# Unhashable identifiers still raise the source TypeError during tallying.
type ItemIdentifier = str | int | float | bool


@dataclass(frozen=True, slots=True)
class ItemAnalysis:
    id: ItemIdentifier
    responses: int
    correct: int
    p_value: float
