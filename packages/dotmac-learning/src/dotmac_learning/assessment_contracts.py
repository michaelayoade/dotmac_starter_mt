"""JSON input shape for the source-preserving Academy grader.

This is a typing contract, not runtime validation. Curriculum and bank rules
own authoring validation. Product adapters authorize requests and supply human
grading decisions.
"""

from __future__ import annotations

from typing import NotRequired, TypedDict

from pydantic import JsonValue


class GradingQuestion(TypedDict):
    ext_id: str
    type: NotRequired[str]
    correct: NotRequired[JsonValue]
    options: NotRequired[JsonValue]
    weight: NotRequired[JsonValue]
