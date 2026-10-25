"""ZTE dialect STUB: proves the interface fits a second addressing scheme.

ZTE addresses an ONT as ``gpon-onu_<shelf>/<slot>/<port>:<onu-id>`` (a port as
``gpon-olt_<shelf>/<slot>/<port>``). This stub implements that addressing and
NOTHING ELSE: it declares no operations and composes no commands, so any profile
that names it can claim no capability until a real dialect, parsers and
transcripts exist. No ZTE command is invented here.
"""

from __future__ import annotations

import re

from dotmac_device_cli.core.dialect import (
    OntRef,
    ParsedTable,
    RefError,
    register_dialect,
)
from dotmac_device_cli.core.profile import Profile

NAME = "zte"

_ONU = re.compile(r"^\s*gpon-onu_(\d+)/(\d+)/(\d+):(\d+)\s*$")
_OLT = re.compile(r"^\s*gpon-olt_(\d+)/(\d+)/(\d+)\s*$")


class ZteDialect:
    name = NAME

    def format_ref(self, ref: OntRef) -> str:
        if ref.ont_id is None:
            return f"gpon-olt_{ref.shelf}/{ref.slot}/{ref.port}"
        return f"gpon-onu_{ref.shelf}/{ref.slot}/{ref.port}:{ref.ont_id}"

    def parse_ref(self, text: str) -> OntRef:
        onu = _ONU.match(text or "")
        if onu:
            s, sl, p, o = (int(onu.group(i)) for i in (1, 2, 3, 4))
            ref = OntRef(NAME, s, sl, p, o, "")
        else:
            olt = _OLT.match(text or "")
            if olt is None:
                raise RefError(f"not a ZTE gpon-onu_/gpon-olt_ address: {text!r}")
            s, sl, p = (int(olt.group(i)) for i in (1, 2, 3))
            ref = OntRef(NAME, s, sl, p, None, "")
        return OntRef(
            NAME, ref.shelf, ref.slot, ref.port, ref.ont_id, self.format_ref(ref)
        )

    def operations(self) -> frozenset[str]:
        return frozenset()

    def compose(self, operation: str, profile: Profile, ref: OntRef | None) -> str:
        raise KeyError(operation)

    def parse_ont_table(self, text: str) -> ParsedTable:
        raise NotImplementedError("the ZTE dialect declares no parsers")


register_dialect(ZteDialect())
