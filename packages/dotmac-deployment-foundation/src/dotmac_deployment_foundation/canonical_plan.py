"""The ten canonicalization rules a PLAN document is hashed under, owned once.

## Why this is a shared core and not a pattern to copy

`FoundationExecutionPlanV1` established these rules and
`RecoveryExecutionPlanV1` needs every one of them. The tempting move is a second
forty-line canonicalizer beside the first — they are obviously parallel, and
each is short.

That is a SECOND AUTHORITY over one question, and the failure mode is not that
the copy is wrong on the day it is written. **Copies agree right up until they
don't.** One gets a rule tightened, or a new refusable shape closed, and the
other does not; the divergence is invisible because both still look correct, and
it surfaces as two documents that hash under different rules while both claim to
be canonical. This package has now paid for that shape three times —
`AuthorizationReceipt` restating `("deploy", "rollback")` instead of reading
`OPERATIONS`, `discover_bindings` about to be copied for a second declaration
kind, and Control's `plan_digest` versus the Foundation's. `discovery.py`'s
docstring argues it at length and the argument is the same one.

So the rules live HERE, once, and every plan document gets all ten by
construction rather than by discipline.

## The ten rules

Restated from `execution_plan.py`, which authored them, because a reader of a
canonicalizer needs them in front of them:

1. **Bytes** are ``json.dumps(document, sort_keys=True, separators=(",", ":"),
   ensure_ascii=True).encode("utf-8")``.
2. **ASCII only**, and a non-ASCII string is REFUSED rather than normalized.
   With ``ensure_ascii=True`` the output is ASCII by construction, so the check
   and the encoder can never disagree — and the Unicode-normalization question
   (``é`` versus ``e`` + U+0301, two byte strings for one name) never arises.
3. **Keys sorted** by code point at every depth.
4. **No insignificant whitespace.**
5. **Every declared key is always present, and ``null`` never appears.** Absence
   is ``""``, ``[]`` or ``false``. A missing key and an explicit null are two
   encodings of one fact and would produce two digests for it.
6. **Integers only.** Float repr is platform- and version-sensitive, so a value
   serializing as ``1.1`` on one runtime and ``1.1000000000000001`` on another
   is a digest that disagrees with itself.
7. **Order-bearing arrays keep their order**; every other array is sorted and
   deduplicated, because it is a set. WHICH arrays are which is the document's
   own business and not this module's.
8. **No prose.** Human descriptions are excluded, so an edit to a sentence
   cannot change a digest somebody has already signed.
9. **The digest covers the document ALONE** — no wrapper, no envelope, no
   sibling keys. That is the entire lesson of the Control divergence.
10. The facility VERSION is inside the document, because a plan's meaning is
    what *this* version does with it.

Rules 5, 6 and 2 are enforced here by :func:`refuse_non_ascii`, walking every
value at every depth. Rules 1, 3 and 4 are the encoder call. Rules 7, 8 and 10
are decisions each document makes when it builds itself, and this module cannot
check them — stated so the split is visible rather than assumed.

## The schema guard is also the TYPE-CONFUSION refusal

:func:`canonical_plan_bytes` refuses a document whose ``schema`` is not the one
asked for, and that refusal does two jobs. It stops a WRAPPER being hashed as
though it were the plan (rule 9, the original defect). And it stops one plan
KIND being hashed as another — which matters more since `RecoveryExecutionPlanV1`
exists, because two documents that both canonicalize cleanly and mean different
acts are exactly how an unauthorized act gets executed under a digest somebody
recognises.

`FoundationExecutionPlanV1` gets the second job for free from a field it happens
to carry: `operation` is checked against `authorization.OPERATIONS`, so a
document is self-identifying as a deploy or a rollback. `RecoveryExecutionPlanV1`
deliberately has NO operation field — one act, no sibling to be told apart from,
and a field carrying no information is one a later author finds something to put
in — so for that document the schema guard is the ONLY thing standing between
the two kinds. It is therefore not a formality, and
`test_deployment_foundation_recovery_plan.py` drives a real document of each kind
into the other's acceptance points and requires a distinct refusal every time.
"""

from __future__ import annotations

import json
from typing import Any, Final

from .errors import SpecError

__all__ = [
    "EXECUTION_PLAN_WRONG_TYPE",
    "PLAN_NOT_THIS_DOCUMENT",
    "PLAN_VALUE_REFUSED",
    "RECOVERY_PLAN_WRONG_TYPE",
    "canonical_plan_bytes",
    "refuse_non_ascii",
]

#: Stable identifiers for this core's two refusals. Assert these; read the
#: prose. `execution_plan.py` predates codes and its callers match on wording,
#: so the messages below keep the phrases those tests already bind to — a
#: constraint worth naming, because it is the reason this module cannot freely
#: reword: `test_the_digest_covers_the_document_alone` matches
#: "covers THIS document".
PLAN_NOT_THIS_DOCUMENT: Final = "canonical_plan.not_this_document"
PLAN_VALUE_REFUSED: Final = "canonical_plan.value_refused"

#: The OBJECT-level twin of `PLAN_NOT_THIS_DOCUMENT`, one code per plan kind.
#:
#: They live here, next to the document-level guard, because they answer the
#: same question at two altitudes — *is this the thing I was asked to judge?* —
#: and because three modules need them: both plan types and `engine/run.py`.
#: This module imports nothing but `errors`, so a constant here can never be
#: part of an import cycle.
#:
#: That last clause was learned rather than assumed. Adding `recovery_plan`
#: broke the package on import, because `execution_plan` imported `engine.plan`
#: at module scope while `engine/__init__` imported `engine.run` which imported
#: `execution_plan` back — a cycle that resolved only through the order
#: `__init__` happened to use, so any new module importing `execution_plan`
#: first brought it down. The root is fixed there (the import was for an
#: annotation and `from __future__ import annotations` made it unnecessary at
#: runtime), and these constants sit in a leaf so the same class of breakage
#: cannot come back through them.
#:
#: TWO codes rather than one shared "wrong plan kind", deliberately. The
#: interchangeability proof drives a real document of each kind into the other's
#: acceptance points, and a single code could not tell "the deploy path refused a
#: recovery plan" from "the recovery path refused a deploy plan" — which is one
#: direction proven twice and the other not at all.
EXECUTION_PLAN_WRONG_TYPE: Final = "execution_plan.wrong_type"
RECOVERY_PLAN_WRONG_TYPE: Final = "recovery_plan.wrong_type"


def refuse_non_ascii(value: Any, *, path: str) -> None:
    """Rules 2, 5 and 6, applied to every value at every depth.

    Refusing beats normalizing. A normalizer is a second opinion about what the
    bytes are, and this contract's whole problem was two parties each holding a
    defensible opinion — so there is exactly one rule here and it is checkable
    by anyone in one line.
    """
    if isinstance(value, str):
        if not value.isascii():
            raise SpecError(
                f"{path} contains a non-ASCII character. A plan digest is "
                "compared across three systems, and two byte strings for one "
                "name (NFC versus NFD) would silently be two plans. ASCII "
                "removes the question rather than answering it",
                code=PLAN_VALUE_REFUSED,
            )
        return
    if isinstance(value, bool | int):
        return
    if isinstance(value, dict):
        for key, item in value.items():
            refuse_non_ascii(key, path=f"{path}.{key}")
            refuse_non_ascii(item, path=f"{path}.{key}")
        return
    if isinstance(value, list | tuple):
        for index, item in enumerate(value):
            refuse_non_ascii(item, path=f"{path}[{index}]")
        return
    if value is None:
        raise SpecError(
            f'{path} is null. Rule 5: absence is "", [] or false, never null — '
            "a missing key and an explicit null are two encodings of one fact "
            "and would produce two digests for it",
            code=PLAN_VALUE_REFUSED,
        )
    if isinstance(value, float):
        raise SpecError(
            f"{path} is a float. Rule 6: float repr is platform- and "
            "version-sensitive, so a duration serializing as 1.1 here and "
            "1.1000000000000001 elsewhere is a digest that disagrees with "
            "itself",
            code=PLAN_VALUE_REFUSED,
        )
    raise SpecError(
        f"{path} is a {type(value).__name__}, which a plan document cannot carry",
        code=PLAN_VALUE_REFUSED,
    )


def canonical_plan_bytes(document: Any, *, schema: str, path: str) -> bytes:
    """The exact bytes for ``document``, or refuse.

    ``schema`` is the ONE schema value this call will accept; anything else is
    refused, wrapper and wrong-plan-kind alike. ``path`` is the root label
    woven into a value refusal so an operator reads a path into their own
    document rather than a generic.
    """
    if not isinstance(document, dict):
        found_type = type(document).__name__
        raise SpecError(
            f"a {schema} document must be a JSON object, got {found_type}",
            code=PLAN_NOT_THIS_DOCUMENT,
        )
    found = document.get("schema")
    if found != schema:
        raise SpecError(
            f"this is not a {schema} document (schema {found!r}). The digest "
            "covers THIS document alone: hashing a wrapper that merely contains "
            "one is how Control's plan_digest and the Foundation's came to be "
            "permanently unequal while both looked correct, and hashing a "
            "DIFFERENT plan kind under this name would let one digest stand for "
            "two different acts",
            code=PLAN_NOT_THIS_DOCUMENT,
        )
    refuse_non_ascii(document, path=path)
    return json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
