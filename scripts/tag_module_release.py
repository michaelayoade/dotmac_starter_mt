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
import json
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import release_authority  # noqa: E402
from write_release_record import (  # noqa: E402
    ReleaseRecordError,
    canonical_smoke_dependency_wheels,
    module_wheel_digest,
    parse_module_release_tag_evidence,
    render_module_release_tag_evidence,
)

REPO_ROOT = SCRIPTS_DIR.parent


def compute_and_check_authority_digest(*, repo_root: Path) -> str:
    """The release-authority digest of THIS checkout, refused if stale.

    Computed from the same surface `scripts/release_authority.py check` uses
    (``derive_surface`` + ``authority_digest``) and required to equal the
    checked-in ledger's ``active.digest`` at this same checkout: a run whose
    authority ledger is stale (or was tampered with) is not an authorized
    commit, and must never mint a tag.
    """
    try:
        read = release_authority.worktree_reader(repo_root)
        files, external = release_authority.derive_surface(read)
        digest = release_authority.authority_digest(files, external, read)
        ledger_path = repo_root / release_authority.LEDGER_PATH
        ledger = release_authority.parse_authority_ledger(
            ledger_path.read_text(encoding="utf-8")
        )
    except (release_authority.ReleaseAuthorityError, OSError) as failure:
        raise ReleaseRecordError(
            f"could not derive or read the release authority: {failure}"
        ) from failure
    active_digest = ledger["active"]["digest"]
    if digest != active_digest:
        raise ReleaseRecordError(
            f"this checkout's release authority digest ({digest}) does not "
            f"match the ledger's active digest ({active_digest}); refusing "
            "to tag under a stale or tampered release authority"
        )
    return digest


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


def read_smoke_dependencies(path: str) -> list[dict[str, str]]:
    """The `SmokeDependencyWheels.v1` manifest `release_module.py verify-wheel`
    wrote: the exact kernel/dependency wheels the smoke installed."""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as failure:
        raise ReleaseRecordError(
            f"cannot read the smoke dependency manifest {path}: {failure}"
        ) from failure
    if (
        not isinstance(document, dict)
        or set(document) != {"schema", "wheels"}
        or document["schema"] != "SmokeDependencyWheels.v1"
    ):
        raise ReleaseRecordError(f"{path} is not a SmokeDependencyWheels.v1 manifest")
    return canonical_smoke_dependency_wheels(document["wheels"])


def existing_tag(tag: str, *, remote: str, cwd: Path) -> tuple[str, str, str] | None:
    """``(tag_object, peeled_commit, message)`` of the REMOTE tag, or None.

    The remote is the authority: the tag is fetched without force, so a
    DIFFERENT local tag of the same name makes the fetch fail and is refused,
    and the local tag object must then equal the remote one. A tag that
    exists only locally is refused outright — it was never published.
    """
    listed = _git("ls-remote", remote, f"refs/tags/{tag}", cwd=cwd)
    if listed.returncode != 0:
        raise ReleaseRecordError(
            f"could not query {remote} for tag {tag}: "
            f"{listed.stderr.strip() or 'no stderr'}"
        )
    remote_object = ""
    for line in listed.stdout.splitlines():
        sha, _, ref = line.partition("\t")
        if ref == f"refs/tags/{tag}":
            remote_object = sha
    if not remote_object:
        local = _git("rev-parse", "-q", "--verify", f"refs/tags/{tag}", cwd=cwd)
        if local.returncode == 0:
            raise ReleaseRecordError(
                f"{tag} exists only locally, never on {remote}; refusing"
            )
        return None
    fetched = _git(
        "fetch", "--no-tags", remote, f"refs/tags/{tag}:refs/tags/{tag}", cwd=cwd
    )
    if fetched.returncode != 0:
        raise ReleaseRecordError(
            f"could not fetch {tag} from {remote} without force (a different "
            f"local tag?): {fetched.stderr.strip() or 'no stderr'}"
        )
    local_object = _git("rev-parse", f"refs/tags/{tag}", cwd=cwd).stdout.strip()
    if local_object != remote_object:
        raise ReleaseRecordError(
            f"{tag} local tag object {local_object} differs from {remote}'s "
            f"{remote_object}; refusing"
        )
    if _git("cat-file", "-t", local_object, cwd=cwd).stdout.strip() != "tag":
        raise ReleaseRecordError(f"{tag} is not an annotated tag; refusing")
    peeled = _git("rev-parse", f"refs/tags/{tag}^{{commit}}", cwd=cwd).stdout.strip()
    body = _git("cat-file", "tag", local_object, cwd=cwd).stdout
    _, separator, message = body.partition("\n\n")
    if not separator:
        raise ReleaseRecordError(f"{tag} tag object has no message body")
    if message.endswith("\n"):
        message = message[: -len("\n")]
    return local_object, peeled, message


def accept_identical_rerun_tag(
    *, tag: str, commit: str, message: str, remote: str, cwd: Path
) -> bool:
    """A rerun of the SAME run may accept the tag its earlier attempt pushed.

    Only when the tag's canonical bytes equal exactly what this run would
    write (which binds run id, source run id, authority digest, wheel
    filename and wheel digest), the tag object is the same locally and on the
    remote, and the peeled commit is this run's commit. Returns False when no
    tag exists; raises on any mismatch. Never forces, deletes or recreates.
    """
    state = existing_tag(tag, remote=remote, cwd=cwd)
    if state is None:
        return False
    _tag_object, peeled, existing_message = state
    if existing_message != message:
        raise ReleaseRecordError(
            f"{tag} already exists with different evidence; refusing — only a "
            "rerun of the run that wrote it, with byte-identical evidence, may "
            "accept an existing tag"
        )
    if peeled != commit:
        raise ReleaseRecordError(
            f"{tag} already exists at {peeled}, not {commit}; refusing"
        )
    return True


def adopt_recovered_tag(
    *,
    tag: str,
    commit: str,
    distribution: str,
    version: str,
    artifact_dir: str,
    original_run_id: str,
    remote: str,
    cwd: Path,
) -> None:
    """Recovery never replaces a tag; it may ADOPT one the original run wrote.

    The tag must exist; its canonical evidence must name the original run as
    both verification and source run, bind this distribution and version,
    carry exactly the recovered artifact's wheel filename and digest, and
    peel to the original run's commit. Whether that original run's tag step
    and publication actually succeeded is proven from the Actions API by
    `module_release_provenance.py`, not here.
    """
    state = existing_tag(tag, remote=remote, cwd=cwd)
    if state is None:
        raise ReleaseRecordError(f"{tag} does not exist; there is nothing to adopt")
    _tag_object, peeled, message = state
    evidence = parse_module_release_tag_evidence(message)
    if evidence["verification_run_id"] != original_run_id or (
        evidence["source_run_id"] != original_run_id
    ):
        raise ReleaseRecordError(
            f"{tag} evidence names runs {evidence['verification_run_id']!r}/"
            f"{evidence['source_run_id']!r}, not the original run "
            f"{original_run_id!r}; refusing to adopt"
        )
    if evidence["distribution"] != distribution or evidence["version"] != version:
        raise ReleaseRecordError(f"{tag} evidence names a different release")
    wheel_filename, wheel_sha256 = module_wheel_digest(
        artifact_dir, distribution=distribution, version=version
    )
    if (evidence["wheel_filename"], evidence["wheel_sha256"]) != (
        wheel_filename,
        wheel_sha256,
    ):
        raise ReleaseRecordError(
            f"{tag} evidence wheel digest differs from the recovered artifact; "
            "refusing to adopt"
        )
    if peeled != commit:
        raise ReleaseRecordError(
            f"{tag} peels to {peeled}, not the original run's {commit}; refusing"
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
    parser.add_argument(
        "--source-run-id",
        required=True,
        help="the run id that BUILT and PUBLISHED the wheel: equal to --run-id "
        "for a normal release, or the original failed run for a recovery",
    )
    parser.add_argument(
        "--smoke-dependencies",
        default=None,
        help="SmokeDependencyWheels.v1 manifest of the exact kernel/dependency "
        "wheels the smoke installed; required unless adopting",
    )
    parser.add_argument(
        "--adopt-original-run",
        default=None,
        help="recovery only: adopt the existing tag the original run wrote "
        "instead of creating one; never creates or replaces a tag",
    )
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    args = parser.parse_args(argv)

    cwd = Path(args.repo_root)
    try:
        # The run acting now must itself be an authorized commit, whether it
        # creates, re-accepts or adopts.
        authority_digest_value = compute_and_check_authority_digest(repo_root=cwd)
        if args.adopt_original_run is not None:
            adopt_recovered_tag(
                tag=args.tag,
                commit=args.commit,
                distribution=args.distribution,
                version=args.version,
                artifact_dir=args.artifact_dir,
                original_run_id=args.adopt_original_run,
                remote=args.remote,
                cwd=cwd,
            )
            print(
                f"adopted existing {args.tag} written by run {args.adopt_original_run}"
            )
            return 0
        if args.smoke_dependencies is None:
            raise ReleaseRecordError(
                "--smoke-dependencies is required when creating or re-accepting "
                "a tag"
            )
        smoke_wheels = read_smoke_dependencies(args.smoke_dependencies)
        wheel_filename, wheel_sha256 = module_wheel_digest(
            args.artifact_dir, distribution=args.distribution, version=args.version
        )
        message = render_module_release_tag_evidence(
            distribution=args.distribution,
            version=args.version,
            wheel_filename=wheel_filename,
            wheel_sha256=wheel_sha256,
            verification_run_id=args.run_id,
            source_run_id=args.source_run_id,
            release_authority_digest=authority_digest_value,
            smoke_dependency_wheels=smoke_wheels,
        )
        if accept_identical_rerun_tag(
            tag=args.tag,
            commit=args.commit,
            message=message,
            remote=args.remote,
            cwd=cwd,
        ):
            print(f"adopted existing identical {args.tag} from this same run")
            return 0
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
