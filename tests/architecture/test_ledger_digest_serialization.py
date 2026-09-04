"""The ledger's content digest, proven against real branches and real `merge-tree`.

`tests/unit/test_namespaces.py` proves the digest is a correct function of the
ledger. That is not the claim that matters. The claim that matters is that two
sibling allocation branches now CONFLICT in git, and the only honest way to
check it is to build the branches and run the merge.

## The defect this repairs, measured

`scripts/check_allocation_serialized.py` serializes a module's SOURCE behind its
allocation. Nothing serialized one allocation against another. Two branches off
one base — one appending a row at the tail, one inserting a row mid-list with a
DUPLICATE prefix — merge under `git merge-tree` with exit 0 and no conflict,
producing a ledger with two rows claiming `prefix="me"`.

Every structure the ledger touches absorbs an independent addition: an
order-insensitive tuple, a list `__all__`, a SET literal of pinned owners, an
unordered markdown table in `COMPATIBILITY.md`. The set literal is why
`test_the_shipped_ledger_is_the_host_owners_plus_allocated_modules` passes on
the merged result — the union of two additions satisfies it. Only
`test_the_shipped_ledger_itself_composes` bites, and it bites AFTER the merge,
on a red `main`.

The kernel's alpha version used to be the scalar that forced serialization: a93
and a95 each bumped it, so two allocations racing for one release both had to
move one line. The `+dev` regime at `a76a887e` removed it. A COUNTER does not
bring it back — two branches can both change 90 to 91 on identical text and
merge just as cleanly. Only a value derived from the ledger's CONTENT gives two
different additions two different strings to fight over.

## Non-vacuity

`test_two_sibling_allocations_merged_clean_without_the_digest` is not decoration.
A conflict test alone would still pass if the two branches conflicted for some
unrelated reason — overlapping context lines, say — and would keep passing after
the digest was removed. So the same two branches are built twice, differing only
in whether the ledger carries the digest, and the without-digest case must merge
CLEAN and must actually produce the duplicate row. That is the experiment that
found the defect, run as a test.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from dotmac_kernel.namespaces import (
    MigrationOwner,
    migration_owner_ledger_digest,
    module_schema,
)

from scripts.check_allocation_serialized import run_gate

LEDGER = "packages/dotmac-kernel/src/dotmac_kernel/namespaces.py"

#: A miniature ledger. Four rows is enough for "mid-list" to mean something and
#: small enough that the conflict is attributable to the digest line.
BASE_ROWS = (
    ("accounting", "ac", "accounting"),
    ("domains", "dn", "domains"),
    ("hosting", "hs", "hosting"),
    ("ticketing", "tk", "ticketing"),
)

#: The two claimants. Different owners, different schemas, SAME prefix — the
#: exact collision the `sa` incident produced three times over.
MANAGED_EMAIL = ("managed_email", "me", "email")
MEDIA_EXCHANGE = ("media_exchange", "me", "mediax")


def _git(repository: Path, *args: str, check: bool = True):
    # The "untrusted input" S603/S607 warn about is this file's own literals
    # plus a pytest tmp_path: fixed argv, no shell.
    return subprocess.run(  # noqa: S603 # nosec B603 B607
        ["git", "-C", str(repository), *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=check,
    )


def _row(owner: str, prefix: str, schema: str) -> str:
    return (
        f"{owner.upper()}_MIGRATION_OWNER = MigrationOwner(\n"
        f'    owner="{owner}",\n'
        f'    prefix="{prefix}",\n'
        f'    branch_label="{owner}",\n'
        f'    db_schema=module_schema("{schema}"),\n'
        ")\n"
    )


def _ledger(rows, *, with_digest: bool) -> str:
    """The miniature ledger's source, optionally carrying its content digest.

    The digest is computed with the REAL `migration_owner_ledger_digest`, not a
    stand-in, so this test fails if the shipped function stops being a function
    of the row set.
    """
    body = "\n".join(_row(*row) for row in rows)
    members = "".join(f"    {owner.upper()}_MIGRATION_OWNER,\n" for owner, _, _ in rows)
    source = f"{body}\nMIGRATION_OWNER_LEDGER = (\n{members})\n"
    if with_digest:
        owners = [
            MigrationOwner(
                owner=owner,
                prefix=prefix,
                branch_label=owner,
                db_schema=module_schema(schema),
            )
            for owner, prefix, schema in rows
        ]
        computed = migration_owner_ledger_digest(owners)
        source += f'\nMIGRATION_OWNER_LEDGER_DIGEST = "{computed}"\n'
    return source


def _two_sibling_allocations(tmp_path: Path, *, with_digest: bool) -> Path:
    """One base, two branches: a TAIL APPEND and a MID-LIST INSERT of one prefix."""
    repository = tmp_path / ("with_digest" if with_digest else "without_digest")
    if repository.exists():
        shutil.rmtree(repository)
    (repository / Path(LEDGER).parent).mkdir(parents=True)
    _git(repository, "init", "-q", "-b", "main")
    _git(repository, "config", "user.email", "ledger@test")
    _git(repository, "config", "user.name", "ledger")

    def commit(rows, message: str) -> None:
        (repository / LEDGER).write_text(_ledger(rows, with_digest=with_digest))
        _git(repository, "add", LEDGER)
        _git(repository, "commit", "-qm", message)

    commit(BASE_ROWS, "base ledger")

    _git(repository, "checkout", "-q", "-b", "alloc/managed-email")
    commit((*BASE_ROWS, MANAGED_EMAIL), "allocate managed_email (tail append)")

    _git(repository, "checkout", "-q", "main")
    _git(repository, "checkout", "-q", "-b", "alloc/media-exchange")
    commit(
        (*BASE_ROWS[:2], MEDIA_EXCHANGE, *BASE_ROWS[2:]),
        "allocate media_exchange (mid-list insert)",
    )
    return repository


def _merge_tree(repository: Path):
    return _git(
        repository,
        "merge-tree",
        "--write-tree",
        "alloc/managed-email",
        "alloc/media-exchange",
        check=False,
    )


def test_two_sibling_allocations_merged_clean_without_the_digest(
    tmp_path: Path,
) -> None:
    """The defect, reproduced — and the reason the conflict test is not vacuous.

    Without the digest these two branches merge with exit 0 and the merged tree
    holds TWO rows claiming `prefix="me"`. Both halves are asserted: a clean
    merge that happened not to produce the duplicate would not be this defect.
    """
    repository = _two_sibling_allocations(tmp_path, with_digest=False)

    result = _merge_tree(repository)

    assert result.returncode == 0, (
        "the without-digest case no longer merges clean, so the conflict test "
        f"below proves nothing:\n{result.stdout}\n{result.stderr}"
    )
    tree = result.stdout.split("\n")[0].strip()
    merged = _git(repository, "show", f"{tree}:{LEDGER}").stdout
    assert merged.count('prefix="me"') == 2, (
        "two sibling allocations merged clean but did not produce the duplicate "
        "prefix; this fixture no longer reproduces the measured defect"
    )


def test_the_digest_turns_that_clean_merge_into_a_conflict(tmp_path: Path) -> None:
    """The same two branches, against a ledger that carries its content digest.

    Each branch recomputes the digest over its own row set, so the two write
    different strings on one line. `git merge-tree` exits non-zero and reports a
    content conflict — a human resolves it, and resolving it is where the
    duplicate prefix becomes visible before it reaches `main`.
    """
    repository = _two_sibling_allocations(tmp_path, with_digest=True)

    result = _merge_tree(repository)

    assert result.returncode != 0, (
        "two sibling allocations still merge clean with the digest present"
    )
    assert "CONFLICT" in result.stdout


def test_relocating_the_row_does_not_dodge_the_conflict(tmp_path: Path) -> None:
    """A mid-list insert and a tail append of the SAME row are the same digest.

    The unit suite proves this on the function. It is re-proved here on real
    branches because the property that matters is a git one: a branch cannot
    escape the conflict by moving its row somewhere else in the tuple, since the
    digest it must write is unchanged by position.
    """
    appended = _ledger((*BASE_ROWS, MANAGED_EMAIL), with_digest=True)
    inserted = _ledger(
        (*BASE_ROWS[:2], MANAGED_EMAIL, *BASE_ROWS[2:]), with_digest=True
    )

    def digest_line(source: str) -> str:
        lines = source.split("\n")
        return next(line for line in lines if "MIGRATION_OWNER_LEDGER_DIGEST" in line)

    assert appended != inserted, "the two ledger sources are genuinely different"
    assert digest_line(appended) == digest_line(inserted)


def test_an_allocation_that_leaves_the_digest_alone_is_refused(tmp_path: Path) -> None:
    """Verifier requirement 3: every allocation commit must move the literal.

    This gate is LOAD-BEARING, not belt-and-braces, and the experiment says so.
    Running the two sibling branches against the real ledger with one of them
    failing to restamp produced `merge-tree` EXIT 0 and a merged ledger holding
    two rows on one prefix — because only one side touched the digest line, and
    a line only one side changed is not a conflict. The digest creates the
    conflict only when BOTH branches restamp. Nothing in git makes them; this
    gate does.

    So a branch that adds a row and leaves the literal alone is refused here,
    rather than trusted to a reviewer noticing an unchanged 64-character string
    in a diff that also adds a row.
    """
    repository = _two_sibling_allocations(tmp_path, with_digest=True)
    _git(repository, "checkout", "-q", "main")
    _git(repository, "checkout", "-q", "-b", "alloc/forgot-to-restamp")
    stale = _ledger((*BASE_ROWS, MANAGED_EMAIL), with_digest=True).replace(
        migration_owner_ledger_digest(
            [
                MigrationOwner(
                    owner=owner,
                    prefix=prefix,
                    branch_label=owner,
                    db_schema=module_schema(schema),
                )
                for owner, prefix, schema in (*BASE_ROWS, MANAGED_EMAIL)
            ]
        ),
        migration_owner_ledger_digest(
            [
                MigrationOwner(
                    owner=owner,
                    prefix=prefix,
                    branch_label=owner,
                    db_schema=module_schema(schema),
                )
                for owner, prefix, schema in BASE_ROWS
            ]
        ),
    )
    (repository / LEDGER).write_text(stale)
    _git(repository, "add", LEDGER)
    _git(repository, "commit", "-qm", "allocate managed_email, digest untouched")

    violations = run_gate("main", "HEAD", repo=str(repository))

    assert violations, "an allocation that left the digest unchanged passed the gate"
    assert "MIGRATION_OWNER_LEDGER_DIGEST" in violations[0]


def test_a_restamped_allocation_passes_the_gate(tmp_path: Path) -> None:
    """The other direction: a correctly restamped allocation is not obstructed.

    A gate that refused every allocation would also make the test above pass,
    which is why both directions are asserted.
    """
    repository = _two_sibling_allocations(tmp_path, with_digest=True)

    assert run_gate("main", "alloc/managed-email", repo=str(repository)) == []
    assert run_gate("main", "alloc/media-exchange", repo=str(repository)) == []


def test_a_ledger_with_no_digest_at_all_is_refused_not_a_pass(
    tmp_path: Path,
) -> None:
    """Deleting the literal must not read as "nothing to check".

    A gate whose check disappears with the thing it checks is the fail-open
    shape that let the first version of the allocation gate report every
    package as unallocated.

    RENAMED and its verdict CHANGED, deliberately and not to make anything
    green: this asserted `GateError` (exit 2, indeterminate). A ledger the gate
    successfully read and found to carry no digest is not a question it could
    not answer — it answered, and the answer was no. Indeterminate is reserved
    for what it genuinely cannot read, such as a digest that is a computed
    expression rather than a literal. So this is now a violation (exit 1), with
    a diagnostic that additionally says WHICH of the two absences it is: a
    branch removing a digest its merge base had, or a bootstrap that failed to
    introduce one. Both are refusals; they have different repairs.
    """
    repository = _two_sibling_allocations(tmp_path, with_digest=False)
    _git(repository, "checkout", "-q", "alloc/managed-email")

    violations = run_gate("main", "HEAD", repo=str(repository))

    assert violations, "a ledger with no digest must not pass"
    assert "head declares no MIGRATION_OWNER_LEDGER_DIGEST" in violations[0]
    assert "must INTRODUCE it" in violations[0], (
        "neither side has a digest, so this is the bootstrap case and the "
        "diagnostic must say so rather than accusing the branch of a removal"
    )
