def sum_quantized_columns(
    columns: list[list[str]],
    context: tuple[int, int, int, int, int],
    traps: int,
) -> tuple[list[str], int, tuple[int, int, int] | None]:
    """Return results, accumulated signals and first (signals, column, row).

    A row equal to column length denotes final quantization. Signal bits follow
    CPython priority (InvalidOperation through Clamped); bit 9 is allocation
    failure. Each addition respects context; quantization uses ROUND_HALF_UP.
    """
    ...
