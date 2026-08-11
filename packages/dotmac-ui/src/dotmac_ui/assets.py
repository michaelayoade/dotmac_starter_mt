"""The compiled-asset boundary — how a consumer gets the CSS without a build.

This is ADR-0006 **D3** in code. The package's consumer contract is a *compiled,
versioned, self-hosted stylesheet*, not a source file to be preprocessed. A
consumer:

1. resolves the packaged directory (`static_dir()`) or the single file
   (`stylesheet_path()`) by PACKAGE PATH, never by CWD — an installed wheel
   lives outside every assembly's working directory;
2. serves it from its own static mount, or copies it into an existing asset
   pipeline's output;
3. adds one `<link rel="stylesheet">` at the URL `stylesheet_url()` builds.

There is no step involving Tailwind, PostCSS, npm, or a bundler, and no
requirement to match this package's own toolchain or major version. `dotmac_erp`
is on Tailwind v3.4 with a JS config; `dotmac_sub` and `dotmac_starter_mt` are
on v4 CSS-first. All three consume the same artifact, because the artifact is
plain CSS custom properties plus one base rule — no `@tailwind`, no `@apply`, no
`@theme`, no layer that needs compiling.

**Cache busting is the package's job.** `stylesheet_url()` appends
`?v=<digest>` derived from the file's own bytes, so a consumer does not have to
wire the artifact into its hashing pipeline and does not depend on the kernel's
`static_asset_url` (which resolves against the *kernel's* package directory and
would report a UI asset as missing).

**Self-hosted and CSP-clean.** The stylesheet references no remote origin: no
CDN, no `@import`, no web font, no remote image. The no-CDN standard and
ADR-0006 D7's deny-by-default CSP both depend on that, so
`test_stylesheet_references_no_external_origin` asserts it rather than trusting
review.
"""

from __future__ import annotations

import json
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from typing import Final

from dotmac_ui.contract import UI_CONTRACT_VERSION

_PKG_DIR: Final[Path] = Path(__file__).resolve().parent
_STATIC_DIR: Final[Path] = _PKG_DIR / "static"

#: Directory containing everything the package publishes, relative to which
#: every other path below is expressed. Namespaced under `dotmac-ui/` so a
#: consumer can layer it into an existing `/static` mount with no chance of
#: shadowing one of its own files.
ASSET_NAMESPACE: Final[str] = "dotmac-ui"

#: The compiled stylesheet, relative to `static_dir()`. The UI CONTRACT version
#: is in the filename (not the package version): a consumer pinned to contract 1
#: keeps resolving contract 1's asset even once contract 2 ships beside it, and
#: a patch release that only changes token VALUES does not change the URL.
STYLESHEET_RELPATH: Final[str] = (
    f"{ASSET_NAMESPACE}/dotmac-ui-{UI_CONTRACT_VERSION}.css"
)

#: The machine-readable asset manifest, relative to `static_dir()`. Published
#: for consumers that integrate outside Python (a JS build, an nginx asset
#: pipeline, an air-gapped bundle check).
MANIFEST_RELPATH: Final[str] = f"{ASSET_NAMESPACE}/manifest.json"

#: The Tailwind preset, generated from the same tokens as the stylesheet so the
#: two cannot disagree. Unhashed on purpose: it is consumed at BUILD time by a
#: consumer's `tailwind.config.js`, never served to a browser, so a
#: cache-busting digest would only make the import path churn.
TAILWIND_PRESET_RELPATH: Final[str] = f"{ASSET_NAMESPACE}/tailwind-preset.js"

#: Length of the truncated sha256 used as the cache-busting token.
DIGEST_LENGTH: Final[int] = 12


def static_dir() -> Path:
    """The packaged static directory, resolved by package path.

    An assembly layers this into its own static mount — see the reference
    assembly's `ProductAssemblySpec.packaged_static_dirs`.
    """
    return _STATIC_DIR


def stylesheet_path() -> Path:
    """Absolute path to the compiled stylesheet."""
    return _STATIC_DIR / STYLESHEET_RELPATH


def tailwind_preset_path() -> Path:
    """Filesystem path to the generated Tailwind preset.

    A consumer's `tailwind.config.js` requires this path, so it is resolved from
    the installed package rather than copied into each repository — copying is
    what let two `_tokens.css` files drift 48% apart.
    """
    return static_dir() / TAILWIND_PRESET_RELPATH


def manifest_path() -> Path:
    """Absolute path to the published asset manifest."""
    return _STATIC_DIR / MANIFEST_RELPATH


@lru_cache(maxsize=8)
def asset_digest(relpath: str = STYLESHEET_RELPATH) -> str:
    """Truncated sha256 of a published asset's bytes.

    Raises `FileNotFoundError` rather than degrading to a placeholder: unlike a
    consumer's own build output, these assets ship inside the wheel, so a
    missing one means a packaging defect, and quietly serving a cache-busting
    token of "missing" would hide it until a browser cached the wrong file.
    """
    return sha256((_STATIC_DIR / relpath).read_bytes()).hexdigest()[:DIGEST_LENGTH]


def stylesheet_url(mount: str = "/static") -> str:
    """The `<link href>` for the compiled stylesheet, with a cache-busting
    `?v=`.

    `mount` is wherever the consumer mounted `static_dir()`; it defaults to the
    conventional `/static`.
    """
    prefix = "/" + mount.strip("/") if mount.strip("/") else ""
    return f"{prefix}/{STYLESHEET_RELPATH}?v={asset_digest()}"


def asset_manifest() -> dict[str, object]:
    """The published manifest as a dict (contract version, package version, and
    every asset's path + digest + byte size)."""
    return json.loads(manifest_path().read_text(encoding="utf-8"))


__all__ = [
    "ASSET_NAMESPACE",
    "DIGEST_LENGTH",
    "MANIFEST_RELPATH",
    "STYLESHEET_RELPATH",
    "TAILWIND_PRESET_RELPATH",
    "asset_digest",
    "asset_manifest",
    "manifest_path",
    "static_dir",
    "stylesheet_path",
    "stylesheet_url",
    "tailwind_preset_path",
]
