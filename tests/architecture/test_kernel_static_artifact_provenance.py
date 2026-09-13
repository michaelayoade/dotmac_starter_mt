"""The Kernel release binds generated static bytes to both built artifacts."""

from __future__ import annotations

import importlib.util
import io
import os
import stat
import subprocess
import sys
import tarfile
import zipfile
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "packages" / "dotmac-kernel" / "scripts" / "verify_static_artifact.py"
INSPECTOR = ROOT / "packages" / "dotmac-kernel" / "scripts" / "inspect_dist.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "release-kernel.yml"


def _checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("kernel_static_artifact", CHECKER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _artifacts(
    tmp_path: Path,
    *,
    source: Mapping[str, bytes] | None = None,
    wheel: Mapping[str, bytes] | None = None,
    sdist: Mapping[str, bytes] | None = None,
) -> tuple[Path, Path, Path]:
    source_files = dict(
        source
        if source is not None
        else {
            "css/main.css": b"compiled css\n",
            "css/src/main.css": b"tailwind input\n",
            "js/runtime.js": b"runtime\n",
        }
    )
    wheel_files = dict(source_files if wheel is None else wheel)
    sdist_files = dict(source_files if sdist is None else sdist)

    static_root = tmp_path / "static"
    for name, content in source_files.items():
        target = static_root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    wheel_path = tmp_path / "dotmac_kernel-0.1.0a100-py3-none-any.whl"
    with zipfile.ZipFile(wheel_path, mode="w") as archive:
        for name, content in wheel_files.items():
            archive.writestr(f"dotmac_kernel/static/{name}", content)

    sdist_path = tmp_path / "dotmac_kernel-0.1.0a100.tar.gz"
    with tarfile.open(sdist_path, mode="w:gz") as archive:
        for name, content in sdist_files.items():
            info = tarfile.TarInfo(
                f"dotmac_kernel-0.1.0a100/src/dotmac_kernel/static/{name}"
            )
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return static_root, wheel_path, sdist_path


def _verify(
    checker: ModuleType,
    source: Path,
    wheel: Path,
    sdist: Path,
    *,
    tracked: frozenset[str] | None = None,
) -> tuple[int, str]:
    if tracked is None:
        source_names = frozenset(checker.source_files(source))
        tracked = source_names - checker.GENERATED_STATIC_FILES
    return checker.verify(source, wheel, sdist, tracked=tracked)


def test_matching_built_source_wheel_and_sdist_are_accepted(tmp_path: Path) -> None:
    checker = _checker()
    source, wheel, sdist = _artifacts(tmp_path)
    count, digest = _verify(checker, source, wheel, sdist)
    assert count == 3
    assert len(digest) == 64


def test_a_tracked_set_shaped_like_the_real_repository_is_accepted(
    tmp_path: Path,
) -> None:
    """Runs the full pipeline against a fixture shaped like the real
    dotmac-kernel static tree: 15 tracked source files plus the one
    declared generated stylesheet, 16 files total in the built tree.

    `tracked` is written out independently of `source_files` below (the way
    `tracked_files()` would report it from git) instead of being derived
    from `source_files` by subtracting `GENERATED_STATIC_FILES` -- that
    shortcut (used by `_verify`'s default) can never disagree with itself,
    so it cannot exercise the real 15-tracked/16-shipped shape the way this
    fixture does. This drives `tracked_files`'s *contract* (not the git
    subprocess itself), `require_declared_generated_set`, and both archive
    comparisons together, without building a real wheel.
    """
    checker = _checker()
    source_files = {
        "css/main.css": b"compiled css\n",
        "css/src/main.css": b"tailwind input\n",
        "js/htmx.min.js": b"htmx\n",
        "js/alpine.min.js": b"alpine\n",
        "js/csrf.js": b"csrf\n",
        "js/components.js": b"components\n",
        "fonts/fonts.css": b"fonts\n",
        "fonts/Outfit-400.woff2": b"outfit400\n",
        "fonts/Outfit-500.woff2": b"outfit500\n",
        "fonts/Outfit-600.woff2": b"outfit600\n",
        "fonts/Outfit-700.woff2": b"outfit700\n",
        "fonts/Outfit-800.woff2": b"outfit800\n",
        "fonts/PlusJakartaSans-400.woff2": b"pjs400\n",
        "fonts/PlusJakartaSans-500.woff2": b"pjs500\n",
        "fonts/PlusJakartaSans-600.woff2": b"pjs600\n",
        "fonts/PlusJakartaSans-700.woff2": b"pjs700\n",
    }
    tracked = frozenset(
        {
            "css/src/main.css",
            "js/htmx.min.js",
            "js/alpine.min.js",
            "js/csrf.js",
            "js/components.js",
            "fonts/fonts.css",
            "fonts/Outfit-400.woff2",
            "fonts/Outfit-500.woff2",
            "fonts/Outfit-600.woff2",
            "fonts/Outfit-700.woff2",
            "fonts/Outfit-800.woff2",
            "fonts/PlusJakartaSans-400.woff2",
            "fonts/PlusJakartaSans-500.woff2",
            "fonts/PlusJakartaSans-600.woff2",
            "fonts/PlusJakartaSans-700.woff2",
        }
    )
    assert len(tracked) == 15
    assert len(source_files) == 16
    assert "css/main.css" not in tracked

    static_root, wheel_path, sdist_path = _artifacts(tmp_path, source=source_files)
    count, digest = checker.verify(static_root, wheel_path, sdist_path, tracked=tracked)
    assert count == 16
    assert len(digest) == 64


def test_the_generated_stylesheet_is_not_a_tracked_source() -> None:
    checker = _checker()
    static_root = (
        ROOT / "packages" / "dotmac-kernel" / "src" / "dotmac_kernel" / "static"
    )
    # `css/main.css` is gitignored and may not exist in this checkout (it is
    # a build artifact), so the enforcement below is exercised against a
    # synthetic source tree built from the REAL, git-derived tracked set --
    # not against `checker.source_files(static_root)`, which would depend on
    # whether `npm run css:build` has already run.
    tracked = checker.tracked_files(ROOT, static_root)
    assert tracked
    assert checker.GENERATED_STATIC_FILES == frozenset({"css/main.css"})
    assert tracked.isdisjoint(checker.GENERATED_STATIC_FILES)

    # Exercise `require_declared_generated_set` itself (not merely the
    # `GENERATED_STATIC_FILES` constant): a post-build tree that is exactly
    # the real tracked set plus the declared generated stylesheet is
    # accepted...
    built_source = {name: b"" for name in tracked} | {"css/main.css": b"compiled\n"}
    checker.require_declared_generated_set(built_source, tracked)

    # ...but deleting the enforcement's effect -- a built tree missing the
    # generated stylesheet -- is refused, proving this test would fail if
    # the enforcement it names were removed.
    with pytest.raises(
        checker.StaticArtifactRefusal, match="generated-file declaration"
    ):
        checker.require_declared_generated_set({name: b"" for name in tracked}, tracked)

    # A built tree that also smuggles in an extra, undeclared generated file
    # is refused too.
    with pytest.raises(
        checker.StaticArtifactRefusal, match="generated-file declaration"
    ):
        checker.require_declared_generated_set(
            built_source | {"css/unexpected.css": b"x\n"}, tracked
        )


@pytest.mark.parametrize("subject", ("wheel", "sdist"))
@pytest.mark.parametrize("difference", ("missing", "extra", "changed"))
def test_every_artifact_disagreement_is_refused(
    tmp_path: Path, subject: str, difference: str
) -> None:
    checker = _checker()
    source_files = {
        "css/main.css": b"compiled css\n",
        "js/runtime.js": b"runtime\n",
    }
    shipped = dict(source_files)
    if difference == "missing":
        shipped.pop("js/runtime.js")
    elif difference == "extra":
        shipped["unreviewed.js"] = b"extra\n"
    else:
        shipped["js/runtime.js"] = b"changed\n"
    kwargs = {subject: shipped}
    source, wheel, sdist = _artifacts(tmp_path, source=source_files, **kwargs)
    with pytest.raises(checker.StaticArtifactRefusal, match=subject):
        _verify(checker, source, wheel, sdist)


def test_missing_compiled_stylesheet_is_refused_before_artifact_comparison(
    tmp_path: Path,
) -> None:
    checker = _checker()
    source, wheel, sdist = _artifacts(tmp_path, source={"js/runtime.js": b"runtime\n"})
    with pytest.raises(checker.StaticArtifactRefusal, match="css/main.css"):
        _verify(checker, source, wheel, sdist)


@pytest.mark.parametrize(
    "difference", ("extra_generated", "compiled_tracked", "missing_tracked")
)
def test_the_generated_static_declaration_is_exact(
    tmp_path: Path, difference: str
) -> None:
    checker = _checker()
    source, wheel, sdist = _artifacts(tmp_path)
    source_names = frozenset(checker.source_files(source))
    tracked = source_names - checker.GENERATED_STATIC_FILES
    if difference == "extra_generated":
        extra = source / "css" / "unexpected.css"
        extra.write_bytes(b"unexpected\n")
    elif difference == "compiled_tracked":
        tracked |= checker.GENERATED_STATIC_FILES
    else:
        tracked |= {"js/missing.js"}
    with pytest.raises(
        checker.StaticArtifactRefusal,
        match="generated-file declaration",
    ):
        _verify(checker, source, wheel, sdist, tracked=tracked)


@pytest.mark.parametrize("member_type", (tarfile.SYMTYPE, tarfile.LNKTYPE))
@pytest.mark.parametrize("relative", ("", "/css/alias.css"))
def test_sdist_static_links_are_refused(
    tmp_path: Path, member_type: bytes, relative: str
) -> None:
    checker = _checker()
    source, wheel, sdist = _artifacts(tmp_path)
    source_files = checker.source_files(source)
    with tarfile.open(sdist, mode="w:gz") as archive:
        for name, content in source_files.items():
            info = tarfile.TarInfo(
                f"dotmac_kernel-0.1.0a100/src/dotmac_kernel/static/{name}"
            )
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        link = tarfile.TarInfo(
            f"dotmac_kernel-0.1.0a100/src/dotmac_kernel/static{relative}"
        )
        link.type = member_type
        link.linkname = "main.css"
        archive.addfile(link)
    diagnostic = "non-regular" if relative else "static root is not a directory"
    with pytest.raises(checker.StaticArtifactRefusal, match=diagnostic):
        _verify(checker, source, wheel, sdist)


def test_source_static_hard_links_are_refused(tmp_path: Path) -> None:
    checker = _checker()
    static_root = tmp_path / "static"
    static_root.mkdir()
    original = static_root / "css" / "main.css"
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(b"compiled css\n")
    linked = static_root / "css" / "alias.css"
    # A hard link is a second directory entry for the SAME inode: st_nlink
    # rises above 1 for BOTH names, there is no separate "this one is the
    # link" bit to inspect, and the bytes read back are identical to the
    # original -- exactly the shape the byte comparison alone cannot catch.
    os.link(original, linked)
    with pytest.raises(checker.StaticArtifactRefusal, match="hard-linked"):
        checker.source_files(static_root)


def test_source_static_ordinary_file_still_passes(tmp_path: Path) -> None:
    # Positive control: an ordinary, non-hard-linked file must still pass,
    # proving the check above triggers on link count rather than on any
    # file under the static root.
    checker = _checker()
    static_root = tmp_path / "static"
    static_root.mkdir()
    original = static_root / "css" / "main.css"
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(b"compiled css\n")
    files = checker.source_files(static_root)
    assert files == {"css/main.css": b"compiled css\n"}


@pytest.mark.parametrize(
    "mode",
    (stat.S_IFLNK, stat.S_IFSOCK, stat.S_IFCHR, stat.S_IFBLK, stat.S_IFIFO),
)
def test_wheel_static_links_are_refused(tmp_path: Path, mode: int) -> None:
    # All FIVE POSIX non-regular types the predicate checks are exercised
    # here, not just a subset: the five S_IS* calls are structurally
    # identical one-line checks, exactly the shape a copy-paste error in
    # any one of them would survive undetected.
    checker = _checker()
    source, wheel, sdist = _artifacts(tmp_path)
    source_files = checker.source_files(source)
    legit_content = source_files["css/main.css"]
    with zipfile.ZipFile(wheel, mode="w") as archive:
        for name, content in source_files.items():
            archive.writestr(f"dotmac_kernel/static/{name}", content)
        # The stored CONTENT below is byte-identical to a real file
        # (css/main.css) -- only the mode bit in the upper 16 bits of
        # external_attr says "materialise this as a non-regular member".
        # A test that instead used mismatched content would pass for the
        # wrong reason: the byte comparison would already catch that.
        link_info = zipfile.ZipInfo("dotmac_kernel/static/css/alias.css")
        link_info.external_attr = (mode | 0o777) << 16
        archive.writestr(link_info, legit_content)
    with pytest.raises(checker.StaticArtifactRefusal, match="non-regular"):
        _verify(checker, source, wheel, sdist)


def _active_line_index(lines: list[str], needle: str, *, label: str) -> int:
    """The line number of the sole line containing `needle` that is NOT
    commented out (ignoring leading whitespace before `#`). A substring
    check alone (`needle in text`) is satisfied whether the line is live or
    `#`-commented into inertness; this refuses to treat the two as the
    same and fails loudly if `needle` is duplicated or entirely absent.
    """
    matches = [
        index
        for index, line in enumerate(lines)
        if needle in line and not line.strip().startswith("#")
    ]
    assert len(matches) == 1, (
        f"{label}: expected exactly one ACTIVE (non-commented) line "
        f"containing {needle!r}, found {len(matches)}"
    )
    return matches[0]


def test_release_build_orders_full_css_build_before_artifact_inspection() -> None:
    workflow_lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    css_build_line = _active_line_index(
        workflow_lines, "npm ci && npm run css:build", label="workflow"
    )
    poetry_build_line = _active_line_index(
        workflow_lines, "poetry build", label="workflow"
    )
    inspect_line = _active_line_index(
        workflow_lines,
        "packages/dotmac-kernel/scripts/inspect_dist.sh",
        label="workflow",
    )
    assert css_build_line < poetry_build_line < inspect_line

    inspector_lines = INSPECTOR.read_text(encoding="utf-8").splitlines()
    _active_line_index(
        inspector_lines,
        'STATIC_PROVENANCE="${SCRIPT_DIR}/verify_static_artifact.py"',
        label="inspect_dist.sh",
    )
    _active_line_index(
        inspector_lines, '--source "$KERNEL_SRC/static"', label="inspect_dist.sh"
    )
    _active_line_index(
        inspector_lines,
        '--repository-root "$REPOSITORY_ROOT"',
        label="inspect_dist.sh",
    )
    _active_line_index(inspector_lines, '--wheel "$WHEEL"', label="inspect_dist.sh")
    _active_line_index(inspector_lines, '--sdist "$SDIST"', label="inspect_dist.sh")


def _run_inspect_dist(
    script_text: str, tmp_path: Path
) -> subprocess.CompletedProcess[str]:
    """Materialise `script_text` as `inspect_dist.sh` at the SAME relative
    layout the real file lives at (`<root>/packages/dotmac-kernel/scripts/`
    next to `<root>/packages/dotmac-kernel/src/dotmac_kernel/`), and run it
    for real.

    The stub verifier is placed exactly where the script's OWN
    `SCRIPT_DIR`/`BASH_SOURCE`-based resolution computes `STATIC_PROVENANCE`
    to be -- nothing about that resolution logic is copied or re-derived,
    only the two things that must be hermetic (the interpreter, via the
    script's existing `INSPECT_PYTHON` override, and the verifier it
    resolves) are substituted. This runs `script_text` byte-for-byte,
    unlike a reconstructed snippet, so it catches suppression ANYWHERE in
    the file: `|| true` on the invocation line, a `set +e` before it, the
    call moved into a dead branch, or the call commented out.
    """
    scripts_dir = tmp_path / "packages" / "dotmac-kernel" / "scripts"
    scripts_dir.mkdir(parents=True)
    (tmp_path / "packages" / "dotmac-kernel" / "src" / "dotmac_kernel").mkdir(
        parents=True
    )

    inspect_dist = scripts_dir / "inspect_dist.sh"
    inspect_dist.write_text(script_text)
    inspect_dist.chmod(0o755)

    # The stub verifier: an unconditional refusal, standing in for a real
    # one. It is placed at the exact path inspect_dist.sh computes for
    # STATIC_PROVENANCE, not passed in as an argument or env var.
    (scripts_dir / "verify_static_artifact.py").write_text(
        "import sys\nsys.exit('STATIC ARTIFACT REFUSED: stub refusal')\n"
    )

    # inspect_dist.sh already exposes PY="${INSPECT_PYTHON:-python3}" as an
    # override seam. The stub dispatcher no-ops every network-requiring
    # tooling step (pip/twine/check-wheel-contents, and the inline heredoc
    # checks) and delegates ONLY the static-provenance script invocation to
    # a real interpreter, so that call's exit status is genuine.
    stub_python = tmp_path / "stub_python.py"
    stub_python.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        f"REAL_PYTHON = {sys.executable!r}\n"
        "args = sys.argv[1:]\n"
        "if not args:\n"
        "    sys.exit(0)\n"
        "if args[0] == '-m':\n"
        "    sys.exit(0)\n"
        "if args[0] == '-':\n"
        "    sys.stdin.read()\n"
        "    sys.exit(0)\n"
        "os.execv(REAL_PYTHON, [REAL_PYTHON, *args])\n"
    )
    stub_python.chmod(0o755)

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "dotmac_kernel-0.1.0a100-py3-none-any.whl").write_bytes(b"")
    (dist_dir / "dotmac_kernel-0.1.0a100.tar.gz").write_bytes(b"")

    env = dict(os.environ)
    env["INSPECT_PYTHON"] = str(stub_python)

    return subprocess.run(  # noqa: S603 - fixed args, hermetic stub interpreter
        ("bash", str(inspect_dist), str(dist_dir)),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_a_failing_static_provenance_gate_fails_inspect_dist_sh(tmp_path: Path) -> None:
    """Hermetic: no network, no real build, no npm -- every step other than
    the static-provenance invocation is stubbed to a no-op, and that
    invocation runs a two-line stub verifier instead of a real wheel/sdist
    comparison. See `test_a_swallowed_static_provenance_gate_is_caught_...`
    below for the sensitivity proof that this harness actually catches
    suppression, rather than merely running successfully.
    """
    real_script = INSPECTOR.read_text(encoding="utf-8")
    result = _run_inspect_dist(real_script, tmp_path)
    assert result.returncode != 0
    assert "PASS" not in result.stdout


@pytest.mark.parametrize(
    "mutation",
    (
        "suffixed_with_or_true",
        "commented_out",
        "errexit_disabled_before_the_call",
        "moved_into_a_dead_branch",
    ),
)
def test_a_swallowed_static_provenance_gate_is_caught_by_the_harness_above(
    tmp_path: Path, mutation: str
) -> None:
    """Sensitivity proof for the test above, covering four DIFFERENT
    suppression mechanisms so that catching one is not mistaken for
    catching the class:

    - `suffixed_with_or_true`: `|| true` appended right after the real
      invocation's closing line (a suffix on the invocation line itself).
    - `commented_out`: the whole invocation `#`-commented out with its text
      left intact (removes the line from execution entirely).
    - `errexit_disabled_before_the_call`: a bare `set +e` inserted
      immediately before the invocation (disables `errexit` for the rest
      of the script; the line itself is untouched and uncommented).
    - `moved_into_a_dead_branch`: the invocation wrapped in
      `if false; then ... fi` (the line is untouched, uncommented, and
      unsuffixed -- it is simply unreachable).

    All four plants must make THE SAME harness report success
    (`returncode == 0`) here -- which is exactly what would turn
    `test_a_failing_static_provenance_gate_fails_inspect_dist_sh` red if
    any of them ever landed in the real file, since that test hardcodes
    `returncode != 0` against this identical, unmutated script.
    """
    real_script = INSPECTOR.read_text(encoding="utf-8")
    invocation_start = real_script.index('"$PY" "$STATIC_PROVENANCE" \\')
    closing_line = '--sdist "$SDIST"'
    invocation_end = real_script.index(closing_line, invocation_start) + len(
        closing_line
    )
    invocation = real_script[invocation_start:invocation_end]

    if mutation == "suffixed_with_or_true":
        mutated = (
            real_script[:invocation_end] + " || true" + real_script[invocation_end:]
        )
    elif mutation == "commented_out":
        commented = "\n".join(f"# {line}" for line in invocation.splitlines())
        mutated = (
            real_script[:invocation_start] + commented + real_script[invocation_end:]
        )
    elif mutation == "errexit_disabled_before_the_call":
        mutated = (
            real_script[:invocation_start] + "set +e\n" + real_script[invocation_start:]
        )
    else:
        mutated = (
            real_script[:invocation_start]
            + "if false; then\n"
            + invocation
            + "\nfi\n"
            + real_script[invocation_end:]
        )

    result = _run_inspect_dist(mutated, tmp_path)
    assert result.returncode == 0
