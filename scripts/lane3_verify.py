#!/usr/bin/env python3
"""The full E9 `verify` CLI (S8b): two targets, re-rendered plans, signed result.

Runs only under offline custody (Michael's session), outside every runner. For
each target the SIGNED dispatch from a pinned CP identity is the authority. The
canonical V3 plan bytes the launcher retained are an UNTRUSTED file; the CLI:

1. extracts the prestate (`HostPrestateV1.from_document`) and the subject fields;
2. re-renders through Foundation's public functions, in order: `build_plan`,
   `render_execution_plan`, `render_execution_plan_v2(exposure_reconciliations_
   for_spec)`, `render_execution_plan_v3`, with the descriptor from the
   verifier's OWN checkout at `release_revision`;
3. refuses unless the re-render is byte-identical to the supplied bytes and its
   digest equals the signed `execution_plan_digest`.

Foundation has no V3 parser, so re-rendering is the only way to a typed plan, and
a second parser would be a second answer. Then `verify_offline` (two targets) and
`sign_result`. Refusals print a stable code and never supplied text; nothing
secret is read except through the age tool and the signing key path, both named
by path.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any, Final

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from dotmac_deployment_foundation.engine.plan import build_plan
from dotmac_deployment_foundation.execution_plan import (
    HostPrestateV1,
    render_execution_plan,
)
from dotmac_deployment_foundation.execution_plan_v2 import render_execution_plan_v2
from dotmac_deployment_foundation.execution_plan_v3 import (
    EXECUTION_PLAN_V3_SCHEMA,
    FoundationExecutionPlanV3,
    render_execution_plan_v3,
)
from dotmac_deployment_foundation.rehearsal import ExecutionRunBindingV1
from dotmac_deployment_foundation.spec import ProductDeploymentSpec
from lane3_cp6_export import (
    Cp6ExportRefused,
    PinnedIdentity,
    parse_cp6_export,
    verify_dispatch,
)
from lane3_offline_orchestrator import (
    JoinRefused,
    NegativeTargetInputs,
    PresentationKeyAnchor,
    age_decryptor,
    sign_result,
    verify_offline,
)
from lane3_offline_result import OfflineResultRefused, parse_policy
from lane3_plan_v3 import exposure_reconciliations_for_spec

#: The descriptor inside the release checkout the plans are re-rendered from.
DESCRIPTOR_RELPATH: Final = "scripts/exposure-rehearsal/product.toml"
_MAX_INPUT_BYTES: Final = 64 * 1024 * 1024
_REVISION_LEN: Final = 40
_GIT: Final = "/usr/bin/git"
_TARGETS: Final = ("positive", "negative")

__all__ = [
    "DESCRIPTOR_RELPATH",
    "load_descriptor",
    "main",
    "rerender_plan",
    "require_plan_digest_signed",
]


def _refuse(code: str) -> None:
    raise JoinRefused(f"offline-join-{code}")


# --- the checkout ---------------------------------------------------------------


def _git(checkout: pathlib.Path, *args: str) -> str:
    try:
        done = subprocess.run(
            [_GIT, "-C", str(checkout), *args],
            capture_output=True,
            timeout=60,
            check=False,
            shell=False,
            env={"LC_ALL": "C", "PATH": "/usr/bin:/bin", "TZ": "UTC"},
        )
    except (OSError, subprocess.TimeoutExpired):
        _refuse("verify-checkout-unavailable")
    if done.returncode != 0:
        _refuse("verify-checkout-unavailable")
    try:
        return done.stdout.decode("utf-8")
    except UnicodeDecodeError:
        _refuse("verify-descriptor-unreadable")
    raise AssertionError  # pragma: no cover


def load_descriptor(
    checkout: pathlib.Path,
    revision: str,
    *,
    git: Callable[..., str] = _git,
) -> ProductDeploymentSpec:
    """The descriptor as committed AT `revision`, via `git show <rev>:<path>`.

    The git object at the release revision is read directly, so neither the
    working tree nor its state is trusted (an unrelated checkout of another
    revision, or a locally edited file, cannot change the answer).
    """
    if (
        type(revision) is not str
        or len(revision) != _REVISION_LEN
        or any(c not in "0123456789abcdef" for c in revision)
    ):
        _refuse("verify-revision-invalid")
    text = git(checkout, "show", f"{revision}:{DESCRIPTOR_RELPATH}")
    try:
        return ProductDeploymentSpec.loads(text, source="<release-revision>")
    except Exception:
        _refuse("verify-descriptor-unreadable")
    raise AssertionError  # pragma: no cover


# --- the re-render --------------------------------------------------------------

_SUBJECT_FIELDS: Final = (
    "candidate_wheel_digest",
    "target_id",
    "controller_ssh_fingerprint",
    "host_id",
    "host_incarnation",
    "host_enrolment_ref",
)


def rerender_plan(
    plan_bytes: bytes, *, descriptor: ProductDeploymentSpec
) -> FoundationExecutionPlanV3:
    """Re-render the supplied plan from the descriptor; refuse unless identical.

    Only the prestate, `target`, `operation`, the application profile digest and
    the subject fields are read from the untrusted bytes; everything else (the
    steps, the descriptor digest, the exposure reconciliations) is DERIVED here, so
    a plan whose derived parts differ cannot round-trip to the same bytes.
    """
    try:
        if type(plan_bytes) is not bytes or not 0 < len(plan_bytes) <= _MAX_INPUT_BYTES:
            raise ValueError
        document = json.loads(plan_bytes.decode("utf-8"))
        if type(document) is not dict or document.get("schema") != (
            EXECUTION_PLAN_V3_SCHEMA
        ):
            raise ValueError
        for name in ("target", "operation", "application_profile_digest"):
            if type(document[name]) is not str:
                raise ValueError
        subject: dict[str, Any] = {name: document[name] for name in _SUBJECT_FIELDS}
        base = render_execution_plan(
            descriptor,
            build_plan(descriptor),
            target=document["target"],
            operation=document["operation"],
            descriptor_digest=str(descriptor.to_canonical_document().sha256_digest()),
            prestate=HostPrestateV1.from_document(document["host_prestate"]),
            application_profile_digest=document["application_profile_digest"],
        )
        plan = render_execution_plan_v3(
            render_execution_plan_v2(
                base,
                exposure_reconciliations=exposure_reconciliations_for_spec(descriptor),
            ),
            **subject,
        )
    except Exception:
        _refuse("verify-plan-unrenderable")
    if plan.canonical_bytes() != plan_bytes:
        _refuse("verify-plan-rerender-mismatch")
    return plan


def require_plan_digest_signed(
    plan: FoundationExecutionPlanV3,
    export_bytes: bytes,
    pinned: Sequence[PinnedIdentity],
) -> None:
    """The re-rendered plan's digest equals the signed dispatch's."""
    try:
        export = parse_cp6_export(export_bytes)
        statement = verify_dispatch(export.dispatch, pinned)
    except Cp6ExportRefused as exc:
        raise JoinRefused(
            "offline-join-" + str(exc).removeprefix("cp6-export-")
        ) from None
    if statement.execution_plan_digest != str(plan.digest()):
        _refuse("verify-plan-digest-mismatch")


# --- the CLI --------------------------------------------------------------------


def _read(path: str) -> bytes:
    try:
        target = pathlib.Path(path)
        if target.stat().st_size > _MAX_INPUT_BYTES:
            raise OSError
        return target.read_bytes()
    except OSError:
        _refuse("verify-input-unreadable")
    raise AssertionError  # pragma: no cover


def _identity(text: str) -> PinnedIdentity:
    parts = text.split(":")
    if len(parts) != 3 or not all(parts):
        _refuse("verify-identity-invalid")
    return PinnedIdentity(parts[0], parts[1], parts[2])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lane3_verify", description=__doc__)
    add = parser.add_argument
    add("--release-checkout", required=True)
    add("--release-revision", required=True)
    add("--run-repository-id", type=int, required=True)
    add("--run-id", type=int, required=True)
    add("--run-attempt", type=int, required=True)
    add("--candidate-artifact-digest", required=True)
    add("--age-identity", required=True)
    add("--age-path", required=True)
    add("--age-sha256", required=True)
    add("--policy", required=True)
    add("--allowed-signers", required=True)
    add("--revoked-keys", required=True)
    add("--cp-identity", action="append", required=True)
    add("--candidate-root-fingerprint", required=True)
    add("--receipt", required=True)
    add("--ciphertext", required=True)
    add("--signing-key", required=True)
    add("--output-dir", required=True)
    for role in _TARGETS:
        for name in (
            "export",
            "admission",
            "candidate-envelope",
            "installed-envelope",
            "plan",
            "presentation-key",
            "anchor-key-id",
            "anchor-fingerprint",
            "anchor-host-id",
            "refresh-digest",
            "e10-receipt-digest",
        ):
            add(f"--{role}-{name}", required=True)
        add(f"--{role}-anchor-version", type=int, required=True)
    return parser


def _anchor(args: argparse.Namespace, role: str) -> PresentationKeyAnchor:
    return PresentationKeyAnchor(
        key_id=getattr(args, f"{role}_anchor_key_id"),
        key_fingerprint=getattr(args, f"{role}_anchor_fingerprint"),
        host_id=getattr(args, f"{role}_anchor_host_id"),
        record_version=getattr(args, f"{role}_anchor_version"),
    )


def run(args: argparse.Namespace, *, now: datetime | None = None) -> list[pathlib.Path]:
    moment = now or datetime.now(UTC)
    descriptor = load_descriptor(
        pathlib.Path(args.release_checkout), args.release_revision
    )
    pinned = [_identity(item) for item in args.cp_identity]
    try:
        policy = parse_policy(
            _read(args.policy), _read(args.allowed_signers), _read(args.revoked_keys)
        )
    except OfflineResultRefused:
        _refuse("verify-policy-invalid")
    plans: dict[str, FoundationExecutionPlanV3] = {}
    exports: dict[str, bytes] = {}
    for role in _TARGETS:
        exports[role] = _read(getattr(args, f"{role}_export"))
        plans[role] = rerender_plan(
            _read(getattr(args, f"{role}_plan")), descriptor=descriptor
        )
        require_plan_digest_signed(plans[role], exports[role], pinned)

    def target(role: str) -> dict[str, Any]:
        return {
            "export_bytes": exports[role],
            "expected_e10_receipt_digest": getattr(args, f"{role}_e10_receipt_digest"),
            "candidate_envelope_bytes": _read(
                getattr(args, f"{role}_candidate_envelope")
            ),
            "installed_envelope_bytes": _read(
                getattr(args, f"{role}_installed_envelope")
            ),
            "host_admission_bytes": _read(getattr(args, f"{role}_admission")),
            "presentation_public_key_b64url": getattr(args, f"{role}_presentation_key"),
            "anchor": _anchor(args, role),
            "refresh_digest": getattr(args, f"{role}_refresh_digest"),
        }

    own = target("positive")
    negative = NegativeTargetInputs(
        execution_plan=plans["negative"], **target("negative")
    )
    verification = verify_offline(
        receipt_bytes=_read(args.receipt),
        ciphertext=_read(args.ciphertext),
        decrypt=age_decryptor(
            identity_path=pathlib.Path(args.age_identity),
            age_path=args.age_path,
            age_sha256=args.age_sha256,
        ),
        run=ExecutionRunBindingV1(
            repository_id=args.run_repository_id,
            run_id=args.run_id,
            run_attempt=args.run_attempt,
        ),
        release_revision=args.release_revision,
        candidate_artifact_digest=args.candidate_artifact_digest,
        execution_plan=plans["positive"],
        descriptor=descriptor,
        export_bytes=own["export_bytes"],
        expected_e10_receipt_digest=own["expected_e10_receipt_digest"],
        pinned_cp_identities=pinned,
        pinned_candidate_root_fingerprint=args.candidate_root_fingerprint,
        candidate_envelope_bytes=own["candidate_envelope_bytes"],
        installed_envelope_bytes=own["installed_envelope_bytes"],
        host_admission_bytes=own["host_admission_bytes"],
        presentation_public_key_b64url=own["presentation_public_key_b64url"],
        anchor=own["anchor"],
        refresh_digest=own["refresh_digest"],
        negative=negative,
        policy=policy,
        now=moment,
    )
    signature = sign_result(
        verification,
        key_path=pathlib.Path(args.signing_key),
        policy=policy,
        now=moment,
    )
    directory = pathlib.Path(args.output_dir)
    written: list[pathlib.Path] = []
    for name, payload in (
        ("offline-result.json", verification.result_bytes),
        ("offline-result.json.sig", signature),
    ):
        destination = directory / name
        try:
            descriptor_fd = os.open(
                destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            with os.fdopen(descriptor_fd, "wb") as stream:
                stream.write(payload)
        except OSError:
            _refuse("verify-output-unavailable")
        written.append(destination)
    return written


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        written = run(args)
    except JoinRefused as refusal:
        print(f"refused: {refusal}", file=sys.stderr)
        return 2
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
