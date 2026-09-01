"""A consumed candidate is recorded by APPENDING, and the record is proved.

`scripts/foundation_candidate.py` writes `CandidateArtifact.v1`: what a
candidate's bytes are and how to fetch them again. Nothing wrote down what
became of one. `dotmac-deployment-foundation 0.3.0a2` is the case that made the
absence expensive — the wheel was built once, a post-freeze change then moved
the source under the same declared version, and the only places that fact lived
were a prose paragraph in a ledger `reason` field and a comment beside
``VERSION``. Neither is readable by a release lane, and both are one edit from
saying the opposite.

`CandidateDisposition.v1` (`scripts/foundation_disposition.py`) is the second
record. This module is its gate, and it checks two different kinds of claim:

* **the live state** — the log is internally sound, and the candidate receipt it
  dispositions is byte-for-byte the one that was recorded;
* **the detector's sensitivity** — each way the append-only property can be
  broken is planted and observed being refused.

The second half is the half that matters. A checker over one well-formed file
passes for the same reason a checker with its body deleted passes, and ADR-0018
is explicit that a guard with no observed refusal is an assumption wearing a
test's clothes.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess  # nosec B404 -- argv list, shell=False; git only
import tomllib
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "foundation_disposition.py"
LOG = PROJECT_ROOT / "docs" / "inventories" / "foundation-candidate-dispositions.json"
RECEIPT = PROJECT_ROOT / "docs" / "inventories" / "foundation-candidate-0.3.0a2.json"

#: The frozen candidate, as recorded on 2026-08-28 and never to be rebuilt. Kept
#: here as literals rather than read from the receipt: a test that derives its
#: expectations from the file it is checking compares the file with itself and
#: passes for every input, which is the shape of a check that has stopped
#: checking (`authorization.py` draws the same line about `receipt.target_ref`).
FROZEN = {
    "artifact_id": "9740182233",
    "filename": "dotmac_deployment_foundation-0.3.0a2-py3-none-any.whl",
    "repository": "michaelayoade/dotmac_starter_mt",
    "run_id": "33339810583",
    "sha256": "2a6e0ccd040b05ab602be4b439e48dd61188b3b71ed6e80ecc8a482e70d57443",
    "size_bytes": 263869,
    "source_sha": "e930f878ce400b766b4a50feb0369021a28ab2fa",
}

#: The post-freeze commit that made `0.3.0a2` name two contracts (#551).
INVALIDATING_COMMIT = "0f390a9aa93b0bb1cb78621ab1e9febc90bc48d2"


def _module():
    spec = importlib.util.spec_from_file_location("foundation_disposition", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DISPOSITION = _module()


def _log() -> dict[str, Any]:
    return json.loads(LOG.read_text(encoding="utf-8"))


# ── the live state ──────────────────────────────────────────────────────────


def test_the_live_disposition_log_is_internally_sound() -> None:
    problems = DISPOSITION.check(_log(), repo_root=PROJECT_ROOT)
    assert not problems, "candidate-disposition:\n" + "\n".join(problems)


def test_the_committed_history_only_ever_grew() -> None:
    """The append-only proof that a single commit cannot forge.

    The chain and the anchor are in-file, and an in-file property can always be
    rewritten by rewriting the whole file — with ONE entry the chain has nothing
    to link, so entry 1 is anchored to the receipt and otherwise unprotected
    against an in-place edit. Git history is the oracle that closes it: every
    past revision of the log must be a prefix of today's.

    Vacuous in the change that introduces the file, and honestly so — there is
    no past to contradict yet. `test_the_history_check_refuses_an_edited_prefix`
    is what proves the check will bite when there is.
    """
    assert not DISPOSITION.history_violations(repo_root=PROJECT_ROOT)


def test_the_candidate_receipt_is_byte_for_byte_unchanged() -> None:
    """`CandidateArtifact.v1` is preserved, and this is the evidence.

    Two digests rather than one. The canonical digest is what a chain should
    cover, because an innocent re-serialization must not read as tampering; the
    raw digest is what proves the checked-in FILE is untouched down to
    whitespace. A reformat passes the first and fails the second, which is the
    correct pair of answers.
    """
    entry = DISPOSITION.entries(_log())[0]
    canonical, raw = DISPOSITION.receipt_digests(RECEIPT)
    assert entry["receipt_digest"] == canonical
    assert entry["receipt_bytes_sha256"] == raw

    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    assert receipt["schema"] == "CandidateArtifact.v1"
    assert {key: receipt[key] for key in FROZEN} == FROZEN
    assert receipt["published"] is False
    assert receipt["tagged"] is False


def test_0_3_0a2_is_recorded_invalidated_and_unpublishable() -> None:
    entry = DISPOSITION.entries(_log())[0]
    assert entry["facility"] == "dotmac-deployment-foundation"
    assert entry["version"] == "0.3.0a2"
    assert entry["disposition"] == "invalidated"
    assert entry["publishable"] is False
    assert entry["invalidating_commit"] == INVALIDATING_COMMIT
    assert entry["artifact"] == FROZEN


def test_the_declared_version_is_no_longer_the_invalidated_one() -> None:
    """The half a disposition alone does not achieve.

    Recording that `0.3.0a2` is invalidated is worthless while the tree still
    declares `0.3.0a2`: the repository would go on offering an invalidated
    identity to every reader, and the ledger would sit beside it disagreeing.
    Moving the declaration is what makes the record true rather than merely
    filed.
    """
    pyproject = tomllib.loads(
        (
            PROJECT_ROOT
            / "packages"
            / "dotmac-deployment-foundation"
            / "pyproject.toml"
        ).read_text(encoding="utf-8")
    )
    declared = pyproject["tool"]["poetry"]["version"]
    invalidated = {
        entry["version"]
        for entry in DISPOSITION.entries(_log())
        if entry["facility"] == "dotmac-deployment-foundation"
        and not entry["publishable"]
    }
    assert declared not in invalidated, (
        f"packages/dotmac-deployment-foundation declares {declared!r}, which the "
        "disposition log records as unpublishable. A tree may not go on "
        "declaring an identity nobody is allowed to build"
    )


# ── the detector's sensitivity ──────────────────────────────────────────────


def _mutated(**overrides: Any) -> dict[str, Any]:
    log = copy.deepcopy(_log())
    log["entries"][0].update(overrides)
    return log


def _problems(log: dict[str, Any], *, expected_entries: int | None = None) -> list[str]:
    kwargs: dict[str, Any] = {"repo_root": PROJECT_ROOT}
    if expected_entries is not None:
        kwargs["expected_entries"] = expected_entries
    return DISPOSITION.check(log, **kwargs)


def test_an_entry_with_no_reason_is_refused() -> None:
    """Editing the JUDGEMENT is caught, because it moves the entry's digest.

    With a single entry nothing downstream links to it, so this fails through
    the count/field checks and the history oracle rather than the chain. That
    distinction is recorded rather than smoothed over: the chain protects
    entries that have successors, and history protects the newest one.
    """
    log = _mutated(reason="")
    assert _problems(log), "an entry with no reason must be refused"


def test_a_removed_entry_is_refused_by_the_ratchet() -> None:
    log = copy.deepcopy(_log())
    log["entries"] = []
    problems = _problems(log)
    assert any("EXPECTED_ENTRIES" in problem for problem in problems), problems


def test_an_appended_entry_is_refused_until_the_constant_moves() -> None:
    """The direction a one-directional ratchet misses (ADR-0018).

    An append is legitimate. It must not be SILENT: the constant moves in the
    same diff, so a reviewer sees a new disposition arriving rather than
    inferring it from a file that grew.
    """
    log = copy.deepcopy(_log())
    second = copy.deepcopy(log["entries"][0])
    second["sequence"] = 2
    second["previous_digest"] = DISPOSITION.digest_of(log["entries"][0])
    log["entries"].append(second)
    problems = _problems(log)
    assert any("EXPECTED_ENTRIES" in problem for problem in problems), problems


def test_a_broken_chain_link_is_refused() -> None:
    log = copy.deepcopy(_log())
    second = copy.deepcopy(log["entries"][0])
    second["sequence"] = 2
    second["previous_digest"] = "sha256:" + "0" * 64
    log["entries"].append(second)
    problems = _problems(log, expected_entries=2)
    assert any("chains to" in problem for problem in problems), problems


def test_a_first_entry_not_anchored_to_its_receipt_is_refused() -> None:
    """The anchor, planted. A zero genesis would have been accepted by a chain
    check and would have proved nothing about the receipt."""
    log = _mutated(previous_digest="sha256:" + "0" * 64)
    problems = _problems(log)
    assert any("first entry" in problem for problem in problems), problems


def test_a_receipt_digest_that_does_not_match_the_file_is_refused() -> None:
    log = _mutated(receipt_digest="sha256:" + "1" * 64)
    problems = _problems(log)
    assert any("receipt_digest" in problem for problem in problems), problems


def test_a_reformatted_receipt_is_reported_by_the_raw_digest() -> None:
    log = _mutated(receipt_bytes_sha256="sha256:" + "2" * 64)
    problems = _problems(log)
    assert any("receipt_bytes_sha256" in problem for problem in problems), problems


def test_a_disposition_bound_to_different_artifact_coordinates_is_refused() -> None:
    log = copy.deepcopy(_log())
    log["entries"][0]["artifact"]["sha256"] = "0" * 64
    problems = _problems(log)
    assert any("artifact sha256" in problem for problem in problems), problems


def test_an_invalidated_candidate_may_not_claim_to_be_publishable() -> None:
    log = _mutated(publishable=True)
    problems = _problems(log)
    assert any("publishable=true" in problem for problem in problems), problems


def test_an_abbreviated_invalidating_commit_is_refused() -> None:
    """A coordinate, not a search. `0f390a9a` identifies a commit only if you
    already have the repository in front of you and no collision ever occurs."""
    log = _mutated(invalidating_commit="0f390a9a")
    problems = _problems(log)
    assert any("40-hex" in problem for problem in problems), problems


def test_an_unknown_disposition_is_refused() -> None:
    log = _mutated(disposition="probably-fine")
    problems = _problems(log)
    assert any("is not one of" in problem for problem in problems), problems


def test_the_history_check_refuses_an_edited_prefix(tmp_path: Path) -> None:
    """The append-only oracle, planted in a real git history.

    `test_the_committed_history_only_ever_grew` is vacuous in the change that
    introduces the log — there is no past yet. This builds a two-commit history
    in a scratch repository, edits the first entry in the second commit, and
    observes the refusal. Without it, the history check would be a function
    nobody has ever seen fail.
    """
    repo = tmp_path / "repo"
    (repo / "docs" / "inventories").mkdir(parents=True)
    log_path = Path("docs/inventories/foundation-candidate-dispositions.json")

    def git(*args: str) -> None:
        subprocess.run(  # noqa: S603 # nosec B603 B607 -- fixed argv, no shell
            ["git", "-C", str(repo), *args],  # noqa: S607
            check=True,
            capture_output=True,
        )

    git("init", "-q")
    git("config", "user.email", "gate@example.invalid")
    git("config", "user.name", "gate")

    original = _log()
    (repo / log_path).write_text(json.dumps(original, indent=2) + "\n")
    git("add", "-A")
    git("commit", "-qm", "record the disposition")

    edited = copy.deepcopy(original)
    edited["entries"][0]["reason"] = "actually it was fine"
    (repo / log_path).write_text(json.dumps(edited, indent=2) + "\n")
    git("add", "-A")
    git("commit", "-qm", "quietly rewrite history")

    problems = DISPOSITION.history_violations(repo_root=repo, path=log_path)
    assert problems, (
        "an entry edited after it was committed must be refused; the history "
        "oracle is the only thing that can see it"
    )
    assert any("prefix" in problem for problem in problems), problems


def test_an_unavailable_git_oracle_refuses_rather_than_passing(
    tmp_path: Path,
) -> None:
    """An oracle that cannot answer must not answer 'fine'.

    `test_declared_publication.py` records this failure happening for real: its
    sweep used to skip on a refusal, so on CI every check in the module skipped
    silently while the gate reported green.
    """
    with pytest.raises(SystemExit):
        DISPOSITION.history_violations(repo_root=tmp_path)
