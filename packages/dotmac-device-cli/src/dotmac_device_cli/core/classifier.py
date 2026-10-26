"""Response classifier. Returns Ok | Unreadable, never a silent empty.

Classification happens ONCE, on the raw device text at the point of capture, and
travels as a typed result. Callers consume ``Reason``; they never re-match text.
Markers are DATA (``profile.error_markers``) evaluated in a fixed priority order,
so a firmware whose privilege failure looks like a syntax failure can say so in
its profile without code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from dotmac_device_cli.core.dialect import OntRow, ParsedTable
from dotmac_device_cli.core.profile import Profile, compile_regex
from dotmac_device_cli.core.results import Evidence, Ok, Reason, Unreadable

ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|[@-_])")
_PROMPT_LINE = re.compile(r"[>#]\s*$")

# Most specific first. A marker the profile does not define is skipped.
MARKER_PRIORITY = (
    "insufficient_privilege",
    "board_type_error",
    "ont_absent",
    "parameter_error",
    "unknown_command",
    "unrecognized_response",
)


class Kind(str, Enum):
    DATA = "data"
    ABSENT = "absent"


@dataclass(frozen=True, slots=True)
class Classified:
    kind: Kind
    body: str  # device text without echo line and trailing prompt
    echo_ok: bool


def clean_text(raw: str, profile: Profile) -> str:
    text = raw.replace("\r", "")
    if "ansi" in profile.paging.strip:
        text = ANSI.sub("", text)
    if "pager_marker" in profile.paging.strip:
        text = re.sub(profile.prompts.pager, "\n", text)
    return text


def _split(text: str, command: str) -> tuple[str, bool]:
    lines = text.split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and _PROMPT_LINE.search(lines[-1]):
        lines.pop()
    first = next((i for i, ln in enumerate(lines) if ln.strip()), None)
    echo_ok = False
    if first is not None and command and command in lines[first]:
        echo_ok = True
        lines.pop(first)
    return "\n".join(lines).strip("\n"), echo_ok


def classify_response(
    profile: Profile, command: str, raw: str, evidence: Evidence
) -> Ok[Classified] | Unreadable:
    text = clean_text(raw, profile)
    body, echo_ok = _split(text, command)
    flag = "" if echo_ok or not profile.send.verify_echo else " [echo_mismatch]"
    for marker_id in MARKER_PRIORITY:
        pattern = profile.error_markers.get(marker_id)
        if pattern is None:
            continue
        match = compile_regex(pattern).search(text)
        if match is None:
            continue
        if marker_id == "ont_absent":
            return Ok(Classified(Kind.ABSENT, body, echo_ok), evidence)
        line = match.group(0).strip().splitlines()[-1][:120]
        return Unreadable(Reason(marker_id), f"{line}{flag}", evidence)
    if flag:
        return Unreadable(
            Reason.UNRECOGNIZED_RESPONSE, f"echo not seen{flag}", evidence
        )
    if not body.strip():
        return Unreadable(Reason.UNRECOGNIZED_RESPONSE, "empty response", evidence)
    return Ok(Classified(Kind.DATA, body, echo_ok), evidence)


@dataclass(frozen=True, slots=True)
class OntInventory:
    """Absence is a value: ``explicitly_empty`` is set only on the device's own word."""

    entries: tuple[OntRow, ...]
    explicitly_empty: bool


def classify_table(
    parsed: ParsedTable, evidence: Evidence
) -> Ok[OntInventory] | Unreadable:
    """The only place a parsed table becomes Ok or Unreadable."""
    if parsed.data_row_hints and not parsed.rows:
        return Unreadable(
            Reason.ROWS_UNPARSED, "ONT rows were present but none parsed", evidence
        )
    if parsed.rows:
        if parsed.data_row_hints > len(parsed.rows):
            return Unreadable(
                Reason.ROWS_UNPARSED,
                f"{parsed.data_row_hints} rows seen, {len(parsed.rows)} parsed",
                evidence,
            )
        if parsed.orphan_descriptions:
            return Unreadable(
                Reason.ROWS_UNPARSED,
                f"{parsed.orphan_descriptions} description(s) match no listed ONT",
                evidence,
            )
        # The trailer ("the total of ONTs are: N") is a completeness check.
        if parsed.declared_total is None:
            return Unreadable(
                Reason.TRUNCATED, "rows listed but no total trailer", evidence
            )
        if len(parsed.rows) < parsed.declared_total:
            return Unreadable(
                Reason.TRUNCATED,
                f"device declared {parsed.declared_total} ONTs, "
                f"{len(parsed.rows)} listed",
                evidence,
            )
        if len(parsed.rows) > parsed.declared_total:
            return Unreadable(
                Reason.ROWS_UNPARSED,
                f"{len(parsed.rows)} rows listed, device declared "
                f"{parsed.declared_total}",
                evidence,
            )
        # Reverse of the orphan check: a description table that does not
        # account for a listed ONT must not read as an empty description.
        # After the trailer checks, so a cut-off table stays TRUNCATED.
        if parsed.missing_descriptions:
            return Unreadable(
                Reason.ROWS_UNPARSED,
                f"{parsed.missing_descriptions} listed ONT(s) have no description",
                evidence,
            )
        return Ok(OntInventory(parsed.rows, False), evidence)
    if parsed.explicitly_empty:
        return Ok(OntInventory((), True), evidence)
    if parsed.recognized:
        # A header with no rows and no empty marker is what a cut-off read looks like.
        return Unreadable(Reason.TRUNCATED, "table header but no rows", evidence)
    return Unreadable(Reason.UNRECOGNIZED_RESPONSE, "not an ONT table", evidence)
