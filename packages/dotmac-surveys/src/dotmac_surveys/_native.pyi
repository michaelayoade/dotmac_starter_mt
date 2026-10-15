"""Private bulk validation seam; Unicode remains reversible in Python."""

from collections.abc import Iterable

type Question = tuple[bytes, int, bool, list[bytes]]
type Answer = tuple[bytes, bytes, int]
type Error = tuple[str, list[int]]
type Result = tuple[list[tuple[int, int]], int | None, int | None, Error | None]

def validate_batch(
    *, questions: list[Question], answers: Iterable[Answer]
) -> Result: ...
