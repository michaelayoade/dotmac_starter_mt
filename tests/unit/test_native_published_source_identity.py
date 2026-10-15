"""Native release source identity sees all build inputs at either revision."""

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/published_source_drift.py"


@pytest.fixture
def repository(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("native_source_drift", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)

    executable = shutil.which("git")
    assert executable is not None

    def git(*args):
        result = subprocess.run(  # noqa: S603 - fixed Git fixture commands
            [
                executable,
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.test",
                *args,
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    git("init", "--quiet")
    package = tmp_path / "packages/dotmac-example"
    package.mkdir(parents=True)
    (package / "src").mkdir()
    (package / "src/module.py").write_text("value = 1\n")
    (package / "pyproject.toml").write_text(
        '[project]\nname="dotmac-example"\nversion="1.0"\n'
    )

    def write(path, content="initial\n"):
        target = package / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def commit():
        git("add", ".")
        git("commit", "--quiet", "--allow-empty", "-m", "fixture")

    def publish(native=True):
        if native:
            for path in [
                "native/src/lib.rs",
                "native/shim.c",
                "native/vendor/input.tar.gz",
                "native/Cargo.lock",
                "native/Cargo.toml",
                "build.py",
            ]:
                write(path)
        commit()
        git("tag", "dotmac-example-v1.0")

    return module, package, write, commit, publish


@pytest.mark.parametrize(
    "path",
    [
        "native/src/lib.rs",
        "native/shim.c",
        "native/vendor/input.tar.gz",
        "native/Cargo.lock",
        "native/Cargo.toml",
        "build.py",
        "pyproject.toml",
    ],
)
def test_each_native_build_input_changes_identity_with_identical_python(
    repository, path
):
    module, package, write, commit, publish = repository
    publish()
    if path == "pyproject.toml":
        write(
            path,
            (package / path).read_text()
            + '\n[tool.dotmac.native]\ncompiler="changed"\n',
        )
    else:
        write(path, "changed\n")
    commit()
    (row,) = module.drifted()
    assert row["published_tree"] == row["head_tree"]
    assert row["native_source_schema"] == "NativePackageSource.v1"
    assert row["published_native_fingerprint"] != row["head_native_fingerprint"]


@pytest.mark.parametrize("action", ["add", "delete", "remove-all-native-inputs"])
def test_native_input_removal_cannot_unenroll_published_native(repository, action):
    module, package, write, commit, publish = repository
    publish()
    if action == "add":
        write("native/new.rs")
    elif action == "delete":
        (package / "native/src/lib.rs").unlink()
    else:
        shutil.rmtree(package / "native")
        (package / "build.py").unlink()
    commit()
    assert len(module.drifted()) == 1


def test_head_only_native_inputs_enroll_legacy_tag(repository):
    module, _package, write, commit, publish = repository
    publish(native=False)
    write("native/src/lib.rs")
    commit()
    assert len(module.drifted()) == 1


def test_equal_native_inputs_and_governance_docs_do_not_drift(repository):
    module, _package, write, commit, publish = repository
    publish()
    assert module.drifted() == []
    write("CHANGELOG.md", "new documentation\n")
    write("EXTRACTION.toml", "qualified_source = true\n")
    commit()
    assert module.drifted() == []


@pytest.mark.parametrize("missing", ["src", "pyproject.toml"])
def test_native_identity_refuses_missing_required_inputs(repository, missing):
    module, package, _write, commit, publish = repository
    publish()
    target = package / missing
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    commit()
    with pytest.raises(SystemExit, match="required native source input"):
        module.native_fingerprint("HEAD", "packages/dotmac-example")


def test_native_identity_refuses_an_unresolved_revision(repository):
    module, _package, _write, _commit, publish = repository
    publish()
    with pytest.raises(SystemExit, match="cannot resolve native source"):
        module.native_fingerprint("missing-revision", "packages/dotmac-example")


def test_legacy_findings_keep_original_tree_only_schema(repository):
    module, _package, write, commit, publish = repository
    publish(native=False)
    write("src/module.py", "value = 2\n")
    commit()
    (row,) = module.drifted()
    assert set(row) == {"distribution", "version", "tag", "published_tree", "head_tree"}


def test_native_flag_alone_at_publication_enrolls_after_flag_removal(repository):
    module, package, write, commit, publish = repository
    original = (package / "pyproject.toml").read_text()
    write("pyproject.toml", original + "\n[tool.dotmac.native]\n")
    publish(native=False)
    write("pyproject.toml", original)
    commit()
    (row,) = module.drifted()
    assert row["native_source_schema"] == "NativePackageSource.v1"


@pytest.mark.parametrize("refuse", [False, True])
def test_synthetic_cli_reports_native_only_drift_or_refuses_missing_source(
    repository, refuse
):
    module, package, write, commit, publish = repository
    publish()
    if refuse:
        shutil.rmtree(package / "src")
    else:
        write("native/src/lib.rs", "changed native computation\n")
    commit()
    script = module.REPO_ROOT / "scripts/published_source_drift.py"
    script.parent.mkdir()
    script.write_text(SCRIPT.read_text())
    result = subprocess.run(  # noqa: S603 - fixture script and fixed interpreter
        [sys.executable, str(script)],
        cwd=module.REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if refuse:
        assert result.returncode != 0
        assert "required native source input" in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert "src/ DRIFTED from the published tag:        1" in result.stdout
        assert "dotmac-example 1.0" in result.stdout


@pytest.mark.parametrize("path", ["build.py", "pyproject.toml"])
def test_native_identity_includes_top_level_file_modes(repository, path):
    module, package, _write, commit, publish = repository
    publish()
    (package / path).chmod(0o755)
    commit()
    (row,) = module.drifted()
    assert row["published_tree"] == row["head_tree"]
    assert row["published_native_fingerprint"] != row["head_native_fingerprint"]


def test_native_identity_includes_same_blob_file_to_symlink_transition(repository):
    module, package, _write, commit, publish = repository
    publish()
    hook = package / "build.py"
    target = hook.read_text()
    hook.unlink()
    hook.symlink_to(target)
    commit()
    published = module._source_objects("dotmac-example-v1.0", "packages/dotmac-example")
    current = module._source_objects("HEAD", "packages/dotmac-example")
    assert published["build.py"]["oid"] == current["build.py"]["oid"]
    assert published["build.py"]["mode"] != current["build.py"]["mode"]
    (row,) = module.drifted()
    assert row["published_native_fingerprint"] != row["head_native_fingerprint"]
