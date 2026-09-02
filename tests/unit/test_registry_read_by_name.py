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
import urllib.parse
import urllib.request
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

#: Where the reader must end up asking for a file, written out rather than
#: derived, because the URL SHAPE is the contract this module exists to hold. A
#: by-name read that requests the wrong URL fails identically against the live
#: index, so deriving this with the same `urljoin` the reader uses would assert
#: nothing. Forgejo's simple index serves `.../simple/<project>/` and links its
#: files at `.../files/<name>`, two segments up — the relative form below.
FILES = "https://registry.dotmac.io/api/packages/dotmac/pypi/files/"
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
        # `urllib.request.Request` splits the fragment off before the request
        # line — `selector` carries no `#sha256=...` — so the stub resolves the
        # way the wire does rather than the way the string looks.
        addressed = urllib.parse.urldefrag(url).url
        if addressed not in self.pages:
            raise READ.RegistryReadRefused(f"no such url: {addressed}")
        return self.pages[addressed]


def _file_requests(reader: _Reader) -> list[str]:
    """Every URL the reader asked for that was NOT the index, fragment removed.

    Filtered rather than sliced: `collect` reads the index through
    `listed_filenames`, so a test that also calls `listed_filenames` itself
    reads it twice, and a positional slice would silently compare an index URL
    against a file URL.
    """
    return sorted(
        urllib.parse.urldefrag(url).url
        for url in reader.requested
        if urllib.parse.urldefrag(url).url != INDEX
    )


def _expected_file_urls() -> list[str]:
    return sorted([f"{FILES}{WHEEL}", f"{FILES}{SDIST}"])


def _href(name: str, style: str = "relative") -> str:
    """One filename, in each of the three forms a real simple index emits.

    All three must resolve to the SAME file URL. PEP 503 appends a
    `#sha256=...` fragment to the href, and the fragment must reach neither the
    derived filename nor the request line.
    """
    if style == "relative":
        return f"../../files/{name}#sha256={'a' * 64}"
    if style == "absolute-path":
        return f"/api/packages/dotmac/pypi/files/{name}#sha256={'a' * 64}"
    if style == "absolute-url":
        return f"{FILES}{name}#sha256={'a' * 64}"
    raise AssertionError(style)


def _index_html(*names: str, style: str = "relative") -> bytes:
    links = "".join(f'<a href="{_href(name, style)}">{name}</a>' for name in names)
    return f"<html><body>{links}</body></html>".encode()


def _pages(
    *names: str, duplicate: str | None = None, style: str = "relative"
) -> dict[str, bytes]:
    listed = list(names) + ([duplicate] if duplicate else [])
    pages = {INDEX: _index_html(*listed, style=style)}
    for name in set(listed):
        pages[f"{FILES}{name}"] = f"bytes of {name}".encode()
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

    # The index ONCE, then each artifact at its own URL — never a resolution.
    # Compared as whole URLs rather than basenames: a reader that requested the
    # right filename under the wrong path would satisfy a basename check and
    # fail identically against the live index, which is the defect this whole
    # module exists to close.
    assert reader.requested[0] == INDEX
    assert _file_requests(reader) == _expected_file_urls()


@pytest.mark.parametrize("style", ["relative", "absolute-path", "absolute-url"])
def test_all_three_href_forms_resolve_to_the_same_file_url(
    style: str, tmp_path: Path
) -> None:
    """A simple index may link a file relatively, by absolute path, or by full
    URL. The reader resolves the href against the index rather than assuming a
    form, so all three land on the same bytes."""
    reader = _Reader(_pages(WHEEL, SDIST, style=style))
    collected = reader.collect(frozenset({WHEEL, SDIST}), tmp_path / "fetched")
    assert set(collected) == {WHEEL, SDIST}
    assert _file_requests(reader) == _expected_file_urls()


def test_the_pep503_digest_fragment_reaches_neither_the_name_nor_the_wire(
    tmp_path: Path,
) -> None:
    """`#sha256=...` is metadata about the link, not part of the filename and
    not part of the request. A reader that folded it into either would look for
    a file the index does not have."""
    reader = _Reader(_pages(WHEEL, SDIST))

    listed = reader.listed_filenames()
    assert set(listed) == {WHEEL, SDIST}, "the fragment leaked into the filename"

    reader.collect(frozenset({WHEEL, SDIST}), tmp_path / "fetched")
    for url in (u for u in reader.requested if urllib.parse.urldefrag(u).url != INDEX):
        # What actually goes on the wire. `urllib.request.Request` splits the
        # fragment off into `.fragment`, so `.selector` is the request line.
        # The suppression below is correct: the Request is CONSTRUCTED to
        # read its parsed `selector` and is never opened. Nothing in this
        # file reaches a network.
        selector = urllib.request.Request(url).selector  # noqa: S310
        assert "#" not in selector, selector
        assert selector.rsplit("/", 1)[-1] in {WHEEL, SDIST}, selector


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
