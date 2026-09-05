"""The allocation gate's EXIT CODES and diagnostics, through the real CLI.

`test_ledger_digest_serialization.py` calls `run_gate` in-process and asserts
the violation list. That is the right shape for the merge semantics, and the
wrong shape for the question here, because CI does not read a list — it reads
`$?`. Every case below runs `scripts/check_allocation_serialized.py` as a
subprocess against a real git repository and asserts BOTH the exit status and
the diagnostic text, because a gate that refuses for the wrong stated reason
sends the next reader to the wrong repair.

The contract, and the reason it is worth a module of its own:

    0  serialized
    1  a violation — the gate read the ledger and rejected it
    2  indeterminate — the gate could not establish an answer

An indeterminate result must never read as a pass. The converse matters just as
much and is what this module was written for: a refusal must never read as
indeterminate either. Exit 2 is "the gate is broken, ask a human"; exit 1 is
"you did the forbidden thing". Collapsing the second into the first is how a
real violation gets triaged as flaky tooling and waved through.

## Non-vacuity

These are not decoration. Measured against the gate BEFORE the one-time
transition was handled explicitly — when `digest_violations` returned early
whenever the row set was unchanged, before reading either digest:

    transition + a row change            exit 2   (must be 1)
    transition, malformed head digest    exit 0   (must be 1)
    transition, head digest absent       exit 0   (must be 1)
    the legitimate bootstrap             exit 0   — correct, but by the SAME
                                                    unvalidated path as the two
                                                    above, so indistinguishable

Three of the four cases in this module therefore fail against the previous
gate, and the fourth passed for a reason that was not a check. Restoring that
early return turns this module red.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from dotmac_kernel.namespaces import (
    MigrationOwner,
    migration_owner_ledger_digest,
    module_schema,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_allocation_serialized.py"
LEDGER = "packages/dotmac-kernel/src/dotmac_kernel/namespaces.py"

BASE_ROWS = (("accounting", "ac"), ("ticketing", "tk"))
ADDED_ROW = ("managed_email", "me")


def _git(repository: Path, *args: str) -> None:
    # Fixed argv from this file's own literals plus a pytest tmp_path; no shell.
    subprocess.run(  # noqa: S603 # nosec B603 B607
        ["git", "-C", str(repository), *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )


def _ledger(rows: tuple[tuple[str, str], ...], digest: str | None) -> str:
    """A miniature ledger. `digest` None means the literal is genuinely absent."""
    body = "".join(
        f"{owner.upper()}_MIGRATION_OWNER = MigrationOwner(\n"
        f'    owner="{owner}",\n'
        f'    prefix="{prefix}",\n'
        f'    branch_label="{owner}",\n'
        f'    db_schema=module_schema("{owner}"),\n'
        ")\n"
        for owner, prefix in rows
    )
    members = "".join(f"    {owner.upper()}_MIGRATION_OWNER,\n" for owner, _ in rows)
    source = f"{body}\nMIGRATION_OWNER_LEDGER = (\n{members})\n"
    if digest is not None:
        source += f'\nMIGRATION_OWNER_LEDGER_DIGEST = "{digest}"\n'
    return source


def _real_digest(rows: tuple[tuple[str, str], ...]) -> str:
    """The digest the SHIPPED function computes, never a stand-in."""
    return migration_owner_ledger_digest(
        [
            MigrationOwner(
                owner=owner,
                prefix=prefix,
                branch_label=owner,
                db_schema=module_schema(owner),
            )
            for owner, prefix in rows
        ]
    )


def _repository(tmp_path: Path, name: str, *, base: str, head: str) -> Path:
    """One repo, one base commit, one `work` branch holding `head`'s ledger."""
    repository = tmp_path / name
    (repository / Path(LEDGER).parent).mkdir(parents=True)
    _git(repository, "init", "-q", "-b", "main")
    _git(repository, "config", "user.email", "gate@test")
    _git(repository, "config", "user.name", "gate")
    ledger = repository / LEDGER
    ledger.write_text(base)
    _git(repository, "add", LEDGER)
    _git(repository, "commit", "-q", "-m", "base")
    _git(repository, "checkout", "-q", "-b", "work")
    ledger.write_text(head)
    _git(repository, "add", LEDGER)
    _git(repository, "commit", "-q", "-m", "head")
    return repository


def _run(repository: Path) -> subprocess.CompletedProcess[str]:
    """The REAL CLI, in that repository, exactly as CI invokes it."""
    return subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), "--base", "main", "--head", "work"],
        cwd=str(repository),
        capture_output=True,
        text=True,
        check=False,
    )


# ── The one-time transition ─────────────────────────────────────────────────


def test_the_bootstrap_passes(tmp_path: Path) -> None:
    """A well-formed digest introduced over an unchanged ledger: exit 0."""
    result = _run(
        _repository(
            tmp_path,
            "bootstrap",
            base=_ledger(BASE_ROWS, None),
            head=_ledger(BASE_ROWS, _real_digest(BASE_ROWS)),
        )
    )

    assert result.returncode == 0, result.stderr
    assert "allocation gate:" in result.stdout


def test_the_bootstrap_may_not_also_change_a_row(tmp_path: Path) -> None:
    """A row added in the same step is REFUSED (1), not indeterminate (2).

    That row would be the only row in the ledger's history that no digest ever
    serialized, and no later gate could tell it apart from one that had always
    been there.
    """
    rows = (*BASE_ROWS, ADDED_ROW)
    result = _run(
        _repository(
            tmp_path,
            "transition_with_row",
            base=_ledger(BASE_ROWS, None),
            head=_ledger(rows, _real_digest(rows)),
        )
    )

    assert result.returncode == 1, (
        f"a row change during the transition must be a refusal, not exit "
        f"{result.returncode}: {result.stderr}"
    )
    assert "must carry the digest alone" in result.stderr
    assert "Land the digest over today's ledger first" in result.stderr


def test_a_bootstrap_that_does_not_introduce_the_digest_is_refused(
    tmp_path: Path,
) -> None:
    """Neither side has a digest: exit 1, and the diagnostic says which absence."""
    result = _run(
        _repository(
            tmp_path,
            "no_digest_either_side",
            base=_ledger(BASE_ROWS, None),
            head=_ledger((*BASE_ROWS, ADDED_ROW), None),
        )
    )

    assert result.returncode == 1, result.stderr
    assert "head declares no MIGRATION_OWNER_LEDGER_DIGEST" in result.stderr
    assert "must INTRODUCE it" in result.stderr
    assert "REMOVES it" not in result.stderr, (
        "the merge base has no digest either, so this is a bootstrap that "
        "failed to bootstrap, not a removal"
    )


def test_a_malformed_digest_is_refused_rather_than_trusted(tmp_path: Path) -> None:
    """A digest that cannot be compared serializes nothing: exit 1."""
    result = _run(
        _repository(
            tmp_path,
            "malformed",
            base=_ledger(BASE_ROWS, None),
            head=_ledger(BASE_ROWS, "cv1:deadbeef"),
        )
    )

    assert result.returncode == 1, result.stderr
    assert "is malformed" in result.stderr
    assert "cv1:<64 lowercase hex digits>" in result.stderr


# ── Steady state, once the ledger carries a digest ──────────────────────────


def test_removing_the_digest_is_a_removal_not_a_bootstrap(tmp_path: Path) -> None:
    """The base HAS one, so the diagnostic must name the removal: exit 1."""
    result = _run(
        _repository(
            tmp_path,
            "removal",
            base=_ledger(BASE_ROWS, _real_digest(BASE_ROWS)),
            head=_ledger(BASE_ROWS, None),
        )
    )

    assert result.returncode == 1, result.stderr
    assert "REMOVES it" in result.stderr
    assert "must INTRODUCE it" not in result.stderr


def test_an_allocation_that_leaves_the_digest_alone_is_refused(
    tmp_path: Path,
) -> None:
    """A row added with a stale digest: exit 1, naming the recompute."""
    result = _run(
        _repository(
            tmp_path,
            "stale",
            base=_ledger(BASE_ROWS, _real_digest(BASE_ROWS)),
            head=_ledger((*BASE_ROWS, ADDED_ROW), _real_digest(BASE_ROWS)),
        )
    )

    assert result.returncode == 1, result.stderr
    assert "migration_owner_ledger_digest()" in result.stderr


def test_a_restamped_allocation_passes(tmp_path: Path) -> None:
    """The other direction. A gate that refused everything would pass the rest."""
    rows = (*BASE_ROWS, ADDED_ROW)
    result = _run(
        _repository(
            tmp_path,
            "restamped",
            base=_ledger(BASE_ROWS, _real_digest(BASE_ROWS)),
            head=_ledger(rows, _real_digest(rows)),
        )
    )

    assert result.returncode == 0, result.stderr


# ── Indeterminate stays reserved for what cannot be read ────────────────────


def test_a_computed_digest_is_indeterminate_not_a_violation(tmp_path: Path) -> None:
    """Exit 2, and ONLY here.

    The gate compares revisions without executing either, so a digest built by
    an expression is a question it genuinely cannot answer — as opposed to
    every case above, which it read and rejected. This is the negative control
    for the whole module: if a refusal ever starts exiting 2, this test can no
    longer distinguish "broken" from "forbidden".
    """
    head = _ledger(BASE_ROWS, None) + (
        '\nMIGRATION_OWNER_LEDGER_DIGEST = "cv1:" + "0" * 64\n'
    )
    result = _run(
        _repository(tmp_path, "computed", base=_ledger(BASE_ROWS, None), head=head)
    )

    assert result.returncode == 2, result.stderr
    assert "could not run" in result.stderr
    assert "must be a string literal" in result.stderr


def test_no_refusal_in_this_module_exits_zero_or_two(tmp_path: Path) -> None:
    """The ruling's floor, asserted as a property rather than case by case.

    Every refusal shape in one place: each must exit exactly 1. Exit 0 would
    wave it through; exit 2 would file it as tooling trouble.
    """
    rows = (*BASE_ROWS, ADDED_ROW)
    digested = _real_digest(BASE_ROWS)
    refusals = {
        "transition + row change": (
            _ledger(BASE_ROWS, None),
            _ledger(rows, _real_digest(rows)),
        ),
        "bootstrap without a digest": (
            _ledger(BASE_ROWS, None),
            _ledger(rows, None),
        ),
        "malformed digest": (_ledger(BASE_ROWS, None), _ledger(BASE_ROWS, "nope")),
        "digest removed": (_ledger(BASE_ROWS, digested), _ledger(BASE_ROWS, None)),
        "stale digest": (_ledger(BASE_ROWS, digested), _ledger(rows, digested)),
    }

    codes = {
        label: _run(
            _repository(
                tmp_path,
                label.replace(" ", "_").replace("+", "and"),
                base=base,
                head=head,
            )
        ).returncode
        for label, (base, head) in refusals.items()
    }

    assert codes == dict.fromkeys(refusals, 1), codes
