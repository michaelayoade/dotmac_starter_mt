#!/usr/bin/env python3
"""``Lane3RunnerCapability.v1`` — can this SOURCE produce a rehearsal receipt?

A candidate wheel is built once and then depended on by a restore proof, an
issuer stand-up and a Lane 3 rehearsal before anything publishes it. Nothing
stopped a candidate being spent at a SHA whose runner **cannot produce a receipt
at all** — and today's `main` is exactly that SHA: the far-end sentinel makes
`qualify_vantage` refuse before the first `results.record`, so the run dies at
qualification and `build_receipt` is never reached.

This record answers one question, before the build: **could this source ever
green?** It is structurally incapable of answering **did it?**, and that
distinction is the whole design rather than a caution.

## Why it cannot claim a rehearsal ran or passed

`foundation-candidate.yml` deliberately does NOT gate on a passing rehearsal —
that is what breaks the bootstrap loop, and it stays. A capability record that
could be satisfied by a passing run would quietly reintroduce the loop from the
other side. So three properties hold, and none of them is a promise:

1. **It cannot be handed a result.** Every input is a path to a file in this
   repository. There is no run id, no receipt path, no SHA, no API, no network.
   A function that cannot RECEIVE a rehearsal outcome cannot report one.
2. **Its vocabulary cannot spell it.** :class:`Verdict` has two members and
   neither is `passed`, `ran`, `executed` or `green`.
3. **It cannot reach the receipt's vocabulary.** `dotmac_deployment_foundation
   .rehearsal.RequirementStatus` has six members, three of which
   (`executed_passed`, `executed_failed`, `not_executed`) are precisely the
   words this record must not be able to say. It is deliberately NOT imported
   and deliberately NOT reused.

**Point 3 is a standing instruction, not an observation.** The tempting
future edit is to "unify the two enums" — one status vocabulary for the lane,
less duplication, obviously tidier. Doing that hands this record the words
`executed_passed` and `executed_failed`, and from that moment a capability
verdict and a rehearsal outcome are the same type and nothing structural keeps
them apart. The duplication is the guard. Leave it.

`tests/architecture/test_lane3_runner_capability.py` enforces all three: an
import allowlist ratcheted in both directions, a refusal of any `Verdict` member
outside the declared pair, and a refusal of any schema field whose name matches
the outcome vocabulary.

## Why five reasons and not one aggregate

A gate that says `not_capable` without saying why is a gate whose only observed
value is the one somebody hardcoded. Each reason below is detected separately,
reported separately, and planted separately: repairing exactly one flips exactly
one, and a source with none of them reaches `capable`. Both directions are
proved, because a refusal that has never been observed turning into an
acceptance is a constant wearing a function's shape.

## What this establishes, and what it does not

It establishes that the runner has, or has not, the structural pieces a receipt
requires: a far-end observation it does not fabricate, a measured service state,
a vantage inside the accepted source set, a provocation the apply path can
genuinely fail, and a Compose identity that reaches the effects.

**It does not establish that any of them works.** A `capable` verdict means the
source could produce a receipt, not that it would produce a passing one, and not
that anyone ran it. The receipt remains the only evidence a rehearsal happened,
and `require_rehearsal.py` remains the only thing that reads one.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

SCHEMA = "Lane3RunnerCapability.v1"

#: The runner, and the collector that supplies its external half.
RUNNER = Path("scripts/exposure_rehearsal_runner.py")
COLLECTOR = Path("scripts/exposure-rehearsal/collect_probe_evidence.sh")

#: The named seam a conforming runner calls to induce a condition the apply path
#: cannot satisfy. DECLARED rather than inferred: "does this source provoke a
#: real failure?" is not answerable by pattern-matching arbitrary code, and a
#: detector that guessed would be satisfied by any `raise` anywhere. A runner
#: states that it provokes by calling something named for it.
PROVOCATION_SEAM = "provoke_apply_failure"

#: The keyword through which the derived Compose project must reach the effects.
#: `ComposeHostExposureEffects` labels every object it creates with the project
#: it is given; a derived name that never reaches it names nothing.
COMPOSE_PROJECT_KEYWORD = "compose_project"

_SENTINEL = re.compile(r"__TARGET_OBSERVED_V[46]__")
_OBSERVED_SOURCE = re.compile(r"observed_source_v[46]")
_LITERAL_SERVICE_STATE = re.compile(r'\\?"service_running\\?"\s*:\s*(true|false)\b')
_LITERAL_PRIVATE_INSIDE = re.compile(
    r'\\?"private_inside\\?"\s*:\s*\{[^}]*\\?"reachable\\?"\s*:\s*(true|false)\b'
)
_LITERAL_PRIVILEGED = re.compile(
    r'\\?"privileged_vantage_refused\\?"\s*:\s*(null|true|false)\b'
)


class Verdict(StrEnum):
    """The only two things this record can say.

    There is no `passed`, `ran`, `executed` or `green` member, and adding one
    would be adding the ability to make a claim this record has no evidence for.
    """

    CAPABLE = "capable"
    NOT_CAPABLE = "not_capable"


class Reason(StrEnum):
    """Why a source cannot produce a rehearsal receipt.

    Members carry explicit string values because they are written into a JSON
    record a person reads. Each is detected independently — an aggregate would
    send a fix-writer round the loop once per defect.
    """

    #: The collector emits a sentinel where the TARGET's observation of this
    #: vantage's source address belongs. `qualify_vantage` refuses on the
    #: mismatch branch, `_qualify` raises before the first `results.record`, and
    #: no receipt is produced AT ALL — this is not a failing item.
    FAR_END_SENTINEL = "far_end_sentinel"

    #: `service_running` is asserted as a literal rather than observed, and the
    #: runner reads an ABSENT key as `True`. Item 13 requires the negative to be
    #: measured against a running service; a literal is about no moment.
    SERVICE_STATE_ASSERTED = "service_state_asserted"

    #: No vantage inside the accepted source set exists, so items 12 and 16 can
    #: only be `executed_failed`: `private_inside` is a literal `false` and
    #: `privileged_vantage_refused` is a literal `null`.
    NO_INSIDE_VANTAGE = "no_inside_vantage"

    #: Nothing induces a condition the apply path cannot satisfy, so
    #: `transaction.rolled_back` has no reachable non-`None` value and item 8
    #: caps the lane at 15 of 16.
    NO_INDUCED_FAILURE = "no_induced_failure"

    #: The derived Compose project never reaches the effects, so item 1 passes
    #: carrying an evidence pointer that names a project Docker never created.
    COMPOSE_IDENTITY_UNUSED = "compose_identity_unused"

    #: A source file this record must read is missing or unparseable. Never a
    #: pass: absent must be distinguishable from unexamined.
    SOURCE_UNREADABLE = "source_unreadable"


@dataclass(frozen=True, slots=True)
class Finding:
    """One reason, with the source location a reader checks it against."""

    reason: Reason
    detail: str
    evidence: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, object]:
        return {
            "reason": str(self.reason),
            "detail": self.detail,
            "evidence": list(self.evidence),
        }


def _read(root: Path, relative: Path) -> tuple[str | None, Finding | None]:
    path = root / relative
    try:
        return path.read_text(encoding="utf-8"), None
    except OSError as exc:
        return None, Finding(
            Reason.SOURCE_UNREADABLE,
            f"{relative} could not be read ({exc}). A capability verdict over a "
            "file this record cannot open would be a verdict about nothing",
            (str(relative),),
        )


def _lines_matching(text: str, pattern: re.Pattern[str], relative: Path) -> list[str]:
    return [
        f"{relative}:{number}"
        for number, line in enumerate(text.splitlines(), 1)
        if pattern.search(line)
    ]


# ── the five detectors ──────────────────────────────────────────────────────


def detect_far_end_sentinel(collector: str) -> Finding | None:
    """The collector must not fabricate the far end's observation.

    A vantage cannot self-certify where it egresses from; only the target can
    say what source address it saw. A sentinel is non-empty, so `qualify_vantage`
    refuses through the MISMATCH branch rather than the empty one — either way
    before any item is recorded.
    """
    sentinels = _lines_matching(collector, _SENTINEL, COLLECTOR)
    if not sentinels:
        return None
    return Finding(
        Reason.FAR_END_SENTINEL,
        "the collector emits a sentinel where the target's observation of this "
        "vantage's source address belongs, so `_qualify` raises before the "
        "first `results.record` and no receipt is produced at all",
        tuple(sentinels),
    )


def detect_service_state_asserted(
    collector: str, runner_tree: ast.AST
) -> Finding | None:
    """Two halves, and either alone is enough to refuse.

    The collector ASSERTS a service state it does not observe, and the runner
    reads an ABSENT key as a running service — so an evidence file that simply
    omits the key is indistinguishable from one that measured it up.
    """
    literals = _lines_matching(collector, _LITERAL_SERVICE_STATE, COLLECTOR)
    defaults_true = [
        node
        for node in ast.walk(runner_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and len(node.args) == 2
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "service_running"
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value is True
    ]
    if not literals and not defaults_true:
        return None
    evidence = tuple(literals) + tuple(
        f"{RUNNER}:{node.lineno}" for node in defaults_true
    )
    halves = []
    if literals:
        halves.append(f"{len(literals)} asserted literal(s) in the collector")
    if defaults_true:
        halves.append("the runner reads an absent key as a running service")
    return Finding(
        Reason.SERVICE_STATE_ASSERTED,
        "item 13 requires the negative probe to be measured against a RUNNING "
        f"service, and a literal is about no moment at all: {'; '.join(halves)}",
        evidence,
    )


def detect_no_inside_vantage(collector: str) -> Finding | None:
    """Items 12 and 16 both need a vantage INSIDE the accepted source set.

    The collecting host is outside it by construction, so both values are
    emitted as literals rather than measured.
    """
    private = _lines_matching(collector, _LITERAL_PRIVATE_INSIDE, COLLECTOR)
    privileged = _lines_matching(collector, _LITERAL_PRIVILEGED, COLLECTOR)
    if not private and not privileged:
        return None
    return Finding(
        Reason.NO_INSIDE_VANTAGE,
        "no vantage inside the accepted source set takes these measurements, so "
        "`private_inside.reachable` and `privileged_vantage_refused` are "
        "emitted as literals and items 16 and 12 can only be `executed_failed`",
        tuple(private) + tuple(privileged),
    )


def detect_no_induced_failure(runner_tree: ast.AST) -> Finding | None:
    """Item 8 is unreachable unless something provokes a real failure.

    Detected by the ABSENCE of a call to the declared provocation seam rather
    than by pattern-matching arbitrary code. A detector that accepted any
    `raise` would be satisfied by every error path the runner already has, none
    of which the apply path meets.
    """
    for node in ast.walk(runner_tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name == PROVOCATION_SEAM:
                return None
    return Finding(
        Reason.NO_INDUCED_FAILURE,
        f"the runner calls no {PROVOCATION_SEAM}(), so nothing induces a "
        "condition the apply path cannot satisfy, `transaction.rolled_back` has "
        "no reachable non-None value, and item 8 caps the lane at 15 of 16",
        (str(RUNNER),),
    )


def detect_compose_identity_unused(runner_tree: ast.AST) -> Finding | None:
    """A derived Compose identity that reaches no consumer.

    `ComposeHostExposureEffects` labels every object with the project it is
    GIVEN. A derived name that reaches only an evidence string names a project
    Docker never created — and a false pointer inside a PASSING item is worse
    than an absent one, for the same reason `RequirementResult` refuses an empty
    detail: the pointer is checkable, and it is wrong.

    Either repair satisfies this and the record does not prefer one:

    * pass the derived name to the effects, so the claim becomes true; or
    * stop deriving it, and let the evidence name the project actually used.

    So the rule is stated as "derived and unconsumed" rather than "not passed as
    `compose_project=`" — a detector written for one repair would refuse the other.

    `lease.owns_project(...)` does NOT count as a consumer. It is `p.startswith(
    prefix)` over a name built by concatenating that prefix: true by
    construction, unable to fire, and its own `pragma` says so. A check that
    cannot fail consumes the name without using it.
    """
    derived = {
        node.targets[0].id
        for node in ast.walk(runner_tree)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and "compose_project_prefix" in ast.dump(node.value)
    }
    if not derived:
        return None

    for node in ast.walk(runner_tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "owns_project":
            continue
        for argument in (*node.args, *(k.value for k in node.keywords)):
            if isinstance(argument, ast.Name) and argument.id in derived:
                return None

    return Finding(
        Reason.COMPOSE_IDENTITY_UNUSED,
        f"{sorted(derived)} is derived from the lease's Compose prefix and "
        "reaches no consumer — only an evidence string and a check that is true "
        "by construction. So item 1 passes carrying a pointer to a project "
        "Docker never created. Either pass it to the effects or stop deriving "
        "it; leaving both as they are is what this refuses",
        (str(RUNNER),),
    )


# ── the record ──────────────────────────────────────────────────────────────


def assess(root: Path) -> dict[str, object]:
    """The whole record, from source bytes and nothing else."""
    findings: list[Finding] = []

    collector, collector_error = _read(root, COLLECTOR)
    runner, runner_error = _read(root, RUNNER)
    findings.extend(f for f in (collector_error, runner_error) if f is not None)

    runner_tree: ast.AST | None = None
    if runner is not None:
        try:
            runner_tree = ast.parse(runner)
        except SyntaxError as exc:
            findings.append(
                Finding(
                    Reason.SOURCE_UNREADABLE,
                    f"{RUNNER} does not parse ({exc}). A source that cannot be "
                    "read cannot be judged capable",
                    (str(RUNNER),),
                )
            )

    if collector is not None:
        findings.append(detect_far_end_sentinel(collector))
        findings.append(detect_no_inside_vantage(collector))
    if collector is not None and runner_tree is not None:
        findings.append(detect_service_state_asserted(collector, runner_tree))
    if runner_tree is not None:
        findings.append(detect_no_induced_failure(runner_tree))
        findings.append(detect_compose_identity_unused(runner_tree))

    reasons = [f for f in findings if f is not None]
    verdict = Verdict.NOT_CAPABLE if reasons else Verdict.CAPABLE
    return {
        "schema": SCHEMA,
        "verdict": str(verdict),
        "reasons": [f.as_dict() for f in reasons],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Decide whether this SOURCE could produce a Lane 3 rehearsal "
            "receipt. It cannot report whether a rehearsal ran or passed, and "
            "takes no input through which it could be told."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("."),
        help="repository root to read the runner and collector from",
    )
    parser.add_argument("--out", type=Path, help="write the record here as JSON")
    parser.add_argument(
        "--summary", type=Path, help="append a human-readable summary here"
    )
    args = parser.parse_args(argv)

    record = assess(args.root)
    text = json.dumps(record, indent=2, sort_keys=True)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)

    reasons = record["reasons"]
    assert isinstance(reasons, list)
    if args.summary:
        lines = [f"### Lane 3 runner capability: `{record['verdict']}`", ""]
        lines += [
            f"- `{r['reason']}` — {r['detail']}"  # type: ignore[index]
            for r in reasons
        ] or ["- no refusals; this source could produce a receipt"]
        lines += [
            "",
            "This says the source COULD produce a receipt. It does not say a "
            "rehearsal ran, and it cannot: it reads source files and nothing "
            "else.",
        ]
        with args.summary.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

    if record["verdict"] == str(Verdict.NOT_CAPABLE):
        print(
            f"\nREFUSED: {len(reasons)} reason(s). Do not build a candidate at "
            "this revision — its runner cannot produce a rehearsal receipt.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
