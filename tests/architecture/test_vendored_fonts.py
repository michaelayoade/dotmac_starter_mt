"""Vendored-font integrity guard (kernel WS / 0.1.0a2 font fix).

The kernel self-hosts its fonts (no CDN, strict CSP). A regression once shipped
every weight as a byte-for-byte copy of the 400 file, so bold/semibold text
silently rendered at weight 400. This guard fails the build if any two vendored
`woff2` files are byte-identical, if a weight referenced by `fonts.css` is
missing, or if a file isn't a real woff2.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import dotmac_kernel

FONTS_DIR = Path(dotmac_kernel.__file__).resolve().parent / "static" / "fonts"
WOFF2_MAGIC = b"wOF2"


def _woff2_files() -> list[Path]:
    return sorted(FONTS_DIR.glob("*.woff2"))


def test_fonts_dir_has_woff2_files() -> None:
    assert _woff2_files(), f"no vendored woff2 fonts under {FONTS_DIR}"


def test_every_woff2_is_a_real_woff2() -> None:
    for f in _woff2_files():
        assert f.read_bytes()[:4] == WOFF2_MAGIC, f"{f.name} is not a woff2 (bad magic)"


def test_no_two_weights_are_byte_identical() -> None:
    """The exact regression: distinct weight files must have distinct bytes."""
    by_digest: dict[str, str] = {}
    for f in _woff2_files():
        digest = hashlib.md5(f.read_bytes()).hexdigest()  # noqa: S324 - integrity, not security
        if digest in by_digest:
            raise AssertionError(
                f"{f.name} is byte-identical to {by_digest[digest]} — "
                "vendored font weights must be the real distinct weights"
            )
        by_digest[digest] = f.name


def test_every_fonts_css_weight_file_exists() -> None:
    """Every `url(/static/fonts/<file>.woff2)` in fonts.css must resolve to a
    file present on disk (no dead @font-face src)."""
    css = (FONTS_DIR / "fonts.css").read_text()
    referenced = set(re.findall(r"url\(/static/fonts/([^)]+\.woff2)\)", css))
    assert referenced, "fonts.css references no woff2 files"
    for name in referenced:
        assert (FONTS_DIR / name).is_file(), f"fonts.css references missing {name}"
