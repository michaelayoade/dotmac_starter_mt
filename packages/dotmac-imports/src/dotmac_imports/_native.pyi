def normalize_rows(
    column_count: int,
    records: list[tuple[int, list[bytes]]],
    remaining: int,
    projection: list[int],
) -> list[list[bytes]]: ...
