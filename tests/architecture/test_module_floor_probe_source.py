"""Source sensitivity canaries; hosted installed execution remains the proof owner."""

import ast
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]


def controls(shell, python):
    assert "ModuleRegistry((module,))" in python
    assert "isinstance(namespaces, namespace_type)" in python
    assert '"${venv}/bin/python" -I' in shell
    assert 'if [[ -f "${cached}" ]]' in shell
    assert 'worktree remove --force "${tree}"' in shell
    assert "fetch --quiet --force --prune --prune-tags" in shell
    assert '"${module_wheel}"' in shell
    assert "check_origin(" in python
    assert "check_version(" in python
    assert "TENANT_AUDIT_LOG_V1" in python
    assert "MIGRATION_OWNER_LEDGER" in python
    assert "sha256" in python
    assert python.index(
        'importlib.import_module("dotmac_kernel.namespaces")'
    ) < python.index("importlib.import_module(args.name)")
    assert shell.count("poetry build -f wheel") == 2
    assert '"${repo_root}/${package_dir}" || return' not in shell
    assert "check_version(result[key], expected, key)" in python


def test_floor_controls_present():
    controls(
        (ROOT / "scripts/module_floor_check.sh").read_text(),
        (ROOT / "scripts/module_floor_probe.py").read_text(),
    )


@pytest.mark.parametrize(
    "token",
    [
        "ModuleRegistry((module,))",
        "check_origin(",
        "check_version(",
        "isinstance(namespaces, namespace_type)",
        "MIGRATION_OWNER_LEDGER",
    ],
)
def test_python_control_removal_is_detected(token):
    shell = (ROOT / "scripts/module_floor_check.sh").read_text()
    python = (
        (ROOT / "scripts/module_floor_probe.py").read_text().replace(token, "REMOVED")
    )
    with pytest.raises(AssertionError):
        controls(shell, python)


@pytest.mark.parametrize(
    "token",
    [
        '"${venv}/bin/python" -I',
        'if [[ -f "${cached}" ]]',
        "fetch --quiet --force --prune --prune-tags",
        'worktree remove --force "${tree}"',
    ],
)
def test_shell_control_removal_is_detected(token):
    shell = (
        (ROOT / "scripts/module_floor_check.sh").read_text().replace(token, "REMOVED")
    )
    python = (ROOT / "scripts/module_floor_probe.py").read_text()
    with pytest.raises(AssertionError):
        controls(shell, python)


def test_learning_literal_matches_all_current_source_tables():
    source = ast.parse((ROOT / "scripts/module_floor_probe.py").read_text())
    literal = next(
        node.value.args[0]
        for node in source.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "LEARNING_TABLES"
            for target in node.targets
        )
    )
    expected = ast.literal_eval(literal)
    actual = set()
    for path in (ROOT / "packages/dotmac-learning/src/dotmac_learning").glob(
        "*models.py"
    ):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "__tablename__"
                for target in node.targets
            ):
                actual.add(ast.literal_eval(node.value))
    assert len(expected) == 27
    assert actual == expected


def test_duplicate_module_build_is_detected():
    shell = (
        ROOT / "scripts/module_floor_check.sh"
    ).read_text() + "poetry build -f wheel"
    with pytest.raises(AssertionError):
        controls(shell, (ROOT / "scripts/module_floor_probe.py").read_text())


def retained_tag_arguments(shell):
    """Read only the bounded retention command; never execute the shell source."""
    commands = re.findall(
        r'^git -C "\$\{repo_root\}" for-each-ref \\\n'
        r'(?:[^\n]*\\\n)*[^\n]*kernel-tags\.txt"$',
        shell,
        flags=re.MULTILINE,
    )
    assert len(commands) == 1, "expected exactly one retained tag command"
    tokens = shlex.split(commands[0].replace("\\\n", " "))
    arguments = tokens[tokens.index("for-each-ref") + 1 : tokens.index(">")]
    assert len(arguments) == 2 and arguments[0].startswith("--format=")
    assert arguments[1].startswith("refs/tags/")
    return arguments


@pytest.fixture
def tag_metadata_fixture(tmp_path):
    """Hosted-only Git matching fixture; these tags are not published floor proof."""
    repo = tmp_path / "tag-metadata"
    repo.mkdir()
    git_executable = shutil.which("git")
    assert git_executable is not None, "Git is required for the metadata fixture"

    def git(*arguments):
        return subprocess.run(  # noqa: S603 - bounded Git arguments, no shell
            [
                git_executable,
                "-C",
                str(repo),
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "tag.gpgsign=false",
                "-c",
                "user.name=Floor metadata fixture",
                "-c",
                "user.email=floor-fixture@example.invalid",
                *arguments,
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout

    git("init", "--quiet")
    git("commit", "--quiet", "--allow-empty", "-m", "Git metadata fixture only")
    commit = git("rev-parse", "HEAD").strip()
    lightweight = "dotmac-kernel-v0.1.0a104"
    annotated = "dotmac-kernel-v0.1.0a105"
    git("tag", lightweight)
    git("tag", "-a", annotated, "-m", "Annotated metadata fixture only")
    git("tag", "dotmac-learning-v0.1.0a1")
    return git, {
        lightweight: (commit, ""),
        annotated: (git("rev-parse", "refs/tags/" + annotated).strip(), commit),
    }


def assert_retained_tag_metadata(shell, fixture):
    git, expected = fixture
    output = git("for-each-ref", *retained_tag_arguments(shell))
    rows = {}
    for line in output.splitlines():
        fields = line.split()
        assert len(fields) in {2, 3}
        name, object_id = fields[:2]
        assert name not in rows
        rows[name] = (object_id, fields[2] if len(fields) == 3 else "")
    assert rows == expected, "retained metadata must contain exactly both Kernel tags"
    return output


def test_retained_kernel_tags_include_objects_and_peeled_commit(tag_metadata_fixture):
    shell = (ROOT / "scripts/module_floor_check.sh").read_text()
    assert_retained_tag_metadata(shell, tag_metadata_fixture)


def test_missing_tag_glob_is_rejected_by_same_metadata_assertion(tag_metadata_fixture):
    shell = (ROOT / "scripts/module_floor_check.sh").read_text()
    pattern = "'refs/tags/dotmac-kernel-v*'"
    assert shell.count(pattern) == 1
    planted = shell.replace(pattern, "'refs/tags/dotmac-kernel-v'")
    git, _ = tag_metadata_fixture
    assert git("for-each-ref", *retained_tag_arguments(planted)) == ""
    with pytest.raises(AssertionError, match="exactly both Kernel tags"):
        assert_retained_tag_metadata(planted, tag_metadata_fixture)
