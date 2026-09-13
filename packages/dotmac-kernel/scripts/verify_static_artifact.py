"""Prove that Kernel release artifacts contain the built static tree exactly.

``static/css/main.css`` is generated from the repository-wide Tailwind inputs
before the Kernel artifacts are built.  It is intentionally not a tracked
source file, so a source-tree-only inventory cannot prove what a published
wheel serves.  This gate compares the post-build source tree byte-for-byte in
both directions with the wheel and sdist produced from it.
"""

from __future__ import annotations

import argparse
import hashlib
import stat
import subprocess
import sys
import tarfile
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath

WHEEL_PREFIX = "dotmac_kernel/static/"
SDIST_STATIC_ROOT = "/src/dotmac_kernel/static"
COMPILED_STYLESHEET = "css/main.css"
GENERATED_STATIC_FILES = frozenset({COMPILED_STYLESHEET})


class StaticArtifactRefusal(ValueError):
    """The built source tree and a release artifact do not agree."""


def _safe_relative_name(name: str, *, subject: str) -> str:
    if "\\" in name:
        raise StaticArtifactRefusal(f"{subject} contains a backslash path: {name!r}")
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise StaticArtifactRefusal(f"{subject} contains an unsafe path: {name!r}")
    return path.as_posix()


def source_files(static_root: Path) -> dict[str, bytes]:
    try:
        candidates = sorted(static_root.rglob("*"))
    except OSError as error:
        raise StaticArtifactRefusal(
            f"cannot enumerate {static_root}: {error}"
        ) from error
    files: dict[str, bytes] = {}
    for path in candidates:
        if path.is_symlink():
            raise StaticArtifactRefusal(
                f"source static tree contains a symlink: {path}"
            )
        if not path.is_file():
            continue
        try:
            nlink = path.stat().st_nlink
        except OSError as error:
            raise StaticArtifactRefusal(
                f"cannot stat source static file {path}: {error}"
            ) from error
        if nlink > 1:
            # A hard link is a second directory entry for the same inode:
            # st_nlink rises above 1 for every name pointing at it, there is
            # no separate "this is a link" bit to inspect (unlike a
            # symlink), and the bytes read back are identical to the
            # original -- exactly what the byte comparison cannot catch.
            #
            # nlink > 1 has two distinct causes and this refusal cannot
            # tell them apart on its own: (a) an adversarial or accidental
            # alias smuggled into the reviewed tree, the reason this check
            # exists; or (b) an ENVIRONMENTAL artifact of how the build
            # tree was materialised -- a content-addressable store,
            # `cp -al`, `rsync --link-dest`, or some cache-restore paths
            # all hard-link by design. No evidence yet establishes that
            # this repository's actual release runner produces a
            # single-link tree; that premise is stated here, not assumed.
            # Name both possibilities so a maintainer meeting this refusal
            # can tell in seconds which one to check, rather than only
            # hearing the security-sounding half.
            raise StaticArtifactRefusal(
                f"source static tree contains a hard-linked file "
                f"(nlink={nlink}): {path} -- this is either an unreviewed "
                "alias smuggled into the built tree, or an environmental "
                "artifact of how the tree was materialised (e.g. a "
                "content-addressable store, `cp -al`, `rsync --link-dest`, "
                "or a cache-restore step that hard-links); check how this "
                "build tree was produced before assuming the former"
            )
        relative = path.relative_to(static_root).as_posix()
        try:
            files[relative] = path.read_bytes()
        except OSError as error:
            raise StaticArtifactRefusal(
                f"cannot read source static file {path}: {error}"
            ) from error
    return files


def tracked_files(repository_root: Path, static_root: Path) -> frozenset[str]:
    repository = repository_root.resolve()
    static = static_root.resolve()
    try:
        relative_root = static.relative_to(repository).as_posix()
    except ValueError as error:
        raise StaticArtifactRefusal(
            f"static source {static} is outside repository {repository}"
        ) from error
    try:
        result = subprocess.run(  # noqa: S603 - fixed git command and arguments
            ("git", "-C", str(repository), "ls-files", "-z", "--", relative_root),
            check=False,
            capture_output=True,
        )
    except OSError as error:
        raise StaticArtifactRefusal(
            f"cannot enumerate tracked static files: {error}"
        ) from error
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise StaticArtifactRefusal(
            "git could not enumerate tracked static files: "
            f"{detail or result.returncode}"
        )
    prefix = relative_root + "/"
    tracked: set[str] = set()
    for raw_name in result.stdout.split(b"\0"):
        if not raw_name:
            continue
        try:
            name = raw_name.decode("utf-8")
        except UnicodeDecodeError as error:
            raise StaticArtifactRefusal(
                "tracked static path is not valid UTF-8"
            ) from error
        if not name.startswith(prefix):
            raise StaticArtifactRefusal(
                f"git returned a path outside {relative_root}: {name!r}"
            )
        tracked.add(_safe_relative_name(name.removeprefix(prefix), subject="git"))
    if not tracked:
        raise StaticArtifactRefusal(
            "git reported no tracked static files; the generated-set check "
            "would prove nothing"
        )
    return frozenset(tracked)


def require_declared_generated_set(
    source: Mapping[str, bytes], tracked: frozenset[str]
) -> None:
    source_names = frozenset(source)
    missing = sorted(tracked - source_names)
    generated = source_names - tracked
    if missing or generated != GENERATED_STATIC_FILES:
        raise StaticArtifactRefusal(
            "post-build static tree disagrees with its generated-file declaration: "
            f"missing_tracked={missing[:10]}, "
            f"expected_generated={sorted(GENERATED_STATIC_FILES)}, "
            f"actual_generated={sorted(generated)[:10]}"
        )


def wheel_files(wheel_path: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(wheel_path) as archive:
            for member in archive.infolist():
                if not member.filename.startswith(WHEEL_PREFIX) or member.is_dir():
                    continue
                relative = _safe_relative_name(
                    member.filename.removeprefix(WHEEL_PREFIX), subject=str(wheel_path)
                )
                # A zip member's Unix mode lives in the UPPER 16 bits of
                # external_attr (the lower 16 bits are DOS attributes). A zip
                # symlink is not required to store its target path as its
                # content, so a member can carry S_IFLNK here while its
                # stored bytes are byte-identical to a legitimate file — the
                # byte comparison below would never catch that; only the
                # mode bit can. Mirrors the sdist non-regular-member refusal.
                #
                # Many legitimate zip writers (including the stdlib's own
                # ZipFile.writestr default) set external_attr to PERMISSION
                # bits only (e.g. 0o600 << 16) with no S_IFMT type bits at
                # all, so S_ISREG on that mode is False even though the
                # member is an ordinary file. Checking "not regular" would
                # therefore false-positive on that common, harmless shape.
                # Refuse only the type bits a zip entry can meaningfully
                # carry that are NEVER a plain static file: a symlink or a
                # device/FIFO/socket special file.
                #
                # Stated premise about the WRITER, not just the reader: this
                # assumes external_attr's upper 16 bits are either 0 or a
                # real Unix `st_mode` with an intact S_IFMT nibble -- true
                # for CPython's own zipfile (writestr's permission-only
                # default) and for poetry-core 2.4.0's
                # normalize_file_permissions, which starts from the real
                # os.stat().st_mode and only masks the low permission bits,
                # leaving S_IFMT alone, so a genuine static file carries
                # S_IFREG. This is the toolchain that actually builds this
                # wheel (release-kernel.yml is Linux-only; CPython's
                # zipfile/os.stat always synthesise proper S_IFMT even on
                # Windows), so there is no live risk today. The assumption
                # is UNVERIFIED for a future non-CPython zip writer that
                # might place raw DOS attributes (which carry no S_IFMT
                # semantics at all) in the upper 16 bits instead -- such a
                # writer could misclassify a member here. Revisit this
                # comment, not the predicate, if the release toolchain ever
                # changes writers.
                mode = member.external_attr >> 16
                if (
                    stat.S_ISLNK(mode)
                    or stat.S_ISCHR(mode)
                    or stat.S_ISBLK(mode)
                    or stat.S_ISFIFO(mode)
                    or stat.S_ISSOCK(mode)
                ):
                    raise StaticArtifactRefusal(
                        f"{wheel_path} contains non-regular static member "
                        f"{relative!r}"
                    )
                if relative in files:
                    raise StaticArtifactRefusal(
                        f"{wheel_path} contains duplicate static member {relative!r}"
                    )
                files[relative] = archive.read(member)
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        raise StaticArtifactRefusal(
            f"cannot read wheel {wheel_path}: {error}"
        ) from error
    return files


def sdist_files(sdist_path: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    try:
        with tarfile.open(sdist_path, mode="r:gz") as archive:
            for member in archive.getmembers():
                if SDIST_STATIC_ROOT not in member.name:
                    continue
                root, suffix = member.name.split(SDIST_STATIC_ROOT, 1)
                if suffix and not suffix.startswith("/"):
                    continue
                if not root or "/" in root:
                    raise StaticArtifactRefusal(
                        f"{sdist_path} contains static data outside its release root: "
                        f"{member.name!r}"
                    )
                relative_name = suffix.removeprefix("/")
                if not relative_name:
                    if member.isdir():
                        continue
                    raise StaticArtifactRefusal(
                        f"{sdist_path} static root is not a directory: "
                        f"{member.name!r}"
                    )
                relative = _safe_relative_name(relative_name, subject=str(sdist_path))
                if member.isdir():
                    continue
                if not member.isfile():
                    raise StaticArtifactRefusal(
                        f"{sdist_path} contains non-regular static member "
                        f"{relative!r}"
                    )
                if relative in files:
                    raise StaticArtifactRefusal(
                        f"{sdist_path} contains duplicate static member {relative!r}"
                    )
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise StaticArtifactRefusal(
                        f"{sdist_path} cannot read static member {relative!r}"
                    )
                files[relative] = extracted.read()
    except (OSError, tarfile.TarError) as error:
        raise StaticArtifactRefusal(
            f"cannot read sdist {sdist_path}: {error}"
        ) from error
    return files


def manifest_digest(files: Mapping[str, bytes]) -> str:
    entries = sorted(
        (name, hashlib.sha256(content).hexdigest()) for name, content in files.items()
    )
    manifest = "\n".join(f"{name}  {digest}" for name, digest in entries)
    return hashlib.sha256(manifest.encode()).hexdigest()


def require_same_files(
    source: Mapping[str, bytes], shipped: Mapping[str, bytes], *, subject: str
) -> None:
    absent = sorted(source.keys() - shipped.keys())
    extra = sorted(shipped.keys() - source.keys())
    changed = sorted(
        name for name in source.keys() & shipped.keys() if source[name] != shipped[name]
    )
    if absent or extra or changed:
        raise StaticArtifactRefusal(
            f"{subject} static tree disagrees with the built source: "
            f"missing={absent[:10]}, extra={extra[:10]}, changed={changed[:10]}"
        )


def verify(
    static_root: Path,
    wheel_path: Path,
    sdist_path: Path,
    *,
    tracked: frozenset[str],
) -> tuple[int, str]:
    source = source_files(static_root)
    if not source:
        raise StaticArtifactRefusal(
            f"no files under {static_root}; "
            "the static provenance gate would prove nothing"
        )
    if COMPILED_STYLESHEET not in source:
        raise StaticArtifactRefusal(
            f"built source is missing {COMPILED_STYLESHEET}; run `npm run css:build`"
        )
    require_declared_generated_set(source, tracked)
    require_same_files(source, wheel_files(wheel_path), subject="wheel")
    require_same_files(source, sdist_files(sdist_path), subject="sdist")
    return len(source), manifest_digest(source)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--sdist", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        count, digest = verify(
            args.source,
            args.wheel,
            args.sdist,
            tracked=tracked_files(args.repository_root, args.source),
        )
    except StaticArtifactRefusal as error:
        print(f"STATIC ARTIFACT REFUSED: {error}", file=sys.stderr)
        return 1
    print(f"STATIC_ASSET_COUNT={count}")
    print(f"STATIC_ASSET_MANIFEST_SHA256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
