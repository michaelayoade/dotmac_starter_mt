"""Every registered setting must have a behavior consumer ("no dead controls").

Adapted from `dotmac_sub:tests/architecture/test_no_orphan_settings.py`,
simplified for this app's much smaller spec registry: instead of a `SETTINGS_SPECS`
module-level list, specs register themselves into `app.core.settings_resolver`
(`register_specs`, called by `app/features/settings/spec.py` at import time) —
see that module's docstring for why the registry mechanism lives in core while
the declarations live in the feature.

A registered setting a tenant admin can edit via `PUT /settings/{domain}/{key}`
but that no runtime code ever reads does nothing — the control is dead. This
test fails the build when a spec's `key` has no reference anywhere under
`app/` outside the settings feature package and the resolver module itself. A
"reference" is the key appearing as a quoted string literal — e.g. a
`resolve_value(..., "key")` call — which is a necessary (not sufficient, but
reliable) condition for the setting to affect behavior.

`_ALLOWED_ORPHAN_SETTINGS` starts EMPTY per this task's contract and may only
ever shrink: a newly-registered key with no reader must fail the build
immediately rather than accumulate here. The entry below is a known,
scoped exception carried forward explicitly by name in the task briefs that
introduced it — not a general escape hatch.
"""

from __future__ import annotations

import pathlib

from app.core.settings_resolver import all_specs

# Import for the side effect: registers custom_fields/max_per_entity,
# branding/ui_branding, audit/retention_days into the registry this test reads.
from app.features.settings import spec as _settings_spec  # noqa: F401

# The settings feature package declares/seeds/serves every key — it is not a
# "reader" for orphan-detection purposes (excluding it is what makes the test
# meaningful at all: every key trivially appears in its own spec.py).
_EXCLUDED_DIR_PREFIX = "app/features/settings/"
# The resolver module's own docstrings/tests-support code reference keys in
# prose/comments while implementing the mechanism, not consuming a value —
# excluded for the same reason.
_EXCLUDED_FILE = "app/core/settings_resolver.py"

# Burn-down allowlist — do NOT add to this without a task/plan reference in
# the comment; a new orphan should get a real consumer instead. EMPTY as of
# plan 2b Task 2: `ui_branding` is now consumed by
# `app.core.branding.load_branding` (`resolve_value(db, SettingDomain.branding,
# "ui_branding", ...)`).
_ALLOWED_ORPHAN_SETTINGS: set[str] = set()


def _repo_root() -> pathlib.Path:
    # tests/architecture/<this file> -> repo root
    return pathlib.Path(__file__).resolve().parents[2]


def _is_excluded(rel_path: str) -> bool:
    return rel_path == _EXCLUDED_FILE or rel_path.startswith(_EXCLUDED_DIR_PREFIX)


def _reader_corpus(root: pathlib.Path) -> str:
    chunks: list[str] = []
    for path in (root / "app").rglob("*.py"):
        rel = str(path.relative_to(root))
        if _is_excluded(rel):
            continue
        try:
            chunks.append(path.read_text(encoding="utf-8"))
        except OSError:
            continue
    return "\n".join(chunks)


def _find_orphans() -> set[str]:
    corpus = _reader_corpus(_repo_root())
    keys = {spec.key for spec in all_specs()}
    return {
        key for key in keys if f'"{key}"' not in corpus and f"'{key}'" not in corpus
    }


def test_no_new_orphan_settings() -> None:
    orphans = _find_orphans()
    new_orphans = orphans - _ALLOWED_ORPHAN_SETTINGS
    assert not new_orphans, (
        "Registered setting(s) with no reader (dead control): "
        f"{sorted(new_orphans)}. Either read the value somewhere (as a "
        "quoted literal) outside app/features/settings/ and "
        "app/core/settings_resolver.py so it changes real behavior, or drop "
        "it from the spec registry. Do not add to _ALLOWED_ORPHAN_SETTINGS."
    )


def test_allowed_orphan_list_is_accurate() -> None:
    # Shrink-only companion: once an allowlisted key is wired (or removed
    # from the registry), it must be deleted from the allowlist so the list
    # trends toward empty instead of accumulating stale exceptions.
    orphans = _find_orphans()
    stale = _ALLOWED_ORPHAN_SETTINGS - orphans
    assert not stale, (
        "These keys are no longer orphaned (a consumer was wired) — delete "
        f"them from _ALLOWED_ORPHAN_SETTINGS: {sorted(stale)}"
    )
