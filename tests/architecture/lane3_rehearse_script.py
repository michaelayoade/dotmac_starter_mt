"""`scripts/lane3_rehearse.sh`, read as the named units its guards assert.

D-S2c C1 moved every Lane 3 rehearsal step out of `exposure-rehearsal.yml` and
into this Starter-owned script, so the properties those steps carried are now
asserted HERE and survive the workflow's retirement (C5,
docs/LANE3_EXECUTION_TOPOLOGY.md § 13).

The script marks each unit with a `step "<name>"` line. A unit is everything
from one marker to the next, read the way bash will read it: comment-only and
blank lines are dropped, exactly as the YAML parser dropped a workflow's `#`
comments, so a sentence ABOUT a command can never satisfy a check for the
command. A commented-out invocation therefore disappears from its unit, and
whatever looked for it finds nothing. The remaining lines are dedented, so a
unit's text is byte-comparable with the canonical `run:` body it replaced.

Each unit is returned as `{"name": ..., "run": ...}`, the shape a parsed
workflow step has, so the existing checks and their planted-defect
sensitivity tests apply to the script unchanged.
"""

from __future__ import annotations

import copy
import re
import textwrap
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "lane3_rehearse.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "exposure-rehearsal.yml"

#: The one invocation each workflow job is allowed: the script, by phase.
PHASE_CALL = {
    "preflight": "bash scripts/lane3_rehearse.sh preflight\n",
    "rehearse": "bash scripts/lane3_rehearse.sh rehearse\n",
}

_STEP = re.compile(r'^  step "(?P<name>[^"]+)"$')


def units(phase: str, text: str | None = None) -> list[dict[str, str]]:
    """The named units of one phase function, in order.

    A prelude before the first marker (the phase's input checks) is returned
    as a unit named `""`, so nothing in the function escapes inspection.
    """
    source = SCRIPT.read_text(encoding="utf-8") if text is None else text
    lines = source.splitlines()
    header = f"{phase}() {{"
    if header not in lines:
        raise AssertionError(f"lane3_rehearse.sh has no {phase}() phase")
    start = lines.index(header)
    end = lines.index("}", start)
    found: list[dict[str, str]] = []
    name = ""
    body: list[str] = []

    def flush() -> None:
        code = [
            line for line in body if line.strip() and not line.lstrip().startswith("#")
        ]
        if code or name:
            run = textwrap.dedent("\n".join(code) + "\n") if code else ""
            found.append({"name": name, "run": run})

    for line in lines[start + 1 : end]:
        match = _STEP.match(line)
        if match:
            flush()
            name = match["name"]
            body = []
        else:
            body.append(line)
    flush()
    return found


def workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def as_document(phase: str = "rehearse", job: str = "rehearse") -> dict[str, Any]:
    """The workflow, with one job's steps replaced by the script's units.

    Dispatch inputs and job permissions stay where they are declared, on the
    workflow; the step sequence is read from the script. A guard written
    against a parsed workflow therefore reads the script's steps unchanged.
    """
    document = copy.deepcopy(workflow())
    document["jobs"][job]["steps"] = units(phase)
    return document


def script_calls(document: dict[str, Any], job: str) -> list[dict[str, Any]]:
    """Every step of a workflow job that runs the Lane 3 script."""
    return [
        step
        for step in document["jobs"][job]["steps"]
        if "scripts/lane3_rehearse.sh" in str(step.get("run", ""))
    ]
