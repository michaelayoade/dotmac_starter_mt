"""The promoted `table_pagination` macro renders against the
`pagination_context` dict contract (see `dotmac_kernel.query`)."""

from __future__ import annotations

from types import SimpleNamespace

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
