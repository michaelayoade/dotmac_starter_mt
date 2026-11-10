"""The promoted `table_pagination` macro renders against the
`pagination_context` dict contract (see `dotmac_kernel.query`)."""

from __future__ import annotations

from html.parser import HTMLParser
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from dotmac_kernel.query import pagination_context
from dotmac_kernel.templating import templates
from starlette.datastructures import URL


def test_table_pagination_macro_renders_navigation():
    request = SimpleNamespace(url=URL("http://testserver/items?status=active"))
    template = templates.env.from_string(
        '{% from "components/table_macros.html" import table_pagination %}'
        '{{ table_pagination(request, pagination, "items", (10, 25),'
        ' (("status", "active"),)) }}'
    )
    html = template.render(
        request=request,
        pagination=pagination_context(total=95, limit=10, offset=40),
    )
    assert "Showing" in html
    assert "<strong>41</strong>" in html
    assert "offset=50" in html
    assert 'name="status" value="active"' in html
    assert 'aria-current="page"' in html


class _PaginationLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and "aria-label" in attrs:
            self.links[attrs["aria-label"]] = parse_qs(urlsplit(attrs["href"]).query)


@pytest.mark.parametrize(
    "total, offset, previous, pages",
    [
        (95, 95, 90, {"Page 1": 0, "Page 9": 80, "Page 10": 90}),
        (95, 100, 90, {"Page 1": 0, "Page 9": 80, "Page 10": 90}),
        (95, 1000, 90, {"Page 1": 0, "Page 9": 80, "Page 10": 90}),
        (0, 10, 0, {"Page 1": 0}),
        (0, 1000, 0, {"Page 1": 0}),
    ],
)
def test_table_pagination_stale_offset_has_recovery_links(
    total, offset, previous, pages
):
    request = SimpleNamespace(
        url=URL(f"http://testserver/items?status=active&offset={offset}&limit=10")
    )
    template = templates.env.from_string(
        '{% from "components/table_macros.html" import table_pagination %}'
        "{{ table_pagination(request, pagination) }}"
    )
    html = template.render(
        request=request,
        pagination=pagination_context(total=total, limit=10, offset=offset),
    )
    assert (
        f"Showing <strong>0</strong> to <strong>0</strong> of <strong>{total}</strong>"
        in html
    )
    assert 'aria-current="page"' not in html
    links = _PaginationLinks()
    links.feed(html)
    assert "Next page" not in links.links
    assert set(links.links) == {"Previous page", *pages}
    for label, expected_offset in {"Previous page": previous, **pages}.items():
        assert links.links[label] == {
            "status": ["active"],
            "offset": [str(expected_offset)],
            "limit": ["10"],
        }
