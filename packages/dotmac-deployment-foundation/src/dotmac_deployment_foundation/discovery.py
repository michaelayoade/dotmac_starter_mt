"""Locating THE one declared thing of a kind, and refusing every ambiguous shape.

## Why this is a shared core and not a pattern to follow

`discover_bindings` worked. The temptation, when a second kind of declaration
needs discovering, is to write a second function shaped like it — the two are
forty lines each and obviously parallel. That is a SECOND AUTHORITY over one
question, and the failure mode is not that the copy is wrong on the day it is
written. **Copies agree right up until they don't.** One gets a refusal
tightened, or a message improved, or a new ambiguous shape closed, and the other
does not; the divergence is invisible because both still look correct, and it
surfaces as an environment that one discoverer refuses and the other admits.

This repository fixed exactly that defect twice in one evening at the layer
below — `AuthorizationReceipt.__post_init__` restating `("deploy", "rollback")`
instead of reading `OPERATIONS`, so widening the vocabulary left one layer
saying three and the next saying two. A restatement of a rule is a copy of it.

So the five refusals live HERE, once, and every consumer gets all five by
construction rather than by discipline.

## The five refusals, and why each is a refusal rather than a skip

A mechanism that makes an ADMIT representable also makes an UNINTENDED admit
representable, so the refusals carry equal weight with the admit:

1. **Two or more declarations** — refuse, naming every declarer. Which one wins
   must never be an iteration-order accident, and a second declaration is either
   a stale install or an attempt to swap behaviour under an unchanged command
   line. Neither is a thing to pick a winner from.
2. **A declaration that fails to import** — refuse. Skipping it turns a broken
   deployment environment into a quieter one.
3. **A factory that raises** — refuse, naming the distribution. Same reason.
4. **A result of the wrong type** — refuse. A duck-typed look-alike with the
   right attributes is exactly what a typed contract exists to reject.
5. **A name that disagrees with the entry point's** — refuse. The entry point
   NAME is what a caller could see before anything was imported; an object
   answering to a different name was selected by nobody.

**Zero declarations is not a refusal.** It returns None, and the caller's own
refusals stand — now able to say what was looked for.

## `name_of` has no default, deliberately

Refusal 5 can only be checked if the discovered object can be asked what it
calls itself, and an OPTIONAL extractor would let a consumer acquire this core
and silently never exercise that refusal — gaining a caller that never proves
it, which is the precise failure the extraction exists to prevent. So it is
required. A consumer whose type genuinely has no name has not finished designing
its contract.

## What discovery is NOT

It authenticates nothing and decides nothing. Python plugins are trusted
in-process code (the repository's standing rule): they are installed and
verified by the supply chain at build and deploy time. This LOCATES the one
declaration the environment was built to carry.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import Any, Final, TypeVar

from .errors import PreconditionFailed

_T = TypeVar("_T")

__all__ = [
    "DISCOVERY_AMBIGUOUS",
    "DISCOVERY_FACTORY_RAISED",
    "DISCOVERY_IMPORT_FAILED",
    "DISCOVERY_NAME_MISMATCH",
    "DISCOVERY_REFUSALS",
    "DISCOVERY_WRONG_TYPE",
    "declared_names",
    "discover_one",
]

#: Stable identifiers for the five refusals. Assert these; read the prose. A
#: core with five refusals has to be testable on WHICH one fired, and `match=`
#: on a sentence makes the sentence the contract — after which the message
#: cannot be improved without breaking a test, and a test that only ever saw one
#: wording cannot tell two refusals apart.
DISCOVERY_AMBIGUOUS: Final = "discovery.ambiguous"
DISCOVERY_IMPORT_FAILED: Final = "discovery.import_failed"
DISCOVERY_FACTORY_RAISED: Final = "discovery.factory_raised"
DISCOVERY_WRONG_TYPE: Final = "discovery.wrong_type"
DISCOVERY_NAME_MISMATCH: Final = "discovery.name_mismatch"

#: Every refusal this core makes. Consumers are exercised against ALL of them —
#: `test_deployment_foundation_discovery.py` derives the consumer list from the
#: package itself, so a new consumer that is not proven against each of these
#: fails the build rather than inheriting them on trust.
DISCOVERY_REFUSALS: Final[tuple[str, ...]] = (
    DISCOVERY_AMBIGUOUS,
    DISCOVERY_IMPORT_FAILED,
    DISCOVERY_FACTORY_RAISED,
    DISCOVERY_WRONG_TYPE,
    DISCOVERY_NAME_MISMATCH,
)


def _distribution_of(entry: Any) -> str:
    dist = getattr(entry, "dist", None)
    name = getattr(dist, "name", None)
    return str(name) if name else "<unknown distribution>"


def _declared(group: str, entries: Iterable[Any] | None) -> Sequence[Any]:
    if entries is not None:
        return list(entries)
    import importlib.metadata

    return list(importlib.metadata.entry_points(group=group))


def declared_names(
    group: str, *, entries: Iterable[Any] | None = None
) -> tuple[str, ...]:
    """Every declared name in ``group``, WITHOUT importing any of it.

    Metadata only, so a caller can offer a menu — or report what it looked for —
    without executing assembly code on a validate or a dry run. A name is
    metadata; a load is an import. Duplicates collapse here and are refused by
    :func:`discover_one`, because a menu is not a gate.
    """
    return tuple(sorted({str(entry.name) for entry in _declared(group, entries)}))


def discover_one(
    *,
    group: str,
    expected_type: type[_T],
    subject: str,
    name_of: Callable[[_T], str],
    entries: Iterable[Any] | None = None,
) -> _T | None:
    """Locate THE declaration in ``group``, or None, or refuse.

    ``subject`` is the human phrase for what is being located ("execution
    bindings"), woven into every refusal so an operator reads a sentence about
    their deployment rather than about a generic. ``name_of`` extracts the name
    the discovered object answers to, for refusal 5; it is required, and this
    module's docstring says why.
    """
    declared = _declared(group, entries)
    if not declared:
        return None
    if len(declared) > 1:
        listed = ", ".join(
            sorted(f"{_distribution_of(entry)}:{entry.name}" for entry in declared)
        )
        raise PreconditionFailed(
            f"{len(declared)} distributions declare {group!r} entry points "
            f"({listed}). One environment carries one set of {subject}; a "
            "second declaration is either a stale install or an attempt to "
            "swap behaviour under an unchanged command line, and neither is a "
            "thing to pick a winner from. Remove all but one and redeploy",
            code=DISCOVERY_AMBIGUOUS,
        )

    entry = declared[0]
    try:
        factory = entry.load()
    except Exception as exc:
        raise PreconditionFailed(
            f"the {subject} declared by {_distribution_of(entry)} "
            f"({group}:{entry.name}) failed to import: {exc}. A broken "
            "distribution is a broken deployment environment, not a thing to "
            "skip",
            code=DISCOVERY_IMPORT_FAILED,
        ) from exc
    try:
        found = factory()
    except Exception as exc:
        raise PreconditionFailed(
            f"the {subject} factory from {_distribution_of(entry)} "
            f"({group}:{entry.name}) raised: {exc}",
            code=DISCOVERY_FACTORY_RAISED,
        ) from exc
    if not isinstance(found, expected_type):
        raise PreconditionFailed(
            f"the entry point {group}:{entry.name} from "
            f"{_distribution_of(entry)} returned {type(found).__name__}, not "
            f"{expected_type.__name__}. The typed object is the contract; a "
            "look-alike is exactly what the type exists to refuse",
            code=DISCOVERY_WRONG_TYPE,
        )
    answers_to = str(name_of(found))
    if answers_to != str(entry.name):
        raise PreconditionFailed(
            f"the entry point is named {str(entry.name)!r} but the {subject} it "
            f"declares answer to {answers_to!r}. The entry point name is what "
            "was visible before anything was imported; a declaration answering "
            "to a different name was selected by nobody",
            code=DISCOVERY_NAME_MISMATCH,
        )
    return found
