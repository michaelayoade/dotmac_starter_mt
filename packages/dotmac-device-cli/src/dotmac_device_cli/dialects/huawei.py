"""Huawei dialect: F/S/P + ONT-ID addressing, command composition, table parsing.

Covers the OLT families that print ``display ont info <port> all`` tables
(MA56xx and MA58xx). Per-model differences (argument spelling, pager, quirks)
are DATA in the profile layers; only addressing and parsing are code.
"""

from __future__ import annotations

import re

from dotmac_device_cli.core.dialect import (
    OntRef,
    OntRow,
    ParsedTable,
    RefError,
    register_dialect,
)
from dotmac_device_cli.core.profile import Profile, placeholders, render

NAME = "huawei"

# "0/ 2/1" (padded slot) and "0/2/1:5" / "0/2/1 5" are all accepted on input.
_REF = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*/\s*(\d+)(?:\s*[ :]\s*(\d+))?\s*$")

_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|[@-_])")
_PAGER = re.compile(r"-{2,}\s*More\s*(?:\([^)]*\)\s*)?-{2,}[ \t]*")
_SPACED_FSP = re.compile(r"(?<![\d/])(\d+)/\s*(\d+)/\s*(\d+)(?=\s+\d+\s+\S)")
_HEADER = re.compile(r"F/S/P\s+ONT\b", re.IGNORECASE)
_EMPTY = re.compile(r"No\s+ONT|the total of ONTs are:\s*0\b", re.IGNORECASE)
_TOTAL = re.compile(r"the total of ONTs are:\s*(\d+)", re.IGNORECASE)
_HINT = re.compile(
    r"^\s*\d+/\d+/\d+\s+\d+\s+[0-9A-Za-z-]{8,}\s+\S+\s+(?:online|offline)\b",
    re.IGNORECASE | re.MULTILINE,
)
_ROW = re.compile(
    r"^\s*(\d+)/(\d+)/(\d+)\s+(\d+)\s+([0-9A-Za-z-]{8,})\s+(\S+)\s+"
    r"(online|offline)\s+(\S+)\s+(\S+)(?:\s+(.*?))?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_PROTECT = re.compile(r"^(?:yes|no)\b\s*", re.IGNORECASE)

_TEMPLATE_FOR = {
    "version": "version",
    "list_onts": "ont_info_all",
    "ont_detail": "ont_detail",
}


def normalize(output: str) -> str:
    """Strip ANSI redraws and pager markers, compact ``0/ 2/1`` to ``0/2/1``.

    The pager erases its marker with cursor-back sequences, so the next row can
    follow the marker on the same line. Removing both lets the table parse as if
    paging were off.
    """
    text = _ANSI.sub("", output).replace("\r", "")
    text = _PAGER.sub("\n", text)
    return _SPACED_FSP.sub(lambda m: f"{m.group(1)}/{m.group(2)}/{m.group(3)}", text)


class HuaweiDialect:
    name = NAME

    def format_ref(self, ref: OntRef) -> str:
        base = f"{ref.shelf}/{ref.slot}/{ref.port}"
        return base if ref.ont_id is None else f"{base} {ref.ont_id}"

    def parse_ref(self, text: str) -> OntRef:
        match = _REF.match(text or "")
        if match is None:
            raise RefError(f"not a Huawei F/S/P[ ONT-ID] address: {text!r}")
        shelf, slot, port = (int(match.group(i)) for i in (1, 2, 3))
        ont = None if match.group(4) is None else int(match.group(4))
        ref = OntRef(NAME, shelf, slot, port, ont, "")
        return OntRef(NAME, shelf, slot, port, ont, self.format_ref(ref))

    def operations(self) -> frozenset[str]:
        return frozenset({"version", "list_onts"})

    def compose(self, operation: str, profile: Profile, ref: OntRef | None) -> str:
        template = profile.read_templates[_TEMPLATE_FOR[operation]]
        wanted = placeholders(template.command)
        available: dict[str, object] = {}
        if ref is not None:
            available = {"frame": ref.shelf, "slot": ref.slot, "port": ref.port}
            if ref.ont_id is not None:
                available["ont_id"] = ref.ont_id
        return render(template, profile, **{k: available[k] for k in wanted})

    def parse_ont_table(self, text: str) -> ParsedTable:
        clean = normalize(text)
        rows: list[OntRow] = []
        for m in _ROW.finditer(clean):
            shelf, slot, port, ont = (int(m.group(i)) for i in (1, 2, 3, 4))
            rest = _PROTECT.sub("", m.group(10) or "").strip()
            native = f"{shelf}/{slot}/{port} {ont}"
            rows.append(
                OntRow(
                    ref=OntRef(NAME, shelf, slot, port, ont, native),
                    serial=m.group(5),
                    admin_state=m.group(6).lower(),
                    run_state=m.group(7).lower(),
                    config_state=m.group(8).lower(),
                    match_state=m.group(9).lower(),
                    description=rest,
                )
            )
        total = _TOTAL.search(clean)
        return ParsedTable(
            rows=tuple(rows),
            data_row_hints=len(_HINT.findall(clean)),
            recognized=bool(_HEADER.search(clean) or total or _EMPTY.search(clean)),
            explicitly_empty=bool(_EMPTY.search(clean)),
            declared_total=int(total.group(1)) if total else None,
        )


register_dialect(HuaweiDialect())
