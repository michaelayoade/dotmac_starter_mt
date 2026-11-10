#!/usr/bin/env python3
"""Phase B kit converter: the A10 classified rebuild CSV -> the drafter input.

Reads ``rebuild-classified.csv`` (the output of the read-only A10 analysis,
``analyze2.py``, run on the SAME snapshot as the census) and writes
``billing.opening.a10-account-rebuild.v1``: per account ``rebuild_net`` (the
exact decimal of ``deposit + p_elig + xalloc + cn + a_other - c_elig - a_renew -
l_docless``, credit-positive, ``analyze2.py:13``), its opening class and its
post-cutover cause. NGN only.

Read-only: it opens the CSV for reading, writes one new file (refusing to
overwrite), changes no Sub row, repairs nothing and backfills nothing. It never
rounds: an amount that is not exact at two decimals is refused.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final

from dotmac_billing.opening_admissibility import (
    A10_REBUILD_SCHEMA,
    AdmissibilityError,
    parse_a10_rebuild,
)

_COLUMNS: Final = ("account_id", "rebuild", "opening_class", "post_cause")


class ConversionError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def convert(rows: list[dict[str, str]]) -> dict[str, object]:
    """Pure conversion of classified-CSV rows; the result passes the closed parser."""
    accounts: list[dict[str, str]] = []
    for row in rows:
        if any(name not in row for name in _COLUMNS):
            raise ConversionError("missing_column")
        try:
            net = Decimal(row["rebuild"])
        except InvalidOperation:
            raise ConversionError("inexact_amount") from None
        if not net.is_finite() or net != net.quantize(Decimal("0.01")):
            raise ConversionError("inexact_amount")
        accounts.append(
            {
                "account_id": row["account_id"],
                "currency": "NGN",
                "rebuild_net": format(net.quantize(Decimal("0.01")), "f"),
                "opening_class": row["opening_class"],
                "post_cause": row["post_cause"],
            }
        )
    accounts.sort(key=lambda a: a["account_id"])
    document = {"schema": A10_REBUILD_SCHEMA, "accounts": accounts}
    try:
        parse_a10_rebuild(document)  # the drafter's own closed parse
    except AdmissibilityError as exc:
        raise ConversionError(exc.code) from None
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--classified", required=True, help="rebuild-classified.csv")
    parser.add_argument("--out", required=True, help="New file; never overwritten")
    args = parser.parse_args(argv)
    out = Path(args.out)
    try:
        with open(args.classified, newline="", encoding="utf-8") as handle:
            document = convert(list(csv.DictReader(handle)))
        if out.exists():
            raise ConversionError("output_exists")
        out.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    except (ConversionError, OSError) as exc:
        code = exc.code if isinstance(exc, ConversionError) else "unreadable_input"
        print(json.dumps({"converted": False, "refused": code}))
        return 2
    print(json.dumps({"converted": True, "accounts": len(document["accounts"])}))  # type: ignore[arg-type]
    return 0


if __name__ == "__main__":
    sys.exit(main())
