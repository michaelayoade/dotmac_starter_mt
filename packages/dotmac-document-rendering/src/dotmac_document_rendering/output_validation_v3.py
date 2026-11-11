"""Data-only HTML output check for V3.

A V3 document may reference exactly one kind of resource: a ``data:`` image URI
built by the renderer from verified bytes. Any other URL-bearing attribute,
active element, event handler, stylesheet reference or CSS ``url(`` is a
violation, wherever it appears. Element text is not an attribute and is never a
reference, so a visible ``https://...`` string is fine; a followable one is not.
"""

from __future__ import annotations

import base64
import re
from html.parser import HTMLParser
from typing import ClassVar

from dotmac_document_rendering.contracts_v3 import (
    LOGO_MAX_BYTES,
    EmbeddedLogoAssetV3,
)
from dotmac_document_rendering.projection_v3 import verify_logo_content_v3

_IMG_DATA_URI = re.compile(r"data:image/(?:png|jpeg);base64,[A-Za-z0-9+/]+={0,2}\Z")
_MAX_IMG_SRC = len("data:image/jpeg;base64,") + 4 * ((LOGO_MAX_BYTES + 2) // 3)

# Every attribute that can name a resource, plus the generic "any scheme" test.
_URL_ATTRIBUTES = frozenset(
    {
        "src",
        "srcset",
        "href",
        "poster",
        "data",
        "action",
        "formaction",
        "background",
        "cite",
        "longdesc",
        "manifest",
        "ping",
        "usemap",
        "codebase",
        "icon",
        "profile",
        "lowsrc",
        "dynsrc",
        "xlink:href",
    }
)
_ALLOWED_TAGS = frozenset(
    {
        "html",
        "head",
        "meta",
        "style",
        "body",
        "section",
        "h2",
        "p",
        "dl",
        "dt",
        "dd",
        "table",
        "thead",
        "tbody",
        "tr",
        "th",
        "td",
        "img",
    }
)


def data_uri_for_logo_v3(asset: EmbeddedLogoAssetV3, content: bytes) -> str:
    """The only resource reference V3 emits; built from verified bytes."""
    verify_logo_content_v3(asset, content)
    return f"data:{asset.media_type};base64,{base64.b64encode(content).decode('ascii')}"


class _ResourceScan(HTMLParser):
    _static_attributes: ClassVar[dict[str, frozenset[str]]] = {
        "meta": frozenset({"charset"}),
        "section": frozenset({"data-section"}),
        "table": frozenset({"data-table"}),
        "tr": frozenset({"data-row"}),
        "img": frozenset({"alt", "src"}),
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.problems: list[str] = []
        self.images = 0
        self._in_style = False

    def _tag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in _ALLOWED_TAGS:
            self.problems.append(f"element <{tag}> is outside the data-only grammar")
        allowed = self._static_attributes.get(tag, frozenset())
        for name, value in attrs:
            lowered = name.lower()
            if lowered.startswith("on") or lowered == "style":
                self.problems.append(f"<{tag}> carries active attribute {lowered}")
            elif lowered in _URL_ATTRIBUTES:
                text = (value or "").strip()
                if tag == "img" and lowered == "src":
                    if (
                        len(text) > _MAX_IMG_SRC
                        or _IMG_DATA_URI.fullmatch(text) is None
                    ):
                        self.problems.append("img src is not a bounded data: image")
                else:
                    self.problems.append(
                        f"<{tag}> {lowered} is a URL reference outside data: images"
                    )
            elif lowered not in allowed:
                self.problems.append(f"<{tag}> attribute {lowered} is not permitted")
            elif value is None:
                self.problems.append(f"<{tag}> attribute {lowered} has no value")
        if tag == "img":
            self.images += 1
            names = [name.lower() for name, _ in attrs]
            if sorted(names) != ["alt", "src"]:
                self.problems.append("img must carry exactly alt and src")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._tag(tag, attrs)
        self._in_style = tag == "style"

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._tag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag == "style":
            self._in_style = False

    def handle_data(self, data: str) -> None:
        if self._in_style:
            compact = re.sub(r"\s+", "", data).lower()
            for needle in ("url(", "@import", "image-set(", "expression("):
                if needle in compact:
                    self.problems.append("stylesheet references a resource")

    def handle_comment(self, data: str) -> None:
        self.problems.append("HTML comments are not permitted")

    def handle_pi(self, data: str) -> None:
        self.problems.append("processing instructions are not permitted")

    def unknown_decl(self, data: str) -> None:
        self.problems.append("unknown declarations are not permitted")


def html_data_only_violations_v3(html: str, *, max_images: int = 1) -> tuple[str, ...]:
    """Empty means: the HTML references no resource except a data: image."""
    scan = _ResourceScan()
    scan.feed(html)
    scan.close()
    problems = list(scan.problems)
    if scan.images > max_images:
        problems.append(f"more than {max_images} image(s)")
    return tuple(dict.fromkeys(problems))


__all__ = ["data_uri_for_logo_v3", "html_data_only_violations_v3"]
