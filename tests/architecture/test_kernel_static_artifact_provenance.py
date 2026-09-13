"""The Kernel release binds generated static bytes to both built artifacts."""

from __future__ import annotations

import importlib.util
import io
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


def test_the_generated_stylesheet_is_not_a_tracked_source() -> None:
    checker = _checker()
    static_root = (
        ROOT / "packages" / "dotmac-kernel" / "src" / "dotmac_kernel" / "static"
    )
    tracked = checker.tracked_files(ROOT, static_root)
    assert tracked
    assert checker.GENERATED_STATIC_FILES == frozenset({"css/main.css"})
    assert tracked.isdisjoint(checker.GENERATED_STATIC_FILES)


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


def test_release_build_orders_full_css_build_before_artifact_inspection() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert workflow.count("npm ci && npm run css:build") == 1
    assert workflow.count("poetry build") == 1
    assert workflow.count("packages/dotmac-kernel/scripts/inspect_dist.sh") == 1
    assert workflow.index("npm ci && npm run css:build") < workflow.index(
        "poetry build"
    )
    assert workflow.index("poetry build") < workflow.index(
        "packages/dotmac-kernel/scripts/inspect_dist.sh"
    )
    inspector = INSPECTOR.read_text(encoding="utf-8")
    assert 'STATIC_PROVENANCE="${SCRIPT_DIR}/verify_static_artifact.py"' in inspector
    assert '--source "$KERNEL_SRC/static"' in inspector
    assert '--repository-root "$REPOSITORY_ROOT"' in inspector
    assert '--wheel "$WHEEL"' in inspector
    assert '--sdist "$SDIST"' in inspector
