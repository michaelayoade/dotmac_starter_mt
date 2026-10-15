def depreciation_charges(
    values: list[str],
    periods: str,
    method: str,
    minor_units: int,
    context: tuple[int, int, int, int, int],
    traps: int,
) -> tuple[list[str], int, tuple[int, int] | None, bool]: ...
