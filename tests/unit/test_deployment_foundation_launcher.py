"""The controller must not be loaded from the tree it is about to deploy.

Every other guard in this facility — the authorization seam, signed evidence,
the ancestry refusal, the pinned toolchain — assumes the code performing the
check is the code that was released. Nothing established that.

The controller runs *against* a staged deploy tree whose contents are writable
by whoever controls the host. Python resolves imports through `sys.path`, and a
directory early on that path wins. A `dotmac_deployment_foundation/` inside the
staged tree replaces the facility with an edited copy whose `authorize()`
returns a grant for anything — and the run looks completely normal: same
command, same output, same green log lines.

## What these tests do NOT claim

They do not claim a process can verify its own integrity. By the time this code
runs, anything that was going to be shadowed already has been, and a
compromised copy would simply not call these functions.

The claim is narrower: **before anything irreversible, refuse if the loaded
facility is coming from somewhere it must not be.** That catches the stale copy
in the deploy tree, the `PYTHONPATH` left set, the editable install pointing at
a working directory on a production host. It does not defeat an adversary who
already owns the interpreter, and a test asserting otherwise would be theatre.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from dotmac_deployment_foundation.errors import PreconditionFailed
from dotmac_deployment_foundation.launcher import (
    identify_launcher,
    loaded_facility_root,
    refuse_untrusted_launcher,
)


def test_the_facility_reports_where_it_was_actually_loaded_from() -> None:
    """Derived from `__file__`, not from configuration.

    A configured answer to "what got loaded" is the thing being checked.
    """
    root = loaded_facility_root()
    assert (root / "launcher.py").is_file()
    assert root.is_absolute()


def test_an_ordinary_deploy_directory_is_permitted(tmp_path: Path) -> None:
    """The positive control.

    The facility legitimately runs from a venv, site-packages, or an editable
    install. Refusing those would make the guard fire constantly and get
    disabled, so this asserts the common case passes.
    """
    identity = refuse_untrusted_launcher(deploy_dir=tmp_path)
    assert identity.digest.startswith("sha256:")
    assert identity.root == str(loaded_facility_root())


def test_a_facility_inside_the_staged_tree_is_refused() -> None:
    """The core case: the input supplying its own validator.

    The deploy dir is set to a PARENT of the loaded facility, which is exactly
    the shape of `pip install -e` into the tree being deployed.
    """
    root = loaded_facility_root()
    with pytest.raises(PreconditionFailed, match="inside the staged deploy tree"):
        refuse_untrusted_launcher(deploy_dir=root.parent)


def test_the_facility_directory_itself_is_refused() -> None:
    """The boundary: equal, not merely contained."""
    with pytest.raises(PreconditionFailed, match="inside the staged deploy tree"):
        refuse_untrusted_launcher(deploy_dir=loaded_facility_root())


def test_a_staged_directory_on_sys_path_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not-yet-imported modules would resolve from the deployed tree.

    Nothing has been shadowed at this point — we already resolved elsewhere.
    The refusal is about the imports that have not happened yet, which under
    this codebase's lazy-import style is most of them.
    """
    staged = tmp_path / "deploy"
    (staged / "pkg").mkdir(parents=True)
    monkeypatch.syspath_prepend(str(staged / "pkg"))
    with pytest.raises(PreconditionFailed, match="sys.path contains"):
        refuse_untrusted_launcher(deploy_dir=staged)


def test_an_unrelated_sys_path_entry_is_not_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sensitivity's other half — only the staged tree is special."""
    monkeypatch.syspath_prepend(str(tmp_path / "somewhere-else"))
    refuse_untrusted_launcher(deploy_dir=tmp_path / "deploy")


def test_an_empty_sys_path_entry_does_not_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`''` means the cwd and appears in real interpreters."""
    monkeypatch.setattr(sys, "path", ["", *sys.path])
    refuse_untrusted_launcher(deploy_dir=tmp_path / "deploy")


# ── digest pinning ──────────────────────────────────────────────────────────


def test_a_matching_digest_is_accepted(tmp_path: Path) -> None:
    expected = identify_launcher().digest
    assert (
        refuse_untrusted_launcher(deploy_dir=tmp_path, expected_digest=expected).digest
        == expected
    )


def test_a_bare_hex_digest_is_accepted_too(tmp_path: Path) -> None:
    """Both spellings, same digest — the Control/Foundation format trap again."""
    bare = identify_launcher().digest.removeprefix("sha256:")
    refuse_untrusted_launcher(deploy_dir=tmp_path, expected_digest=bare)


def test_a_mismatched_digest_is_refused(tmp_path: Path) -> None:
    with pytest.raises(PreconditionFailed, match="not the released"):
        refuse_untrusted_launcher(deploy_dir=tmp_path, expected_digest="0" * 64)


def test_the_digest_is_stable_across_calls() -> None:
    """A digest that changed per call could never be compared to a receipt."""
    assert identify_launcher().digest == identify_launcher().digest


def test_the_digest_covers_file_names_not_only_contents(tmp_path: Path) -> None:
    """Renaming a module must change the digest.

    A content-only hash would treat `authorize.py` moved to `authorize.py.bak`
    plus a new `authorize.py` as no change at all — which is precisely the
    substitution worth catching.
    """
    from dotmac_deployment_foundation.launcher import _package_digest

    root = tmp_path / "pkg"
    root.mkdir()
    (root / "a.py").write_bytes(b"X")
    first = _package_digest(root)
    (root / "a.py").unlink()
    (root / "b.py").write_bytes(b"X")
    assert _package_digest(root) != first, (
        "the same bytes under a different name produced the same digest, so a "
        "module swap is invisible to it"
    )


def test_the_identity_records_the_interpreter() -> None:
    """An unexpected venv is part of "is this the released controller"."""
    assert identify_launcher().executable == sys.executable
