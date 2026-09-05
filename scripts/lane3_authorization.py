#!/usr/bin/env python3
"""Lane 3's authorization standing — attested, refused, or unanswerable.

## The defect this closes

`exposure_rehearsal_runner.py` took `--authorization-run` and
`--authorization-doc-digest` as `workflow_dispatch` TEXT and treated the pair as
the authorization it was executing under. The only thing ever compared was
`lease.covers(authorization_run_id=...)`, which is a string equality against a
lease record the same operator can write, and the digest was compared to nothing
at all until `build_receipt` asserted it equalled the descriptor digest — a
value the caller computes locally from a file in the repository.

Nothing imported `provenance.py` or `authorization.py`. No `VerifiedAuthorization`
was ever constructed and no `ExecutionGrant` ever existed. So two matching
strings LOOKED like a binding while no authorization existed anywhere, and Lane
3 could be driven green on a fabricated run id and a digest anyone could compute.
The programme's governing rule is that the Foundation cannot self-authorize, and
this is the step that was meant to enforce it.

## What this module does instead, and what it deliberately does NOT do

It does not build the verification chain. That chain cannot be built today —
:data:`PRECONDITIONS` enumerates exactly why, and every entry there was measured
rather than assumed. Building a plausible-looking substitute would be worse than
the gap, because a weak verifier reads as coverage.

What it does is make the unverified case UNREPRESENTABLE. The caller-supplied
run id and document digest are not parameters of
:func:`establish_authorization` at all, so no code path can promote them into
proof by forgetting a check. The only route to
an `ExecutionGrant` is the Foundation's own: an injected `AuthorizationVerifier`
attests the signed document, `verify_authorization` turns the attested bytes
into `VerifiedAuthorization`, and `authorize` binds it to this descriptor, this
target and this operation. No verifier, no grant; no grant, no rehearsal.

## Three answers, and a refusal must never read as indeterminate

The same three-status rule `scripts/check_allocation_serialized.py` states, and
for the same reason: an unanswerable question reported as a pass is how a gate
stops being one, and a refusal reported as unanswerable is how a real finding
becomes something to wait out.

* :attr:`Standing.ATTESTED` — exit 0. A verifier attested the document and the
  terms bind to the descriptor, target and operation in hand.
* :attr:`Standing.UNATTESTABLE` — exit 1, a VIOLATION. The environment could
  answer the question and the answer is no: material was offered and did not
  attest, or the run cited an authorization it could not show while a verifier
  stood ready to check one.
* :attr:`Standing.UNANSWERABLE` — exit 2, INDETERMINATE. No verifier is
  installed, so nothing in this process can tell an authentic authorization from
  fabricated text. This is not "unauthorized" and it is not "fine"; it is the
  honest report that the question has no answer here, and it is the answer this
  repository gets today.

The ordering below is what keeps the two apart. The verifier is looked for
FIRST, before the document is read: with nothing able to attest, a missing
document is not a finding about the caller, and reporting one would blame the
operator for an absence the environment owns.

## Why the preconditions live in code

`docs/inventories/deployment-exposure-rehearsal.md` has recorded since
2026-08-30 that "nothing can issue the authorization the rehearsal binds to"
(prerequisite 2) — and the runner shipped accepting the text anyway. Prose that
the code contradicts is how "blocked" quietly becomes "green". Here the list is
the thing the refusal is built from: a refusal names the unmet entries by code,
and an entry that becomes satisfiable is observed rather than re-argued.
"""

from __future__ import annotations

import argparse
import dataclasses
import enum
import json
import pathlib
import sys
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any, Final

#: The group an assembly declares its bindings in. Restated here as a LITERAL
#: on purpose: this module must be able to say what it looked for even when the
#: installed Foundation is too old to define the constant, which is precondition
#: `published_foundation`. The architecture test compares it against the
#: package's own value so the two cannot drift.
ENTRY_POINT_GROUP: Final = "dotmac_deployment_foundation.execution_bindings"

#: The operation Lane 3 rehearses. Named rather than defaulted at the call site,
#: because `authorize()` refuses a grant for a different one and a silent
#: default is how a rollback approval comes to cover a deploy.
LANE_OPERATION: Final = "deploy"

EXIT_ATTESTED, EXIT_VIOLATION, EXIT_INDETERMINATE = 0, 1, 2


@dataclasses.dataclass(frozen=True, slots=True)
class Precondition:
    """One thing that must exist before Lane 3's authorization can be verified.

    ``observable`` is the honest half. An entry a machine can check here is
    checked, and its absence is what a refusal cites; an entry nothing in this
    repository can decide is marked False and stays a STATED requirement. A
    stated requirement is not an enforced one, and conflating the two is how a
    checklist comes to be read as coverage (ADR-0018).
    """

    code: str
    statement: str
    owner: str
    evidence: str
    observable: bool

    def render(self) -> str:
        seen = "observable here" if self.observable else "STATED, not enforced"
        return (
            f"  - {self.code} ({seen})\n"
            f"      {self.statement}\n"
            f"      owner: {self.owner}\n"
            f"      measured: {self.evidence}"
        )


#: THE list. Every entry was measured on 2026-09-05 against this tree and the
#: repositories under `management/`; none is an inference from the others.
PRECONDITIONS: Final[tuple[Precondition, ...]] = (
    Precondition(
        code="verifier_implementation",
        statement=(
            "Something implements "
            "`dotmac_deployment_foundation.provenance.AuthorizationVerifier` "
            "(one method, `attest(material) -> Mapping`) carrying Platform CP's "
            "trust roots. The Foundation ships none by design — it declares "
            "zero runtime dependencies (ADR-0070) and a weak stdlib substitute "
            "would read as coverage."
        ),
        owner=(
            "the assembly — the only party that legitimately depends on both "
            "Control and the Foundation"
        ),
        evidence=(
            "no implementation exists in any repository under management/; the "
            "Protocol has no implementor fleet-wide"
        ),
        observable=True,
    ),
    Precondition(
        code="bindings_entry_point",
        statement=(
            f"That implementation is declared as ONE {ENTRY_POINT_GROUP!r} "
            "entry point and installed into the same environment the runner "
            "executes in — the isolated candidate venv, not the checkout."
        ),
        owner="the assembly's bindings distribution, installed by the supply chain",
        evidence=f"zero declarations of {ENTRY_POINT_GROUP!r} exist fleet-wide",
        observable=True,
    ),
    Precondition(
        code="signed_document_reaches_the_runner",
        statement=(
            "The runner receives the signed authorization DOCUMENT, not only a "
            "digest of it. A verifier attests BYTES; a digest of bytes nobody "
            "holds cannot be attested, only compared against a digest the same "
            "caller chose."
        ),
        owner="Platform CP, which issues it, and the workflow that carries it",
        evidence=(
            "`workflow_dispatch` supplied `authorization_doc_digest` as free "
            "text and the document itself never reached the runner; "
            "`--authorization-document` was added by this change and there is "
            "nothing to put in it yet"
        ),
        observable=True,
    ),
    Precondition(
        code="issuer_to_verifier_translation",
        statement=(
            "Something translates Control's issued statement into the document "
            "`AuthorizationReceipt.from_document` accepts. Control issues "
            "`AuthorizationStatementV2` (schema `dotmac.deployment-authorization` "
            "v2); the receipt requires 14 named keys and REFUSES unknown ones, "
            "so the two shapes cannot meet without a declared translator."
        ),
        owner="the assembly; neither side may normalize the other's document",
        evidence=(
            "measured against dotmac-deployment-control at the peeled a11 tag: "
            "~18 keys the receipt would reject as unknown, 5 required keys the "
            "statement does not carry, and `control_plan_digest` has no "
            "producer on either side"
        ),
        observable=False,
    ),
    Precondition(
        code="published_foundation",
        statement=(
            "The bytes Lane 3 installs contain the verifying code — "
            "`provenance.verify_authorization`, `authorization.authorize` and "
            "`execution_bindings.discover_bindings`. Lane 3 installs a recorded "
            "`CandidateArtifact.v1` wheel, so the verifier has to be inside "
            "that wheel rather than in the checkout beside it."
        ),
        owner="the Foundation's release pipeline",
        evidence=(
            "the verifying code lives in dotmac-deployment-foundation 0.4.0a1, "
            "which has never been built and is unpublished (version.py; "
            "docs/inventories/declared-publication-baseline.json)"
        ),
        observable=True,
    ),
    Precondition(
        code="middle_term_is_the_execution_plan_digest",
        statement=(
            "Gate item 9's middle term is `ExecutionPlanDigestV1` — the plan "
            "the Foundation renders and Control merely freezes (AGENTS.md rule "
            "49). It is explicitly NOT the descriptor digest and NOT the "
            "authorization-envelope digest, so an attested receipt alone does "
            "not restore the term: `rehearsal.build_receipt` still asserts "
            "`authorization_document_digest == descriptor_digest == "
            "execution_report_digest`, which is the degenerate two-term shape "
            "its own docstring warns about."
        ),
        owner=(
            "the Foundation (`rehearsal.py`) together with this lane's runner; "
            "NOT repaired by this change"
        ),
        evidence=(
            "`build_receipt(require_same_digest)` forces the three terms equal, "
            "so the middle term can only ever be the descriptor digest"
        ),
        observable=False,
    ),
)

_BY_CODE: Final[dict[str, Precondition]] = {p.code: p for p in PRECONDITIONS}


class Standing(enum.Enum):
    """What can be established about this run's authorization. Closed."""

    ATTESTED = "attested"
    UNATTESTABLE = "unattestable"
    UNANSWERABLE = "unanswerable"

    @property
    def exit_status(self) -> int:
        """0 attested / 1 violation / 2 indeterminate — never collapsed.

        The same statuses `check_allocation_serialized.py` uses, and the same
        rule: an indeterminate answer is not a pass, and a violation is not an
        indeterminate answer.
        """
        if self is Standing.ATTESTED:
            return EXIT_ATTESTED
        if self is Standing.UNATTESTABLE:
            return EXIT_VIOLATION
        return EXIT_INDETERMINATE


class AuthorizationUnverifiable(Exception):
    """Lane 3's authorization could not be established, and why.

    Deliberately NOT a `DeploymentFoundationError` subclass at definition time:
    this module must be importable and must be able to REFUSE even when the
    installed Foundation is too old to import from, which is the
    `published_foundation` precondition. The runner translates it into its own
    `PreconditionUnfit` at the call site — where the fact "the host has not been
    touched" is known — rather than this module asserting it from a distance.
    """

    def __init__(
        self, standing: Standing, reason: str, unmet: Iterable[str] = ()
    ) -> None:
        self.standing = standing
        self.reason = reason
        self.unmet: tuple[str, ...] = tuple(unmet)
        super().__init__(self.render())

    @property
    def exit_status(self) -> int:
        return self.standing.exit_status

    def render(self) -> str:
        cited = [_BY_CODE[code] for code in self.unmet if code in _BY_CODE]
        lines = [
            f"Lane 3 authorization is {self.standing.value}: {self.reason}",
            "",
            "Caller-supplied `--authorization-run` and any document digest are "
            "RECORDED, never believed. Matching text is not a binding, and this "
            "lane exists to prove the Foundation cannot authorize itself.",
        ]
        if cited:
            lines += ["", "What would have to exist:"]
            lines += [p.render() for p in cited]
        lines += [
            "",
            "The full list, including the entries nothing here can decide, is "
            "`PRECONDITIONS` in scripts/lane3_authorization.py.",
        ]
        return "\n".join(lines)


def _verifying_symbols() -> tuple[Any, Any, Any]:
    """The Foundation's verifying entry points, from the INSTALLED distribution.

    Imported here rather than at module scope so an installed Foundation that
    predates them produces a named, enumerated refusal instead of an
    `ImportError` at collection time. Which distribution answers is exactly the
    question `published_foundation` asks, and the import is how it is asked.
    """
    try:
        from dotmac_deployment_foundation.authorization import authorize
        from dotmac_deployment_foundation.execution_bindings import discover_bindings
        from dotmac_deployment_foundation.provenance import verify_authorization
    except ImportError as exc:
        raise AuthorizationUnverifiable(
            Standing.UNANSWERABLE,
            (
                "the installed dotmac-deployment-foundation does not carry the "
                f"verifying code ({exc}). Nothing here can attest an "
                "authorization, so nothing here can tell one from a fabrication"
            ),
            unmet=("published_foundation",),
        ) from exc
    return authorize, discover_bindings, verify_authorization


def _find_verifier(discover_bindings: Any, entries: Iterable[Any] | None) -> Any:
    """The installed `AuthorizationVerifier`, or an UNANSWERABLE refusal.

    Looked for BEFORE the document is read. With nothing able to attest, a
    missing document says nothing about the caller, and citing it would report
    an environment gap as an operator error.
    """
    try:
        bindings = discover_bindings(entries=entries)
    except Exception as exc:
        raise AuthorizationUnverifiable(
            Standing.UNANSWERABLE,
            (
                f"the execution bindings could not be resolved ({exc}). A "
                "broken deployment environment is not a quieter one"
            ),
            unmet=("bindings_entry_point",),
        ) from exc
    if bindings is None:
        raise AuthorizationUnverifiable(
            Standing.UNANSWERABLE,
            (
                f"no distribution declares {ENTRY_POINT_GROUP!r}, so this "
                "environment ships no AuthorizationVerifier and cannot "
                "distinguish a Platform CP authorization from text typed into "
                "a workflow_dispatch field"
            ),
            unmet=("verifier_implementation", "bindings_entry_point"),
        )
    verifier = getattr(bindings, "authorization_verifier", None)
    if verifier is None:
        raise AuthorizationUnverifiable(
            Standing.UNANSWERABLE,
            (
                f"the bindings declared by {ENTRY_POINT_GROUP!r} carry no "
                "authorization_verifier. Bindings that inject effects but no "
                "verifier leave this question exactly as unanswerable as none "
                "at all"
            ),
            unmet=("verifier_implementation",),
        )
    return verifier


def _read_document(path: str | pathlib.Path | None) -> Mapping[str, Any]:
    """The signed authorization document, as bytes a verifier can judge.

    Every failure here is a VIOLATION rather than indeterminate: a verifier is
    already in hand by the time this runs, so the environment could have
    answered the question and the run did not give it anything to answer with.
    """
    if not path:
        raise AuthorizationUnverifiable(
            Standing.UNATTESTABLE,
            (
                "a verifier is installed and no --authorization-document was "
                "supplied. The run cites an authorization it cannot show, and "
                "a run id plus a digest is a claim about a document rather "
                "than the document"
            ),
            unmet=("signed_document_reaches_the_runner",),
        )
    try:
        raw = pathlib.Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise AuthorizationUnverifiable(
            Standing.UNATTESTABLE,
            f"the authorization document at {path} could not be read ({exc})",
            unmet=("signed_document_reaches_the_runner",),
        ) from exc
    try:
        document = json.loads(raw)
    except ValueError as exc:
        raise AuthorizationUnverifiable(
            Standing.UNATTESTABLE,
            f"the authorization document at {path} is not valid JSON ({exc})",
            unmet=("signed_document_reaches_the_runner",),
        ) from exc
    if not isinstance(document, Mapping):
        raise AuthorizationUnverifiable(
            Standing.UNATTESTABLE,
            (
                f"the authorization document at {path} is a "
                f"{type(document).__name__}, not an object"
            ),
            unmet=("signed_document_reaches_the_runner",),
        )
    return document


def establish_authorization(
    *,
    descriptor_digest: str,
    target: str,
    authorization_document: str | pathlib.Path | None,
    now: datetime,
    operation: str = LANE_OPERATION,
    entries: Iterable[Any] | None = None,
) -> Any:
    """The ONLY route from Lane 3's inputs to an `ExecutionGrant`.

    Note what is NOT a parameter: the authorization run id and the document
    digest the workflow dispatches. They are recorded on the receipt and
    compared against the lease elsewhere, and neither can reach this function,
    so no path through it can promote caller-supplied text into permission. That
    is the difference between a guard and a convention — a caller who wants an
    unverified grant has nothing to call.

    `now` is injected for the same reason `authorize` injects it: an expiry
    check that read its own clock could not be moved by a test, and an expiry
    nobody can test is a field.

    Returns the Foundation's `ExecutionGrant`. Raises
    :class:`AuthorizationUnverifiable` otherwise — never a sentinel, so a caller
    cannot treat "refused" as "granted" by forgetting to look.
    """
    authorize, discover_bindings, verify_authorization = _verifying_symbols()
    verifier = _find_verifier(discover_bindings, entries)
    document = _read_document(authorization_document)
    try:
        verified = verify_authorization(document, verifier=verifier)
    except Exception as exc:
        raise AuthorizationUnverifiable(
            Standing.UNATTESTABLE,
            (
                f"the authorization document was not attested ({exc}). The "
                "verifier judged the material and refused it; this is an "
                "answer, not an absence"
            ),
            unmet=("issuer_to_verifier_translation",),
        ) from exc
    try:
        return authorize(
            verified=verified,
            operation=operation,
            descriptor_digest=descriptor_digest,
            target=target,
            now=now,
        )
    except Exception as exc:
        raise AuthorizationUnverifiable(
            Standing.UNATTESTABLE,
            (
                f"the attested authorization does not cover this run ({exc}). "
                "The terms are authentic and they are for other work"
            ),
        ) from exc


def standing_of(**kwargs: Any) -> tuple[Standing, str]:
    """:func:`establish_authorization` reduced to its verdict, for the gate."""
    try:
        establish_authorization(**kwargs)
    except AuthorizationUnverifiable as refusal:
        return refusal.standing, refusal.render()
    return (
        Standing.ATTESTED,
        "Lane 3 authorization is attested: an installed verifier vouched for "
        "the signed document and its terms bind to this descriptor, target and "
        "operation.",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lane3_authorization.py",
        description=(
            "Report whether Lane 3's authorization can be verified in THIS "
            "environment. 0 attested / 1 violation / 2 indeterminate."
        ),
    )
    parser.add_argument("--descriptor", required=True, help="the rehearsal fixture")
    parser.add_argument("--target", required=True, help="the leased host")
    parser.add_argument(
        "--authorization-document",
        default="",
        help="the signed Platform CP authorization document (a path)",
    )
    parser.add_argument("--operation", default=LANE_OPERATION)
    arguments = parser.parse_args(argv)

    try:
        from dotmac_deployment_foundation.spec import ProductDeploymentSpec
    except ImportError as exc:
        print(
            AuthorizationUnverifiable(
                Standing.UNANSWERABLE,
                f"dotmac-deployment-foundation is not importable ({exc})",
                unmet=("published_foundation",),
            ).render(),
            file=sys.stderr,
        )
        return EXIT_INDETERMINATE

    spec = ProductDeploymentSpec.load(arguments.descriptor)
    standing, report = standing_of(
        descriptor_digest=spec.to_canonical_document().sha256_digest(),
        target=arguments.target,
        authorization_document=arguments.authorization_document,
        operation=arguments.operation,
        now=datetime.now(UTC),
    )
    stream = sys.stdout if standing is Standing.ATTESTED else sys.stderr
    print(report, file=stream)
    return standing.exit_status


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
