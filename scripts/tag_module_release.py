#!/usr/bin/env python3
"""Create the ONE annotated tag a governed module release is allowed to write.

Both `release-module.yml` and `recover-module-release.yml` call this script
right after registry verification succeeds. It owns two properties together,
because splitting them across two tools would let one drift from the other:

1. **The tag message is canonical evidence, not free text.** It is exactly
   one line of ``ModuleReleaseTagEvidence.v1`` JSON
   (``render_module_release_tag_evidence`` in ``write_release_record.py`` is
   the single renderer; its parser lives right beside it). The wheel digest
   is computed from the exact retained artifact directory that
   ``compare-published`` just verified — never rebuilt, never predicted.

2. **Tag creation is fail-closed.** A tag that already exists, locally or on
   the remote, is refused outright: this script never force-tags, never
   deletes, and never recreates an existing git tag. A release that needs a
   new tag writes a NEW version; it never overwrites the annotated tag object
   an earlier run (or a concurrent one) already pushed.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from write_release_record import (  # noqa: E402
    ReleaseRecordError,
    module_wheel_digest,
    render_module_release_tag_evidence,
)

REPO_ROOT = SCRIPTS_DIR.parent


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def tag_exists(tag: str, *, remote: str, cwd: Path) -> bool:
    """True if ``tag`` already exists locally OR on ``remote``.

    Both checks run every time: a tag pushed by a concurrent or earlier run
    but never fetched locally would otherwise be invisible to the local
    check, and a tag created locally but not yet pushed would be invisible to
    the remote check.
    """
    local = _git("rev-parse", "-q", "--verify", f"refs/tags/{tag}", cwd=cwd)
    if local.returncode == 0:
        return True
    remote_check = _git("ls-remote", "--tags", remote, f"refs/tags/{tag}", cwd=cwd)
    if remote_check.returncode != 0:
        raise ReleaseRecordError(
            f"could not query {remote} for existing tag {tag}: "
            f"{remote_check.stderr.strip() or 'no stderr'}"
        )
    return bool(remote_check.stdout.strip())


def create_and_push_tag(
    *, tag: str, commit: str, message: str, remote: str, cwd: Path
) -> None:
    """Refuse an existing tag; otherwise create and push exactly one ref.

    Never ``git tag -f``, never a delete, never a recreate: an existing tag —
    local or remote — is a refusal, full stop. The push is a plain,
    non-force push of the single new ref.
    """
    if tag_exists(tag, remote=remote, cwd=cwd):
        raise ReleaseRecordError(
            f"{tag} already exists locally or on {remote}; refusing to "
            "recreate, force, or delete an existing release tag — a release "
            "that needs a new tag ships a new version instead"
        )
    created = _git("tag", "-a", tag, "-m", message, commit, cwd=cwd)
    if created.returncode != 0:
        raise ReleaseRecordError(
            f"git tag -a {tag} failed: {created.stderr.strip() or 'no stderr'}"
        )
    pushed = _git("push", remote, f"refs/tags/{tag}", cwd=cwd)
    if pushed.returncode != 0:
        raise ReleaseRecordError(
            f"git push of {tag} to {remote} failed: "
            f"{pushed.stderr.strip() or 'no stderr'}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distribution", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument(
        "--artifact-dir",
        required=True,
        help="directory holding the exact retained wheel compare-published verified",
    )
    parser.add_argument(
        "--run-id",
        required=True,
        help="the run id of the job creating this tag (github.run_id)",
    )
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    args = parser.parse_args(argv)

    cwd = Path(args.repo_root)
    try:
        wheel_filename, wheel_sha256 = module_wheel_digest(
            args.artifact_dir, distribution=args.distribution, version=args.version
        )
        message = render_module_release_tag_evidence(
            distribution=args.distribution,
            version=args.version,
            wheel_filename=wheel_filename,
            wheel_sha256=wheel_sha256,
            verification_run_id=args.run_id,
        )
        create_and_push_tag(
            tag=args.tag,
            commit=args.commit,
            message=message,
            remote=args.remote,
            cwd=cwd,
        )
    except ReleaseRecordError as failure:
        print(f"module release tag REFUSED: {failure}", file=sys.stderr)
        return 1
    print(f"tagged {args.tag} on {args.commit}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
