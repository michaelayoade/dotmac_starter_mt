"""Private native geometry boundary; inputs contain owned primitives only."""

type Shape = tuple[tuple[float, float, float] | None, list[tuple[float, float]]]

def contains_batch(
    *, latitude: float, longitude: float, shapes: list[Shape]
) -> list[bool]: ...
