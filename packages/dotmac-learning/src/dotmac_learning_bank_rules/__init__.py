"""Standard-library bank rules from the versioned dotmac-learning distribution.

One prospective reusable owner; YAML/path/CLI adapters remain product-side.
Rules and arithmetic are unchanged. Academy must retire its local rules at
installed-package cutover; this source preparation does not claim that cutover.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_TARGET = {"recall": 0.20, "application": 0.50, "analysis": 0.30}
_TOL = 0.10

# Distractor-balance thresholds. A bank fails these when the correct option is
# identifiable by its shape rather than its content — the standard authoring
# failure where the answer is written as the careful full sentence and the
# distractors as short dismissals. A learner can then score well by picking the
# longest option without reading the course, so the bank measures nothing.
# The check is symmetric: an answer that is reliably the *shortest* option is
# just as easy to spot as one that is reliably the longest, and authors fixing
# the second failure routinely create the first.
_MIN_BALANCE_SAMPLE = 5  # below this, one question swings the share too far
_MAX_EXTREME_SHARE = 0.40  # chance is 0.25 for 4-option questions
_MAX_LENGTH_RATIO = 1.30  # mean correct length over mean distractor length


_ASSESSMENT_MODES = {"practice", "graded", "exam"}


@dataclass
class BankDoc:
    course: str
    chapter: int | None
    kind: str
    version: int
    questions: list[dict[str, Any]]
    # Optional assessment policy declared alongside the questions. Absent keys
    # leave the Activity's existing value alone, so adding a policy to one bank
    # never disturbs content that has not declared one. Defaulted so that every
    # existing BankDoc(...) construction keeps working.
    policy: dict[str, Any] = field(default_factory=dict)


def _policy_violations(doc: BankDoc) -> list[str]:
    """Validate a declared assessment policy against the bank it applies to."""
    out: list[str] = []
    policy = doc.policy
    unknown = set(policy) - {"pool", "max_attempts", "mode", "pass_threshold"}
    if unknown:
        out.append(f"policy: unknown key(s) {', '.join(sorted(unknown))}")

    pool = policy.get("pool")
    if pool is not None:
        if not isinstance(pool, int) or pool < 1:
            out.append(f"policy: pool must be a positive integer, got {pool!r}")
        elif pool > len(doc.questions):
            # A pool at or above the bank size draws every question every time,
            # which is the behaviour the author was trying to move away from.
            out.append(
                f"policy: pool {pool} exceeds the {len(doc.questions)} questions "
                "in the bank — nothing is held back"
            )

    attempts = policy.get("max_attempts")
    if attempts is not None and (not isinstance(attempts, int) or attempts < 1):
        out.append(f"policy: max_attempts must be a positive integer, got {attempts!r}")

    threshold = policy.get("pass_threshold")
    if threshold is not None:
        if not isinstance(threshold, int | float) or isinstance(threshold, bool):
            out.append(f"policy: pass_threshold must be a number, got {threshold!r}")
        elif not 0.0 <= float(threshold) <= 1.0:
            out.append(
                "policy: pass_threshold is a fraction between 0 and 1, "
                f"got {threshold!r}"
            )

    mode = policy.get("mode")
    if mode is not None and mode not in _ASSESSMENT_MODES:
        out.append(
            f"policy: mode must be one of {', '.join(sorted(_ASSESSMENT_MODES))}, "
            f"got {mode!r}"
        )

    return out


def _competency_of(q: dict[str, Any]) -> str | None:
    """The skill domain a question measures, under either spelling.

    The entrance bank tags `category:`; the 252 technical manual banks tag
    `competency:` and meant the same thing. Only `category` was ever read, so
    every technical question imported with a NULL domain and the taxonomy the
    content team maintained was discarded at the door — which also left those
    banks outside the per-domain balance check below.
    """
    value = q.get("category") or q.get("competency")
    return str(value) if value else None


def _balance_stats(
    questions: list[dict[str, Any]],
) -> tuple[int, list[str], list[str], list[float]]:
    """Return (sample, uniquely-longest ids, uniquely-shortest ids, length ratios).

    Only questions carrying a real option list participate; ``numeric`` and
    ``short_text`` items have nothing to compare and ``truefalse`` items have
    fixed-length options. The extreme-length tests apply to single-answer
    questions only — a ``multi`` question with three correct options holds both
    the longest and the shortest string by construction.
    """
    sample = 0
    longest_ids: list[str] = []
    shortest_ids: list[str] = []
    ratios: list[float] = []

    for q in questions:
        opts = q.get("options")
        correct = q.get("correct")
        if q.get("type") == "truefalse":
            # Fixed "true"/"false" options: the one-character difference between
            # them is not a signal an author can balance away.
            continue
        if not isinstance(opts, list) or len(opts) < 2:
            continue
        if not isinstance(correct, list) or not correct:
            continue
        chosen = [o for o in opts if o in correct]
        distractors = [o for o in opts if o not in correct]
        if not chosen or not distractors:
            continue

        sample += 1
        mean_correct = sum(len(str(o)) for o in chosen) / len(chosen)
        mean_distractor = sum(len(str(o)) for o in distractors) / len(distractors)
        if mean_distractor:
            ratios.append(mean_correct / mean_distractor)

        if q.get("type") == "multi":
            continue
        lengths = [len(str(o)) for o in opts]
        answer = len(str(chosen[0]))
        qid = str(q.get("id"))
        # A tie leaks no signal — only a strictly extreme option does.
        if lengths.count(max(lengths)) == 1 and answer == max(lengths):
            longest_ids.append(qid)
        if lengths.count(min(lengths)) == 1 and answer == min(lengths):
            shortest_ids.append(qid)

    return sample, longest_ids, shortest_ids, ratios


def _distractor_balance_violations(
    questions: list[dict[str, Any]], scope: str = ""
) -> list[str]:
    """Flag banks whose correct options are identifiable by length alone.

    ``scope`` labels a subset (a competency category) so the message says where
    the imbalance is.
    """
    sample, longest_ids, shortest_ids, ratios = _balance_stats(questions)
    if sample < _MIN_BALANCE_SAMPLE:
        return []

    where = f"{scope}: " if scope else ""
    out: list[str] = []
    for label, ids in (("longest", longest_ids), ("shortest", shortest_ids)):
        share = len(ids) / sample
        if share > _MAX_EXTREME_SHARE:
            shown = ", ".join(ids[:8])
            more = f" (+{len(ids) - 8} more)" if len(ids) > 8 else ""
            out.append(
                f"{where}distractor balance: correct answer is the {label} option in "
                f"{len(ids)}/{sample} questions ({share:.0%}, max "
                f"{_MAX_EXTREME_SHARE:.0%}) — {shown}{more}"
            )

    if ratios:
        mean_ratio = sum(ratios) / len(ratios)
        if not 1 / _MAX_LENGTH_RATIO <= mean_ratio <= _MAX_LENGTH_RATIO:
            direction = "longer" if mean_ratio > 1 else "shorter"
            out.append(
                f"{where}distractor balance: correct options average {mean_ratio:.2f}x "
                f"the length of their distractors — consistently {direction} "
                f"(allowed {1 / _MAX_LENGTH_RATIO:.2f}x to {_MAX_LENGTH_RATIO:.2f}x)"
            )

    return out


def lint_bank(doc: BankDoc) -> list[str]:
    """Validate a BankDoc against rubric-mix and correct-in-options rules.

    Returns a (possibly empty) list of human-readable violation strings.
    An empty list means the bank is clean.

    Rules:
    - Bank must not be empty.
    - Each question must have a valid rubric_category (recall/application/analysis).
    - For non-truefalse questions, every entry in `correct` must appear in `options`.
    - The overall rubric mix must be 20% recall / 50% application / 30% analysis
      within ±10 percentage points.
    - Distractors must be balanced against the correct option: the answer may be
      the strictly longest — or strictly shortest — option in at most 40% of
      single-answer questions, and correct options must average between 0.77x
      and 1.30x their distractors' length.
    - A declared `policy` block must use known keys and sane values, and a
      question pool must actually hold something back.
    """
    out: list[str] = []
    n = len(doc.questions)
    if n == 0:
        return ["bank has no questions"]

    counts: dict[str, int] = {k: 0 for k in _TARGET}

    for q in doc.questions:
        cat = q.get("rubric_category")
        if cat not in _TARGET:
            out.append(f"{q.get('id')}: invalid rubric_category {cat!r}")
            continue
        counts[cat] += 1
        qtype = q.get("type")
        if qtype == "numeric":
            try:
                float(q.get("correct", ""))
            except (ValueError, TypeError):
                out.append(f"{q.get('id')}: numeric correct must be a parseable number")
        elif qtype == "short_text":
            accepted = q.get("correct", [])
            if not isinstance(accepted, list) or len(accepted) == 0:
                out.append(
                    f"{q.get('id')}: short_text correct must be a non-empty list"
                )
        elif qtype != "truefalse":
            raw_opts = q.get("options", [])
            # An option that is not a string means the YAML parsed it as
            # something else — most often text of the form "Word: rest", which
            # becomes a mapping. Report it; do not raise on the way past.
            malformed = [o for o in raw_opts if not isinstance(o, str)]
            if malformed:
                out.append(
                    f"{q.get('id')}: option is not text — {malformed[0]!r}. Text "
                    f"beginning 'Word: ' parses as a YAML mapping; quote it or reword."
                )
                continue
            opts = set(raw_opts)
            for c in q.get("correct", []):
                if c not in opts:
                    out.append(f"{q.get('id')}: correct {c!r} not in options")

    for cat, target in _TARGET.items():
        frac = counts[cat] / n
        # +1e-9 epsilon: a mix exactly at the tolerance edge (e.g. analysis 40%
        # vs target 30% ±10%) must pass — bare float subtraction yields
        # 0.4-0.3==0.10000000000000003 and would wrongly reject a compliant bank.
        if abs(frac - target) > _TOL + 1e-9:
            out.append(
                f"rubric mix off: {cat} {frac:.0%} (target {target:.0%} ±{_TOL:.0%})"
            )

    out.extend(_distractor_balance_violations(doc.questions))

    # Where questions carry a competency `category`, that category is a scoring
    # unit in its own right — the entrance exam reports a per-category profile
    # and draws per category. A bank can therefore look balanced overall while
    # one competency is entirely guessable, which is the case that matters most:
    # a candidate's safety score is not rescued by well-written numeracy items.
    categories: dict[str, list[dict[str, Any]]] = {}
    for q in doc.questions:
        category = _competency_of(q)
        if category:
            categories.setdefault(category, []).append(q)
    if len(categories) > 1:
        for category, group in sorted(categories.items()):
            out.extend(_distractor_balance_violations(group, scope=category))

    out.extend(_policy_violations(doc))

    return out


def balance_targets(doc: BankDoc) -> list[dict[str, Any]]:
    """For each unbalanced question, the option lengths that would fix it.

    Rebalancing by eye oscillates. Trimming an over-long answer makes it the
    *shortest* option; padding distractors to compensate makes it the longest
    again. Two passes over foundation ch05 went 85% -> 78% and back to
    answer-longest without converging, which is what prompted this.

    The stable shape is distractors that **straddle** the answer — at least one
    longer, at least one shorter, and a mean close to the answer's own length.
    That satisfies both halves of the extreme-share rule and puts the mean ratio
    near 1.0 rather than near an edge of the allowed band.

    Targets are derived from the same constants the linter checks against, so
    they cannot drift from the rule they are meant to satisfy.
    """
    out: list[dict[str, Any]] = []
    for q in doc.questions:
        options = q.get("options") or []
        correct = (q.get("correct") or [None])[0]
        if len(options) < 3 or not isinstance(correct, str):
            continue
        distractors = [o for o in options if o != correct]
        if len(distractors) < 2:
            continue

        a = len(correct)
        ordered = sorted(distractors, key=len)
        longest_other = len(ordered[-1])
        shortest_other = len(ordered[0])
        # Only actual violations. The linter flags an answer that is *strictly*
        # longest or shortest, so ties are fine and equal-length options are
        # ideal — "20% / 35% / 50% / 75%" needs no help. Reporting those as work
        # invites damaging a balanced question to satisfy a number.
        strictly_longest = a > longest_other
        strictly_shortest = a < shortest_other
        if not (strictly_longest or strictly_shortest):
            continue

        # Spread the distractors either side of the answer. The widest sits
        # above it, the narrowest below, the rest near it — mean lands ~= a,
        # so the ratio check clears with margin rather than by a hair.
        spread = [1.18, 1.0, 0.84, 0.92, 1.10][: len(distractors)]
        targets = [max(8, round(a * f)) for f in spread]
        out.append(
            {
                "id": q.get("id"),
                "answer_len": a,
                "distractors": [
                    {"text": d, "now": len(d), "target": t}
                    for d, t in zip(ordered, sorted(targets), strict=False)
                ],
            }
        )
    return out


# Hedges used to lengthen a distractor. Every one is a qualifier that asserts no
# new fact, which is what makes this safe to apply mechanically: a distractor is
# already the wrong answer, so hedging it cannot make it more or less wrong. The
# same appended to a *correct* answer would be unacceptable, which is why
# propose_balance only ever touches distractors.
_HEDGES = [
    ", in practice",
    ", as a general rule",
    ", in most ordinary cases",
    ", which is the usual assumption here",
    ", at least as it is normally understood on site",
    ", which is what most people would reasonably expect to happen",
]


def propose_balance(doc: BankDoc) -> list[dict[str, Any]]:
    """Concrete edits that would bring each unbalanced question into range.

    The binding constraint is narrower than `balance_targets` implies. A
    question needs one distractor longer than the answer and one shorter — not
    three distractors at ideal lengths. Chasing all three took four passes on
    foundation ch07; computing exactly how many characters the longest
    distractor needed took one.

    So this proposes the minimum edit: extend the longest distractor just past
    the answer, using the shortest hedge that gets there. Output is for review
    rather than blind application — a hedge that reads badly in context should
    be replaced by hand, and questions whose answer is long for domain reasons
    ("Within 50 milliseconds", "Core / Metro / Access / International")
    should be left alone entirely.
    """
    proposals: list[dict[str, Any]] = []
    for row in balance_targets(doc):
        distractors = row["distractors"]
        if not distractors:
            continue
        answer_len = row["answer_len"]
        longest = max(distractors, key=lambda d: d["now"])
        if longest["now"] > answer_len:
            continue  # already straddled above; the shortfall is on the low side

        needed = answer_len - longest["now"] + 1
        hedge = next((h for h in _HEDGES if len(h) >= needed), None)
        if hedge is None:
            proposals.append(
                {
                    "id": row["id"],
                    "option": longest["text"],
                    "needed": needed,
                    "suggestion": None,
                    "note": "gap too large for a hedge — rewrite or trim the answer",
                }
            )
            continue

        proposals.append(
            {
                "id": row["id"],
                "option": longest["text"],
                "needed": needed,
                "suggestion": longest["text"].rstrip(".") + hedge,
                "note": None,
            }
        )
    return proposals


__all__ = ["BankDoc", "lint_bank", "balance_targets", "propose_balance"]
