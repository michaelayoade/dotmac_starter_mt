"""Private bulk validation seam; Unicode remains reversible in Python."""

from collections.abc import Callable, Iterable

type Question = tuple[bytes, int, bool, list[bytes]]
type Answer = tuple[bytes, bytes, int]
type Error = tuple[str, list[int]]
type Result = tuple[list[tuple[int, int]], int | None, int | None, Error | None]

def validate_batch(
    *, questions: list[Question], answers: Iterable[Answer]
) -> Result: ...

type ContextSpec = tuple[int, int, int, int, int]
type ArithmeticFailure = tuple[int, int] | None

def calculate_metrics(
    *,
    ratings: list[str],
    nps_predicates: Iterable[tuple[bool, bool]],
    context: ContextSpec,
    traps: int,
    replay_rating: Callable[[int, ArithmeticFailure], None],
    capture_nps_context: Callable[[], tuple[ContextSpec, int]],
) -> tuple[str | None, str | None, int, ArithmeticFailure]: ...
