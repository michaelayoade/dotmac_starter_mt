"""The exam engine — one owner for what an exam *is*.

Before this module the answer depended on who was asking. A learner sitting a
course activity and an applicant sitting the entrance exam shared exactly one
function, ``grade_submission``; selection, option order, validity and result
policy were each implemented once, on whichever side happened to need them
first. Two consequences were live defects rather than asymmetries: course
learners saw an identical option order, so "the answer is the third one"
transferred between them, and a course sitting had no validity gate, so a
forty-second final at chance was recorded as a genuine result.

See ``docs/adr/0005-assessment-engine-owns-exam-logic.md``.

What lives here
---------------
The *decisions*: who may sit, which questions they get, in what order they see
them, whether the sitting counts as signal. Grading stays in ``grading`` as a
pure function; this module calls it rather than reimplementing it.

What does not live here
-----------------------
The *facts* — questions, options, answers, competency tags — which belong to
the banks, and persistence, which belongs to the adapters that know whether
they are writing against a ``Person`` or an ``Applicant``.
"""

from __future__ import annotations

import hashlib
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import JsonValue


@dataclass(frozen=True, slots=True)
class ValidityTerms:
    """Caller-selected validity policy; paper-derived arithmetic stays canonical."""

    seconds_per_question: float
    near_chance_margin: float
    fallback_score_floor: float
    fallback_duration_seconds: float

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise TypeError(f"{name} must be a finite number")
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if self.seconds_per_question < 0 or self.fallback_duration_seconds < 0:
            raise ValueError("validity durations must be nonnegative")
        if self.near_chance_margin <= 0:
            raise ValueError("near_chance_margin must be positive")
        if not 0 <= self.fallback_score_floor <= 1:
            raise ValueError("fallback_score_floor must be a fraction")


INVALID_NEAR_CHANCE = "near_chance"
INVALID_TOO_FAST = "too_fast"


class Sitter(Protocol):
    """Whoever is taking the exam.

    Deliberately narrow: the engine needs an identity to make its per-sitter
    decisions reproducible, and nothing else. A ``Person`` and an ``Applicant``
    both satisfy this, which is the point — the identity split between admitted
    learners and candidates is an admissions concern, not an exam one.
    """

    # A read-only property rather than an attribute, so both a mutable ORM
    # model and a frozen dataclass adapter satisfy it.
    @property
    def id(self) -> object: ...


class Question(Protocol):
    """The shape the engine needs from a question. Banks own the rest."""

    @property
    def ext_id(self) -> str: ...

    @property
    def category(self) -> str | None: ...

    @property
    def options(self) -> JsonValue: ...


QuestionT = TypeVar("QuestionT", bound=Question)


@dataclass(frozen=True)
class SelectionPolicy:
    """How many questions to draw, and how.

    ``per_category`` draws that many from each competency, so every sitter is
    measured on the same shape and their per-domain profiles stay comparable —
    a flat draw would hand one sitter nine numeracy items and another two.

    ``total`` draws that many across the bank, ignoring competency.

    Both zero means the whole bank, which is the behaviour that existed before
    any pooling and remains the default.
    """

    per_category: int = 0
    total: int = 0

    def __post_init__(self) -> None:
        if self.per_category and self.total:
            raise ValueError("choose per_category or total, not both")


def _rank(sitter: Sitter, salt: str, key: str) -> str:
    """A stable pseudo-random ordering key for (sitter, thing).

    Deterministic so a reload or a resumed sitting reproduces the same paper,
    and so grading can rebuild what was shown without persisting it. Salted so
    that selection order and presentation order are independent — otherwise the
    first questions drawn would also be the ones whose options moved least.
    """
    return hashlib.sha256(f"{sitter.id}:{salt}{key}".encode()).hexdigest()


def select(
    sitter: Sitter,
    questions: list[QuestionT],
    policy: SelectionPolicy,
    variant: str = "",
) -> list[QuestionT]:
    """The questions this sitter gets, in the order they get them.

    ``variant`` distinguishes repeat sittings by the same person. Without it a
    learner's second attempt at a pooled activity would draw the identical
    paper as their first, which removes the only reason to pool a bank that
    allows retakes. The entrance exam is a single sitting and passes nothing.
    """
    if policy.per_category:
        by_category: dict[str, list[QuestionT]] = {}
        for q in questions:
            by_category.setdefault(
                getattr(q, "category", None) or "general", []
            ).append(q)

        chosen: list[QuestionT] = []
        for group in by_category.values():
            if len(group) <= policy.per_category:
                # A category smaller than the draw contributes all of itself
                # rather than vanishing from the profile.
                chosen.extend(group)
                continue
            ranked = sorted(group, key=lambda q: _rank(sitter, variant, q.ext_id))
            chosen.extend(ranked[: policy.per_category])
    elif policy.total:
        ranked = sorted(questions, key=lambda q: _rank(sitter, variant, q.ext_id))
        chosen = ranked[: policy.total]
    else:
        chosen = list(questions)

    # Re-rank the whole paper so it is not grouped by competency, which would
    # telegraph the structure and let sitters compare notes by section.
    chosen.sort(key=lambda q: _rank(sitter, f"{variant}order:", q.ext_id))
    return chosen


def present_options(sitter: Sitter, question: Question) -> list[str]:
    """This question's options in a per-sitter order.

    A fixed order leaks: sitters talk, and "the answer is C" stays true for
    everyone. Deterministic in (sitter, question) so a reload does not move the
    options under autosaved answers. Safe because answers are submitted by
    option TEXT, so grading is order-independent.
    """
    opts = list(getattr(question, "options", None) or [])
    # Deterministic per-sitter option presentation; never generates credentials
    # or secrets. Bandit B311 is inapplicable to reproducible presentation.
    rng = random.Random(_rank(sitter, "", question.ext_id))  # noqa: S311  # nosec B311
    rng.shuffle(opts)
    return opts


def chance_baseline(
    questions: Sequence[Question], *, fallback_baseline: float
) -> float:
    """The score guessing would produce on *this* paper.

    Averaged over the questions that actually offer options, so a paper with
    true/false items reports the ~50% it really carries rather than the 25% a
    four-option assumption would claim.
    """
    if (
        isinstance(fallback_baseline, bool)
        or not isinstance(fallback_baseline, int | float)
        or not math.isfinite(fallback_baseline)
        or fallback_baseline < 0
    ):
        raise ValueError("fallback_baseline must be a finite nonnegative number")
    per_question = []
    for q in questions:
        opts = getattr(q, "options", None)
        if isinstance(opts, list) and len(opts) >= 2:
            per_question.append(1.0 / len(opts))
    if not per_question:
        return fallback_baseline
    return sum(per_question) / len(per_question)


def near_chance_floor(questions: Sequence[Question], *, terms: ValidityTerms) -> float:
    """Scores at or below this are indistinguishable from guessing."""
    return (
        chance_baseline(
            questions,
            fallback_baseline=terms.fallback_score_floor / terms.near_chance_margin,
        )
        * terms.near_chance_margin
    )


def duration_floor(
    questions: Sequence[Question], *, seconds_per_question: float
) -> float:
    """The fastest a sitting could plausibly have been read."""
    if (
        isinstance(seconds_per_question, bool)
        or not isinstance(seconds_per_question, int | float)
        or not math.isfinite(seconds_per_question)
        or seconds_per_question < 0
    ):
        raise ValueError("seconds_per_question must be a finite nonnegative number")
    return len(questions) * seconds_per_question


def check_validity(
    fraction: float,
    duration_seconds: float | None,
    *,
    terms: ValidityTerms,
    questions: Sequence[Question] | None = None,
) -> tuple[bool, str | None]:
    """Is this sitting real signal, or an absence of data?

    Pass ``questions`` — the paper actually served — and both floors follow from
    it. Without a paper the caller supplies both fallback floors explicitly.
    """
    score_floor = (
        near_chance_floor(questions, terms=terms)
        if questions
        else terms.fallback_score_floor
    )
    time_floor = (
        duration_floor(questions, seconds_per_question=terms.seconds_per_question)
        if questions
        else terms.fallback_duration_seconds
    )

    if fraction <= score_floor + 1e-9:
        return False, INVALID_NEAR_CHANCE
    if duration_seconds is None or duration_seconds < time_floor:
        return False, INVALID_TOO_FAST
    return True, None
