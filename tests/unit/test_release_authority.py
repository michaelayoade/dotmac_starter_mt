"""``ReleaseAuthority.v1``'s closure, digest and ledger parser.

`scripts/release_authority.py` derives the complete executable surface of a
module release from the two release workflows outward, hashes it
canonically, and strictly parses the checked-in ledger that names the
currently-accepted digest. Every check here drives that module directly
(in-memory dict readers for the pure derivation/digest tests, real `git`
subprocesses for the two commit-reading tests, and the real repository for
the enforcement test that proves this repo's own ledger is up to date).
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "release_authority.py"


def _load():
    spec = importlib.util.spec_from_file_location("release_authority", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["release_authority"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def ra():
    return _load()


def _dict_reader(files: dict[str, str]):
    def read(path: str) -> str | None:
        return files.get(path)

    return read


# The minimal two-root document set every derivation test starts from: two
# workflows, the always-included policy file, and this module's own path
# (also always included). Individual tests add to this before deriving.
def _base_files(ra) -> dict[str, str]:
    return {
        ra.ROOT_WORKFLOWS[0]: "on: workflow_dispatch\njobs: {}\n",
        ra.ROOT_WORKFLOWS[1]: "on: workflow_dispatch\njobs: {}\n",
        ra.POLICY_PATH: '{"modules": {}}\n',
        ra.AUTHORITY_MODULE_PATH: "# authority module source\n",
    }


# ── 1. Closure: run bodies, composite actions, shell refs, python imports ───


def test_derive_surface_always_includes_policy_and_self(ra) -> None:
    files = _base_files(ra)
    derived_files, derived_external = ra.derive_surface(_dict_reader(files))
    assert ra.POLICY_PATH in derived_files
    assert ra.AUTHORITY_MODULE_PATH in derived_files
    assert derived_external == []


def test_derive_surface_follows_a_run_body_script_reference(ra) -> None:
    files = _base_files(ra)
    files[ra.ROOT_WORKFLOWS[0]] = (
        "jobs:\n  build:\n    steps:\n"
        "      - run: python scripts/release_module.py resolve X\n"
    )
    files["scripts/release_module.py"] = "import sys\n"
    derived_files, _ = ra.derive_surface(_dict_reader(files))
    assert "scripts/release_module.py" in derived_files


def test_derive_surface_follows_a_local_composite_action_and_its_run_body(ra) -> None:
    files = _base_files(ra)
    files[ra.ROOT_WORKFLOWS[0]] = (
        "jobs:\n  build:\n    steps:\n" "      - uses: ./.github/actions/setup-poetry\n"
    )
    files[".github/actions/setup-poetry/action.yml"] = (
        "runs:\n  using: composite\n  steps:\n"
        "    - run: python scripts/check_poetry_toolchain.py\n"
    )
    files["scripts/check_poetry_toolchain.py"] = "import sys\n"
    derived_files, _ = ra.derive_surface(_dict_reader(files))
    assert ".github/actions/setup-poetry/action.yml" in derived_files
    assert "scripts/check_poetry_toolchain.py" in derived_files


def test_derive_surface_follows_a_shell_scripts_own_references(ra) -> None:
    files = _base_files(ra)
    files[ra.ROOT_WORKFLOWS[0]] = (
        "jobs:\n  build:\n    steps:\n"
        "      - run: bash scripts/open_release_record_pr.sh\n"
    )
    files["scripts/open_release_record_pr.sh"] = (
        "#!/usr/bin/env bash\npython scripts/write_release_record.py\n"
    )
    files["scripts/write_release_record.py"] = "import json\n"
    derived_files, _ = ra.derive_surface(_dict_reader(files))
    assert "scripts/open_release_record_pr.sh" in derived_files
    assert "scripts/write_release_record.py" in derived_files


def test_derive_surface_follows_a_python_import(ra) -> None:
    files = _base_files(ra)
    files[ra.ROOT_WORKFLOWS[0]] = (
        "jobs:\n  build:\n    steps:\n"
        "      - run: python scripts/tag_module_release.py\n"
    )
    files["scripts/tag_module_release.py"] = (
        "from write_release_record import ReleaseRecordError\n"
    )
    files["scripts/write_release_record.py"] = "import json\n"
    derived_files, derived_external = ra.derive_surface(_dict_reader(files))
    assert "scripts/write_release_record.py" in derived_files
    assert derived_external == []


def test_derive_surface_follows_a_python_string_literal_reference(ra) -> None:
    files = _base_files(ra)
    files[ra.ROOT_WORKFLOWS[0]] = (
        "jobs:\n  build:\n    steps:\n"
        "      - run: python scripts/release_module.py\n"
    )
    files["scripts/release_module.py"] = (
        "import subprocess\n" 'subprocess.run(["python", "scripts/helper_probe.py"])\n'
    )
    files["scripts/helper_probe.py"] = "import json\n"
    derived_files, _ = ra.derive_surface(_dict_reader(files))
    assert "scripts/helper_probe.py" in derived_files


def test_derive_surface_records_a_non_stdlib_top_level_import_as_external(ra) -> None:
    files = _base_files(ra)
    files[ra.ROOT_WORKFLOWS[0]] = (
        "jobs:\n  build:\n    steps:\n"
        "      - run: python scripts/release_module.py\n"
    )
    files["scripts/release_module.py"] = (
        "import dotmac_kernel\nfrom dotmac_kernel.modules import ModuleRegistry\n"
    )
    _, derived_external = ra.derive_surface(_dict_reader(files))
    assert derived_external == ["dotmac_kernel"]


def test_derive_surface_ignores_an_unresolvable_run_body_token(ra) -> None:
    files = _base_files(ra)
    files[ra.ROOT_WORKFLOWS[0]] = (
        "jobs:\n  build:\n    steps:\n"
        '      - run: echo "${prefix}scripts/nonexistent-thing.py"\n'
        "      - run: python scripts/release_module.py\n"
    )
    files["scripts/release_module.py"] = "import sys\n"
    # Must not raise: an unresolvable regex match is a discovery heuristic,
    # not a claimed reference.
    derived_files, _ = ra.derive_surface(_dict_reader(files))
    assert "scripts/nonexistent-thing.py" not in derived_files


def test_derive_surface_raises_on_a_dangling_composite_action_reference(ra) -> None:
    # `uses: ./<dir>` unconditionally claims that dir has an action.yml or
    # action.yaml (rule 2) — unlike a bare run-body token (rule 1), this is a
    # structural declaration, and its absence is the dangling-reference
    # defect rule 6 describes, not a heuristic miss.
    files = _base_files(ra)
    files[ra.ROOT_WORKFLOWS[0]] = (
        "jobs:\n  build:\n    steps:\n"
        "      - uses: ./.github/actions/does-not-exist\n"
    )
    with pytest.raises(ra.ReleaseAuthorityError):
        ra.derive_surface(_dict_reader(files))


def test_derive_surface_treats_an_unresolved_python_import_as_external_not_dangling(
    ra,
) -> None:
    # A top-level import that does not correspond to any `scripts/<name>.py`
    # in the surface is recorded as an external import rather than raised —
    # this closure never verifies that an external package is actually
    # installed, only that it is named. See the module docstring's rule 4:
    # the import is either a resolvable local reference (added + recursed)
    # or an external name; there is no third, "dangling import", case.
    files = _base_files(ra)
    files[ra.ROOT_WORKFLOWS[0]] = (
        "jobs:\n  build:\n    steps:\n"
        "      - run: python scripts/tag_module_release.py\n"
    )
    files["scripts/tag_module_release.py"] = (
        "from write_release_record import ReleaseRecordError\n"
    )
    # `scripts/write_release_record.py` is deliberately absent.
    derived_files, derived_external = ra.derive_surface(_dict_reader(files))
    assert "scripts/write_release_record.py" not in derived_files
    assert derived_external == ["write_release_record"]


def test_derive_surface_raises_when_a_root_workflow_is_missing(ra) -> None:
    files = _base_files(ra)
    del files[ra.ROOT_WORKFLOWS[0]]
    with pytest.raises(ra.ReleaseAuthorityError):
        ra.derive_surface(_dict_reader(files))


def test_authority_module_itself_is_excluded_from_recursive_discovery(ra) -> None:
    # The authority file is always a member (rule 5) but never appears as a
    # *discovered* scripts/ reference target from anywhere else — nothing in
    # this test wires that reference, so this simply proves inclusion does
    # not depend on any run body naming it.
    files = _base_files(ra)
    derived_files, _ = ra.derive_surface(_dict_reader(files))
    assert derived_files.count(ra.AUTHORITY_MODULE_PATH) == 1


# ── 2. Digest stability and sensitivity ─────────────────────────────────────


def test_digest_is_stable_regardless_of_input_list_order(ra) -> None:
    files = _base_files(ra)
    read = _dict_reader(files)
    file_list = sorted(files)
    digest_a = ra.authority_digest(file_list, [], read)
    digest_b = ra.authority_digest(list(reversed(file_list)), [], read)
    assert digest_a == digest_b
    assert digest_a.startswith("sha256:")


def test_digest_changes_when_any_surface_file_byte_changes(ra) -> None:
    files = _base_files(ra)
    file_list = sorted(files)
    before = ra.authority_digest(file_list, [], _dict_reader(files))

    mutated = dict(files)
    mutated[ra.AUTHORITY_MODULE_PATH] += "# one extra byte\n"
    after = ra.authority_digest(file_list, [], _dict_reader(mutated))

    assert before != after


def test_digest_changes_when_external_imports_change(ra) -> None:
    files = _base_files(ra)
    file_list = sorted(files)
    read = _dict_reader(files)
    without_external = ra.authority_digest(file_list, [], read)
    with_external = ra.authority_digest(file_list, ["dotmac_kernel"], read)
    assert without_external != with_external


def test_authority_digest_refuses_a_missing_file(ra) -> None:
    files = _base_files(ra)
    with pytest.raises(ra.ReleaseAuthorityError):
        ra.authority_digest(
            [*sorted(files), "scripts/absent.py"], [], _dict_reader(files)
        )


# Sensitivity plant named in the brief: a one-byte append to
# `scripts/tag_module_release.py` must change the digest and must fail the
# real-repo equality test below.
def test_sensitivity_plant_appending_one_byte_to_tag_module_release_changes_digest(
    ra,
) -> None:
    files = _base_files(ra)
    files[ra.ROOT_WORKFLOWS[0]] = (
        "jobs:\n  build:\n    steps:\n"
        "      - run: python scripts/tag_module_release.py\n"
    )
    files["scripts/tag_module_release.py"] = (
        "from write_release_record import ReleaseRecordError\n"
    )
    files["scripts/write_release_record.py"] = "import json\n"
    file_list, external = ra.derive_surface(_dict_reader(files))
    clean_digest = ra.authority_digest(file_list, external, _dict_reader(files))

    tampered = dict(files)
    tampered["scripts/tag_module_release.py"] += "#"
    tampered_digest = ra.authority_digest(file_list, external, _dict_reader(tampered))

    assert clean_digest != tampered_digest
    assert clean_digest != ra.authority_digest(
        file_list, external, _dict_reader(tampered)
    )


# ── 3. Ledger parser refusals ────────────────────────────────────────────────


def _valid_ledger_dict(digest: str = "sha256:" + "a" * 64) -> dict:
    return {
        "$comment": "generated",
        "schema": "ReleaseAuthorityLedger.v1",
        "active": {"digest": digest, "files": ["a"], "external_imports": []},
        "history": [digest],
    }


def test_parse_authority_ledger_accepts_a_valid_document(ra) -> None:
    text = json.dumps(_valid_ledger_dict())
    parsed = ra.parse_authority_ledger(text)
    assert parsed["active"]["digest"] == "sha256:" + "a" * 64


def test_parse_authority_ledger_refuses_wrong_schema(ra) -> None:
    doc = _valid_ledger_dict()
    doc["schema"] = "SomethingElse.v1"
    with pytest.raises(ra.ReleaseAuthorityError):
        ra.parse_authority_ledger(json.dumps(doc))


def test_parse_authority_ledger_refuses_active_digest_missing_from_history(ra) -> None:
    doc = _valid_ledger_dict()
    doc["history"] = ["sha256:" + "b" * 64]
    with pytest.raises(ra.ReleaseAuthorityError):
        ra.parse_authority_ledger(json.dumps(doc))


def test_parse_authority_ledger_refuses_duplicate_history_entry(ra) -> None:
    digest = "sha256:" + "a" * 64
    doc = _valid_ledger_dict(digest)
    doc["history"] = [digest, digest]
    with pytest.raises(ra.ReleaseAuthorityError):
        ra.parse_authority_ledger(json.dumps(doc))


def test_parse_authority_ledger_refuses_a_malformed_digest(ra) -> None:
    doc = _valid_ledger_dict()
    doc["active"]["digest"] = "not-a-digest"
    doc["history"] = ["not-a-digest"]
    with pytest.raises(ra.ReleaseAuthorityError):
        ra.parse_authority_ledger(json.dumps(doc))


def test_parse_authority_ledger_refuses_duplicate_json_keys(ra) -> None:
    digest = "sha256:" + "a" * 64
    text = (
        '{"schema": "ReleaseAuthorityLedger.v1", '
        f'"active": {{"digest": "{digest}", "files": [], "external_imports": []}}, '
        f'"active": {{"digest": "{digest}", "files": [], "external_imports": []}}, '
        f'"history": ["{digest}"]}}'
    )
    with pytest.raises(ra.ReleaseAuthorityError):
        ra.parse_authority_ledger(text)


def test_parse_authority_ledger_refuses_unexpected_top_level_key(ra) -> None:
    doc = _valid_ledger_dict()
    doc["surprise"] = True
    with pytest.raises(ra.ReleaseAuthorityError):
        ra.parse_authority_ledger(json.dumps(doc))


# ── 4. Real git: commit_reader and reconstruct_at ───────────────────────────


def _run_git(args: list[str], *, cwd: Path) -> str:
    result = subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _run_git(["init", "--initial-branch=main"], cwd=path)
    _run_git(["config", "user.name", "Test Runner"], cwd=path)
    _run_git(["config", "user.email", "test@example.invalid"], cwd=path)


def _commit_all(path: Path, message: str) -> str:
    _run_git(["add", "-A"], cwd=path)
    _run_git(["commit", "-m", message], cwd=path)
    return _run_git(["rev-parse", "HEAD"], cwd=path).strip()


def test_commit_reader_reads_bytes_at_a_commit_and_none_for_missing_path(
    ra, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "present.txt").write_text("hello\n", encoding="utf-8")
    sha = _commit_all(repo, "add present.txt")

    read = ra.commit_reader(repo, sha)
    assert read("present.txt") == "hello\n"
    assert read("absent.txt") is None


def test_reconstruct_at_differs_across_two_commits_changing_one_file(
    ra, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "surface.py").write_text("VALUE = 1\n", encoding="utf-8")
    first_sha = _commit_all(repo, "first")

    (repo / "surface.py").write_text("VALUE = 2\n", encoding="utf-8")
    second_sha = _commit_all(repo, "second")

    digest_one = ra.reconstruct_at(repo, first_sha, ["surface.py"], [])
    digest_two = ra.reconstruct_at(repo, second_sha, ["surface.py"], [])
    assert digest_one != digest_two


def test_commit_reader_refuses_a_non_sha(ra, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "f.txt").write_text("x", encoding="utf-8")
    _commit_all(repo, "init")
    with pytest.raises(ra.ReleaseAuthorityError):
        ra.commit_reader(repo, "not-a-sha")


# ── 5. Enforcement: this repository's own ledger must match the worktree ───


def test_this_repositorys_ledger_matches_its_own_derived_surface(ra) -> None:
    """The load-bearing check: a surface change must update the ledger.

    Derives the real surface from THIS worktree and compares it to the
    checked-in `docs/inventories/release-authority.json`. If this fails, the
    fix is `python scripts/release_authority.py write`, never editing the
    ledger by hand.
    """
    ledger_path = PROJECT_ROOT / "docs" / "inventories" / "release-authority.json"
    if not ledger_path.is_file():
        pytest.fail(
            f"{ledger_path} does not exist — run "
            "`python scripts/release_authority.py write`"
        )
    ledger = ra.parse_authority_ledger(ledger_path.read_text(encoding="utf-8"))

    read = ra.worktree_reader(PROJECT_ROOT)
    derived_files, derived_external = ra.derive_surface(read)

    assert derived_files == ledger["active"]["files"]
    assert derived_external == ledger["active"]["external_imports"]

    actual_digest = ra.authority_digest(derived_files, derived_external, read)
    assert actual_digest == ledger["active"]["digest"]


def test_sensitivity_plant_against_the_real_repo_appending_one_byte_fails_equality(
    ra,
) -> None:
    """Same sensitivity proof as above, against the real repository's files.

    Wraps the real worktree reader so `scripts/tag_module_release.py` reads
    back with one extra byte appended, and shows the resulting digest no
    longer equals the checked-in ledger's active digest — proving the
    equality check in the previous test is actually sensitive to a real
    surface file's content, not vacuously true.
    """
    ledger_path = PROJECT_ROOT / "docs" / "inventories" / "release-authority.json"
    if not ledger_path.is_file():
        pytest.skip("no ledger yet to compare a tampered digest against")
    ledger = ra.parse_authority_ledger(ledger_path.read_text(encoding="utf-8"))

    real_read = ra.worktree_reader(PROJECT_ROOT)

    def tampered_read(path: str) -> str | None:
        text = real_read(path)
        if text is None:
            return None
        if path == "scripts/tag_module_release.py":
            return text + "#"
        return text

    derived_files, derived_external = ra.derive_surface(tampered_read)
    tampered_digest = ra.authority_digest(
        derived_files, derived_external, tampered_read
    )

    assert tampered_digest != ledger["active"]["digest"]
