"""S8b: the `verify` CLI re-renders each plan from the release checkout.

The supplied plan bytes are untrusted; the signed dispatch is the authority.
Synthetic keys only.
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

from tests.unit.test_lane3_offline_orchestrator import (
    World,
    canonical,
    world,  # noqa: F401  (fixture)
)

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import lane3_verify as verify  # noqa: E402
from dotmac_deployment_foundation.execution_plan_v3 import (  # noqa: E402
    canonical_execution_plan_v3_bytes,
)
from lane3_offline_orchestrator import JoinRefused  # noqa: E402

REVISION = "c" * 40


def refused(code: str, call, *args, **kwargs) -> None:
    with pytest.raises(JoinRefused) as caught:
        call(*args, **kwargs)
    assert str(caught.value) == f"offline-join-{code}"


# --- the re-render ------------------------------------------------------------


def test_a_faithful_plan_rerenders_byte_identically(world: World) -> None:  # noqa: F811
    for target in (world, world.neg):
        raw = target.plan.canonical_bytes()
        plan = verify.rerender_plan(raw, descriptor=world.spec)
        assert plan.canonical_bytes() == raw
        assert plan.digest() == target.plan.digest()


def _tampered(world: World, **changes) -> bytes:  # noqa: F811
    document = world.plan.as_document()
    document.update(changes)
    return canonical_execution_plan_v3_bytes(document)


@pytest.mark.parametrize(
    "changes",
    [
        {"descriptor_digest": "sha256:" + "9" * 64},
        {"strategy": "something-else"},
        {"foundation_version": "0.0.0"},
    ],
)
def test_a_plan_whose_derived_parts_differ_is_a_rerender_mismatch(
    world: World,  # noqa: F811
    changes,
) -> None:
    raw = _tampered(world, **changes)
    with pytest.raises(JoinRefused) as caught:
        verify.rerender_plan(raw, descriptor=world.spec)
    assert str(caught.value) in {
        "offline-join-verify-plan-rerender-mismatch",
        "offline-join-verify-plan-unrenderable",
    }


def test_a_non_canonical_serialization_is_a_rerender_mismatch(world: World) -> None:  # noqa: F811
    raw = json.dumps(world.plan.as_document(), indent=2).encode()
    refused(
        "verify-plan-rerender-mismatch",
        verify.rerender_plan,
        raw,
        descriptor=world.spec,
    )


@pytest.mark.parametrize(
    "raw",
    [b"", b"{}", b"[]", b"not json", b'{"schema":"FoundationExecutionPlanV3"}'],
)
def test_an_unrenderable_plan_is_refused(world: World, raw: bytes) -> None:  # noqa: F811
    refused(
        "verify-plan-unrenderable", verify.rerender_plan, raw, descriptor=world.spec
    )


def test_the_subject_is_taken_from_the_bytes_but_the_bytes_must_still_match(
    world: World,  # noqa: F811
) -> None:
    other = _tampered(world, host_id="fleet-host-9")
    plan = verify.rerender_plan(other, descriptor=world.spec)
    assert plan.host_id == "fleet-host-9"
    assert plan.canonical_bytes() == other


# --- the digest is the signed one ---------------------------------------------


def test_the_rerendered_digest_equals_the_signed_dispatch(world: World) -> None:  # noqa: F811
    plan = verify.rerender_plan(world.plan.canonical_bytes(), descriptor=world.spec)
    verify.require_plan_digest_signed(
        plan, canonical(world.export), [world.cp_identity]
    )


def test_a_plan_the_dispatch_did_not_sign_is_refused(world: World) -> None:  # noqa: F811
    other = verify.rerender_plan(
        world.neg.plan.canonical_bytes(), descriptor=world.spec
    )
    refused(
        "verify-plan-digest-mismatch",
        verify.require_plan_digest_signed,
        other,
        canonical(world.export),
        [world.cp_identity],
    )


def test_an_unpinned_signer_is_refused_before_the_digest_is_compared(
    world: World,  # noqa: F811
) -> None:
    plan = verify.rerender_plan(world.plan.canonical_bytes(), descriptor=world.spec)
    refused(
        "dispatch-signer-unpinned",
        verify.require_plan_digest_signed,
        plan,
        canonical(world.export),
        [],
    )


# --- the checkout ----------------------------------------------------------------


DESCRIPTOR_TEXT = (REPO / "scripts/exposure-rehearsal/product.toml").read_text("utf-8")


def _git(text: str = DESCRIPTOR_TEXT, calls: list | None = None):
    def run(_checkout, *args: str) -> str:
        if calls is not None:
            calls.append(args)
        return text

    return run


def test_the_descriptor_is_read_from_the_git_object_at_the_release_revision() -> None:
    calls: list = []
    spec = verify.load_descriptor(REPO, REVISION, git=_git(calls=calls))
    assert spec.product == "lane3_exposure"
    assert calls == [("show", f"{REVISION}:{verify.DESCRIPTOR_RELPATH}")]


def test_the_worktree_is_never_read(tmp_path) -> None:
    """A checkout with no descriptor file still answers from the git object."""
    spec = verify.load_descriptor(tmp_path, REVISION, git=_git())
    assert spec.product == "lane3_exposure"


@pytest.mark.parametrize("revision", ["", "c" * 39, "C" * 40, "g" * 40, "HEAD"])
def test_a_malformed_release_revision_is_refused(revision: str) -> None:
    refused(
        "verify-revision-invalid",
        verify.load_descriptor,
        REPO,
        revision,
        git=_git(),
    )


def test_a_revision_git_cannot_show_is_refused() -> None:
    def unavailable(_checkout, *_args: str) -> str:
        verify._refuse("verify-checkout-unavailable")
        raise AssertionError

    refused(
        "verify-checkout-unavailable",
        verify.load_descriptor,
        REPO,
        REVISION,
        git=unavailable,
    )


@pytest.mark.parametrize("text", ["", "not = [valid", "schema = 'nope'"])
def test_an_unreadable_descriptor_is_refused(text: str) -> None:
    refused(
        "verify-descriptor-unreadable",
        verify.load_descriptor,
        REPO,
        REVISION,
        git=_git(text),
    )


# --- the command line --------------------------------------------------------------


def test_every_input_of_both_targets_is_required() -> None:
    with pytest.raises(SystemExit) as caught:
        verify.main([])
    assert caught.value.code == 2
    parser = verify.build_parser()
    required = {a.dest for a in parser._actions if a.required}
    for role in ("positive", "negative"):
        for name in (
            "export",
            "admission",
            "plan",
            "presentation_key",
            "anchor_version",
            "refresh_digest",
            "e10_receipt_digest",
        ):
            assert f"{role}_{name}" in required
    assert {"receipt", "ciphertext", "signing_key", "cp_identity"} <= required


def test_a_refusal_prints_only_its_code(monkeypatch, capsys) -> None:
    def refuse(_args):
        raise JoinRefused("offline-join-verify-plan-rerender-mismatch")

    monkeypatch.setattr(verify, "run", refuse)
    monkeypatch.setattr(verify, "build_parser", lambda: _Parser())
    assert verify.main([]) == 2
    assert (
        capsys.readouterr().err
        == "refused: offline-join-verify-plan-rerender-mismatch\n"
    )


class _Parser:
    def parse_args(self, _argv):
        return object()
