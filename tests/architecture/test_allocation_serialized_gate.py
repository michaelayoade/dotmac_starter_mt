"""The serialized-allocation gate (ADR-0006 D1), proven on real repositories.

`tests/unit/test_namespaces.py` can only prove the tree it runs in is
self-consistent. This proves the thing that actually prevents the `sa`
collision class: that a module's ledger row must be merged BEFORE its source,
which is a question about the merge base and therefore about git.

Every case below builds a real temporary repository and runs the gate against
it, because the gate's failure mode is failing OPEN — a version of it that
classified packages by directory name reported "no manifest" for every package
on a branch that was not checked out, and a version that matched manifests with
a regex silently skipped any formatting it did not recognise. Neither would
have been caught by asserting on data structures.
"""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest

from scripts.check_allocation_serialized import GateError, run_gate

LEDGER = "packages/dotmac-kernel/src/dotmac_kernel/namespaces.py"

BASE_LEDGER = """\
from dotmac_kernel.namespaces import MigrationOwner, module_schema

APPROVALS_MIGRATION_OWNER = MigrationOwner(
    owner="approvals",
    prefix="ap",
    branch_label="approvals",
    db_schema=module_schema("approvals"),
)

MIGRATION_OWNER_LEDGER = (APPROVALS_MIGRATION_OWNER,)
"""

# `main` plus a merged `sales` allocation. Built by substitution rather than
# written out three times, so the three scenarios that need it cannot drift
# apart and quietly stop testing the same thing.
LEDGER_WITH_SALES = BASE_LEDGER.replace(
    "MIGRATION_OWNER_LEDGER = (APPROVALS_MIGRATION_OWNER,)",
    "SALES_MIGRATION_OWNER = MigrationOwner(\n"
    '    owner="sales",\n'
    '    prefix="sa",\n'
    '    branch_label="sales",\n'
    '    db_schema=module_schema("sales"),\n'
    ")\n\n"
    "MIGRATION_OWNER_LEDGER = (\n"
    "    APPROVALS_MIGRATION_OWNER,\n"
    "    SALES_MIGRATION_OWNER,\n"
    ")",
)

MANIFEST = """\
from dotmac_kernel.modules import ModuleManifest

module = ModuleManifest(
    code="{code}",
    version="0.1.0",
    short_code="{short}",
    migration_prefix="{prefix}",
    migration_branch="{branch}",
    tables=(),
)
"""

STATELESS_MANIFEST = """\
from dotmac_kernel.modules import ModuleManifest

module = ModuleManifest(code="{code}", version="0.1.0")
"""


def _run(repository: Path, *args: str) -> None:
    # The "untrusted input" S603/S607 warn about is this file's own literals
    # plus a pytest tmp_path: fixed argv, no shell.
    subprocess.run(  # noqa: S603 # nosec B603 B607
        ["git", "-C", str(repository), *args],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    )


def _write(repository: Path, path: str, body: str) -> None:
    target = repository / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body)


def _dossier(classification: str) -> str:
    return textwrap.dedent(
        f"""\
        schema_version = 1
        classification = "{classification}"
        """
    )


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    """A `main` carrying one allocated module (`approvals`) and one connector."""
    repo = tmp_path / "starter"
    repo.mkdir()
    _run(repo, "init", "-q", "-b", "main")
    _run(repo, "config", "user.email", "gate@test")
    _run(repo, "config", "user.name", "gate")

    _write(repo, LEDGER, BASE_LEDGER)
    _write(
        repo,
        "packages/dotmac-kernel/EXTRACTION.toml",
        _dossier("universal-facility"),
    )
    _write(
        repo,
        "packages/dotmac-approvals/EXTRACTION.toml",
        _dossier("optional-module"),
    )
    _write(
        repo,
        "packages/dotmac-approvals/src/dotmac_approvals/manifest.py",
        MANIFEST.format(
            code="approvals", short="approvals", prefix="ap", branch="approvals"
        ),
    )
    _write(
        repo,
        "packages/dotmac-connector-paystack/EXTRACTION.toml",
        _dossier("stateless-protocol-adapter"),
    )
    _write(
        repo,
        "packages/dotmac-connector-paystack/src/dotmac_connector_paystack/client.py",
        "TIMEOUT = 30\n",
    )
    _write(
        repo,
        "packages/dotmac-ui/EXTRACTION.toml",
        _dossier("presentation-foundation"),
    )
    _write(repo, "packages/dotmac-ui/src/dotmac_ui/tokens.py", "TOKENS = {}\n")
    _run(repo, "add", "-A")
    _run(repo, "commit", "-qm", "main: approvals allocated")
    return repo


def _branch(repository: Path, name: str) -> None:
    _run(repository, "checkout", "-q", "-b", name, "main")


def _commit(repository: Path, message: str) -> None:
    _run(repository, "add", "-A")
    _run(repository, "commit", "-qm", message)


def _gate(repository: Path, head: str) -> list[str]:
    return run_gate("main", head, repo=str(repository))


# ── The acceptance table ────────────────────────────────────────────────────


def test_a_connector_branch_passes(repository: Path) -> None:
    """A stateless protocol adapter owns no namespace, so it is never gated."""
    _branch(repository, "connector")
    _write(
        repository,
        "packages/dotmac-connector-paystack/src/dotmac_connector_paystack/client.py",
        "TIMEOUT = 60\n",
    )
    _commit(repository, "connector: bump timeout")

    assert _gate(repository, "connector") == []


def test_a_presentation_foundation_branch_passes(repository: Path) -> None:
    _branch(repository, "ui")
    _write(
        repository,
        "packages/dotmac-ui/src/dotmac_ui/tokens.py",
        "TOKENS = {'a': 1}\n",
    )
    _commit(repository, "ui: add a token")

    assert _gate(repository, "ui") == []


def test_editing_an_allocated_module_passes(repository: Path) -> None:
    _branch(repository, "edit")
    _write(
        repository,
        "packages/dotmac-approvals/src/dotmac_approvals/service.py",
        "def approve() -> None: ...\n",
    )
    _commit(repository, "approvals: add a service")

    assert _gate(repository, "edit") == []


def test_an_allocation_only_branch_passes(repository: Path) -> None:
    """The change that makes a prefix real for everybody, and the whole point.

    It touches no module source, so it passes vacuously — which is what lets
    allocation land first, on its own, where a duplicate is a conflict in one
    file rather than a surprise at merge.
    """
    _branch(repository, "allocate")
    _write(repository, LEDGER, LEDGER_WITH_SALES)
    _commit(repository, "allocate: reserve sa for sales")

    assert _gate(repository, "allocate") == []


def test_allocation_bundled_with_module_source_fails(repository: Path) -> None:
    """The `sa` failure mode: the row does not exist at its own merge base."""
    _branch(repository, "bundled")
    _write(repository, LEDGER, LEDGER_WITH_SALES)
    _write(
        repository, "packages/dotmac-sales/EXTRACTION.toml", _dossier("optional-module")
    )
    _write(
        repository,
        "packages/dotmac-sales/src/dotmac_sales/manifest.py",
        MANIFEST.format(code="sales", short="sales", prefix="sa", branch="sales"),
    )
    _write(
        repository,
        "packages/dotmac-sales/src/dotmac_sales/migrations/versions/sa_0001_sales.py",
        "revision = 'sa_0001_sales'\n",
    )
    _commit(repository, "sales: allocation and lineage together")

    violations = _gate(repository, "bundled")

    assert len(violations) == 1
    assert "dotmac-sales" in violations[0]
    assert "ledger row at the merge base" in violations[0]


def test_the_train_passes_once_the_allocation_has_merged(repository: Path) -> None:
    """The acceptance criterion that proves the gate is a sequencer, not a wall.

    Same module source that failed above, rebased onto a `main` that now
    carries the allocation. Without this, a gate that simply refused new
    modules would look identical.
    """
    _run(repository, "checkout", "-q", "main")
    _write(repository, LEDGER, LEDGER_WITH_SALES)
    _commit(repository, "main: allocation merged")

    _branch(repository, "train")
    _write(
        repository, "packages/dotmac-sales/EXTRACTION.toml", _dossier("optional-module")
    )
    _write(
        repository,
        "packages/dotmac-sales/src/dotmac_sales/manifest.py",
        MANIFEST.format(code="sales", short="sales", prefix="sa", branch="sales"),
    )
    _commit(repository, "sales: the module, after allocation")

    assert _gate(repository, "train") == []


def test_indeterminate_history_raises_rather_than_passing(repository: Path) -> None:
    """Exit 2, never 0. A gate that cannot answer must not report success."""
    with pytest.raises(GateError):
        run_gate("no-such-ref", "main", repo=str(repository))


# ── Fail-closed classification ──────────────────────────────────────────────


def test_a_missing_classification_fails(repository: Path) -> None:
    _branch(repository, "unclassified")
    _write(
        repository, "packages/dotmac-mystery/EXTRACTION.toml", "schema_version = 1\n"
    )
    _write(
        repository, "packages/dotmac-mystery/src/dotmac_mystery/models.py", "X = 1\n"
    )
    _commit(repository, "mystery: no classification")

    with pytest.raises(GateError, match="declares no `classification`"):
        _gate(repository, "unclassified")


def test_an_unreadable_dossier_fails(repository: Path) -> None:
    _branch(repository, "broken")
    _write(repository, "packages/dotmac-broken/EXTRACTION.toml", "this = = not toml\n")
    _write(repository, "packages/dotmac-broken/src/dotmac_broken/models.py", "X = 1\n")
    _commit(repository, "broken: unparseable dossier")

    with pytest.raises(GateError, match="unreadable"):
        _gate(repository, "broken")


def test_an_unknown_classification_fails(repository: Path) -> None:
    """A new classification must be taught to the gate, not silently skipped."""
    _branch(repository, "novel")
    _write(
        repository, "packages/dotmac-novel/EXTRACTION.toml", _dossier("something-new")
    )
    _write(repository, "packages/dotmac-novel/src/dotmac_novel/models.py", "X = 1\n")
    _commit(repository, "novel: unknown classification")

    violations = _gate(repository, "novel")

    assert len(violations) == 1
    assert "unknown classification" in violations[0]


def test_an_optional_module_with_no_manifest_fails(repository: Path) -> None:
    """Fail-closed: a gated package whose allocation cannot be read is a fail."""
    _branch(repository, "manifestless")
    _write(
        repository, "packages/dotmac-ghost/EXTRACTION.toml", _dossier("optional-module")
    )
    _write(repository, "packages/dotmac-ghost/src/dotmac_ghost/models.py", "X = 1\n")
    _commit(repository, "ghost: source without a manifest")

    violations = _gate(repository, "manifestless")

    assert len(violations) == 1
    assert "0 manifest.py" in violations[0]


def test_a_genuinely_stateless_module_needs_no_ledger_row(repository: Path) -> None:
    """Declaring no namespace is a real answer, not a missing one."""
    _branch(repository, "stateless")
    _write(
        repository,
        "packages/dotmac-render/EXTRACTION.toml",
        _dossier("optional-module"),
    )
    _write(
        repository,
        "packages/dotmac-render/src/dotmac_render/manifest.py",
        STATELESS_MANIFEST.format(code="render"),
    )
    _commit(repository, "render: stateless module")

    assert _gate(repository, "stateless") == []


def test_a_non_literal_prefix_fails_rather_than_being_skipped(repository: Path) -> None:
    _branch(repository, "computed")
    _write(
        repository,
        "packages/dotmac-computed/EXTRACTION.toml",
        _dossier("optional-module"),
    )
    _write(
        repository,
        "packages/dotmac-computed/src/dotmac_computed/manifest.py",
        "from dotmac_kernel.modules import ModuleManifest\n"
        'module = ModuleManifest(code="computed", short_code=SHORT, '
        "migration_prefix=PREFIX)\n",
    )
    _commit(repository, "computed: non-literal declaration")

    with pytest.raises(GateError, match="string literal"):
        _gate(repository, "computed")


# ── Deletion and rename ─────────────────────────────────────────────────────


def test_deleting_a_module_passes_and_keeps_its_reservation(repository: Path) -> None:
    """A retired prefix is never reclaimed, so deletion needs no allocation.

    The reservation outliving the package is the point: a prefix handed to a
    new owner would collide with rows still live in a deployed database.
    """
    _branch(repository, "delete")
    _run(repository, "rm", "-r", "-q", "packages/dotmac-approvals")
    _commit(repository, "approvals: retire the package")

    assert _gate(repository, "delete") == []


def test_a_rename_checks_the_new_side(repository: Path) -> None:
    """Deletion half passes; the added half is gated like any new module."""
    _branch(repository, "rename")
    _run(repository, "rm", "-r", "-q", "packages/dotmac-approvals")
    _write(
        repository,
        "packages/dotmac-sign-off/EXTRACTION.toml",
        _dossier("optional-module"),
    )
    _write(
        repository,
        "packages/dotmac-sign-off/src/dotmac_sign_off/manifest.py",
        MANIFEST.format(
            code="sign_off", short="signoff", prefix="so", branch="sign_off"
        ),
    )
    _commit(repository, "rename approvals -> sign-off")

    violations = _gate(repository, "rename")

    assert len(violations) == 1
    assert "dotmac-sign-off" in violations[0]


def test_a_renamed_code_still_resolves_through_its_branch_label(
    repository: Path,
) -> None:
    """`migration_branch` is the immutable lineage identity.

    A module that changes its `code` after allocation has not changed which
    lineage it owns, so resolving only by `code` would report an allocated
    module as unallocated and push it toward a second, duplicate row.
    """
    _branch(repository, "recode")
    _write(
        repository,
        "packages/dotmac-approvals/src/dotmac_approvals/manifest.py",
        MANIFEST.format(
            code="approval_decisions",
            short="approvals",
            prefix="ap",
            branch="approvals",
        ),
    )
    _commit(repository, "approvals: rename the code, keep the lineage")

    assert _gate(repository, "recode") == []


# ── The head is not the working tree ────────────────────────────────────────


def test_the_gate_reads_a_head_that_is_not_checked_out(repository: Path) -> None:
    """Reading the working tree instead of `head` attaches a wrong REASON.

    An earlier version did exactly that: pointed at a branch that was not
    checked out, it reported "no manifest" for every package the branch added
    — the right verdict for the wrong reason, which hides the real one.
    """
    _branch(repository, "elsewhere")
    _write(
        repository, "packages/dotmac-sales/EXTRACTION.toml", _dossier("optional-module")
    )
    _write(
        repository,
        "packages/dotmac-sales/src/dotmac_sales/manifest.py",
        MANIFEST.format(code="sales", short="sales", prefix="sa", branch="sales"),
    )
    _commit(repository, "sales: added on a branch")
    _run(repository, "checkout", "-q", "main")  # the branch is NOT checked out

    violations = _gate(repository, "elsewhere")

    assert len(violations) == 1
    # The REASON must be the allocation, not "I could not find a manifest" —
    # the failure mode this guards. The example path legitimately ends in
    # `manifest.py`, so assert on the diagnosis, not on the substring.
    assert "ledger row" in violations[0]
    assert "manifest.py;" not in violations[0]


# ── The gate is actually wired, with the evidence it needs ──────────────────


def test_ci_runs_the_gate_with_full_history_and_an_immutable_base() -> None:
    """A correct gate wired wrongly is a green check that proves nothing.

    Three properties, each of which silently neuters the job if lost:

    - `fetch-depth: 0`. A shallow checkout has no merge base, so the gate
      exits 2 — visible, but the job stops testing allocation at all.
    - the base **SHA**, not the base **ref**. A ref moves, so gating on it
      makes the verdict depend on when the job ran: a module could pass
      because somebody else's allocation landed on the base after this PR was
      opened. That is precisely the race the gate exists to remove.
    - the SHA passed through `env:` and quoted in the command, so a value that
      is not a SHA can never be parsed as shell.
    """
    import yaml

    workflow = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / ".github/workflows/ci.yml").read_text()
    )
    job = workflow["jobs"]["allocation-gate"]

    checkout = next(
        step
        for step in job["steps"]
        if str(step.get("uses", "")).startswith("actions/checkout")
    )
    assert checkout["with"]["fetch-depth"] == 0, "no merge base without full history"

    step = next(
        step
        for step in job["steps"]
        if "check_allocation_serialized" in str(step.get("run", ""))
    )
    assert (
        step["env"]["BASE_SHA"] == "${{ github.event.pull_request.base.sha }}"
    ), "gate against the immutable base SHA, never the moving base ref"
    assert '--base "$BASE_SHA"' in step["run"], "pass the SHA quoted, via the env var"
    assert "${{" not in step["run"], "never interpolate an expression into the command"


def test_the_gate_is_not_in_the_quality_matrix_or_make_check() -> None:
    """It needs the network and full history; `make check` must stay offline.

    Also protects the invariant `test_ci_runs_canonical_check.py` enforces —
    that the quality matrix equals `make check`'s prerequisites exactly — from
    being broken by adding this job's target to one and not the other.
    """
    import yaml

    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root / ".github/workflows/ci.yml").read_text())
    matrix = workflow["jobs"]["quality"]["strategy"]["matrix"]["target"]
    assert "allocation-gate" not in matrix

    check_line = next(
        line
        for line in (root / "Makefile").read_text().splitlines()
        if line.startswith("check:")
    )
    assert "allocation-gate" not in check_line
