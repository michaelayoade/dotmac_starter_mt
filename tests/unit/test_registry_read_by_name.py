"""The by-name registry read: enumerated, exactly-once, credential-free URL.

`registry_read.RegistryReader` is the ONE implementation of "fetch this exact
set of filenames from a private simple index". It was extracted from the kernel
lane's `collect_private_registry_files.py` rather than copied, because a second
copy of a security-shaped fetch drifts silently in the worse direction.

The property that matters, and the reason the extraction happened at all: a
caller states the filenames it expects and every one of them is requested. A
resolver-mediated fetch answers a different question — `pip download` takes the
wheel and leaves the sdist, which is correct pip behaviour and no proof about
the sdist's bytes. That is the gap recorded against
`dotmac-deployment-control` 0.1.0a3.

Every refusal below is exercised, not described.
"""

from __future__ import annotations

import importlib.util
import sys
import urllib.error
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PROJECT_ROOT / "scripts"


def _load():
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "registry_read", SCRIPTS / "registry_read.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


READ = _load()

INDEX = "https://registry.dotmac.io/api/packages/dotmac/pypi/simple/some-dist/"
WHEEL = "some_dist-1.0.0-py3-none-any.whl"
SDIST = "some_dist-1.0.0.tar.gz"


class _Reader(READ.RegistryReader):
    """A reader whose transport is a dictionary, so the enumeration logic is
    exercised without a network."""

    def __init__(self, pages: dict[str, bytes]) -> None:
        super().__init__(INDEX, "ci-reader", "not-a-real-credential")
        self.pages = pages
        self.requested: list[str] = []

    def get(self, url: str) -> bytes:  # type: ignore[override]
        self.requested.append(url)
        if url not in self.pages:
            raise READ.RegistryReadRefused(f"no such url: {url}")
        return self.pages[url]


def _index_html(*names: str) -> bytes:
    links = "".join(f'<a href="../../files/{name}">{name}</a>' for name in names)
    return f"<html><body>{links}</body></html>".encode()


def _pages(*names: str, duplicate: str | None = None) -> dict[str, bytes]:
    listed = list(names) + ([duplicate] if duplicate else [])
    pages = {INDEX: _index_html(*listed)}
    for name in set(listed):
        pages[f"https://registry.dotmac.io/api/packages/files/{name}"] = (
            f"bytes of {name}".encode()
        )
    return pages


# ── the credential never travels in the URL ─────────────────────────────────


def test_an_index_url_carrying_a_credential_is_refused() -> None:
    with pytest.raises(READ.RegistryReadRefused, match="credential-free"):
        READ.RegistryReader(
            "https://ci-reader:secret@registry.dotmac.io/simple/x/", "ci-reader", "s"
        )


def test_a_plaintext_index_is_refused() -> None:
    with pytest.raises(READ.RegistryReadRefused, match="credential-free"):
        READ.RegistryReader("http://registry.dotmac.io/simple/x/", "ci-reader", "s")


def test_a_missing_read_credential_is_refused() -> None:
    with pytest.raises(READ.RegistryReadRefused, match="read login and credential"):
        READ.RegistryReader(INDEX, "ci-reader", "")


# ── the enumeration itself ──────────────────────────────────────────────────


def test_every_expected_filename_is_requested_individually(tmp_path: Path) -> None:
    reader = _Reader(_pages(WHEEL, SDIST))
    collected = reader.collect(frozenset({WHEEL, SDIST}), tmp_path / "fetched")
    assert set(collected) == {WHEEL, SDIST}
    assert collected[SDIST].read_bytes() == b"bytes of " + SDIST.encode()
    # The index once, then each artifact by its own name — never a resolution.
    assert reader.requested[0] == INDEX
    assert sorted(Path(url).name for url in reader.requested[1:]) == sorted(
        [SDIST, WHEEL]
    )


def test_an_expected_name_the_index_does_not_list_is_refused(tmp_path: Path) -> None:
    """The sdist-shaped failure: the wheel is there, the sdist is not, and the
    read refuses instead of quietly proving one file out of two."""
    reader = _Reader(_pages(WHEEL))
    with pytest.raises(READ.RegistryReadRefused, match="cardinality"):
        reader.collect(frozenset({WHEEL, SDIST}), tmp_path / "fetched")


def test_a_name_listed_twice_is_refused(tmp_path: Path) -> None:
    """One filename must identify one set of bytes."""
    reader = _Reader(_pages(WHEEL, SDIST, duplicate=SDIST))
    with pytest.raises(READ.RegistryReadRefused, match="cardinality"):
        reader.collect(frozenset({WHEEL, SDIST}), tmp_path / "fetched")


def test_an_empty_expectation_is_refused(tmp_path: Path) -> None:
    """A fetch of nothing succeeds trivially and proves nothing."""
    reader = _Reader(_pages(WHEEL, SDIST))
    with pytest.raises(READ.RegistryReadRefused, match="empty filename set"):
        reader.collect(frozenset(), tmp_path / "fetched")


# ── origin discipline ───────────────────────────────────────────────────────


def test_a_redirect_off_the_index_origin_is_refused() -> None:
    handler = READ.SameOriginRedirect("registry.dotmac.io")
    with pytest.raises(urllib.error.HTTPError, match="unsafe redirect"):
        handler.redirect_request(
            None, None, 302, "Found", {}, "https://elsewhere.example/evil.whl"
        )
