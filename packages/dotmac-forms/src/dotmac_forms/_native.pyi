def review_answers(
    fields: list[
        tuple[str, str, bool, str, bytes, list[bytes], list[tuple[bytes, bytes]]]
    ],
) -> list[tuple[str, bytes, list[bytes], bytes | None, bytes | None]]: ...
