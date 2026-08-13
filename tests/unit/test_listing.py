"""Parity tests for the list surface, ported with the implementation.

Source: ``dotmac_sub:tests/test_list_query_contract.py``. ADR-0006 rule 22 says
a product-first extraction ports the qualifying implementation **and its parity
tests** — the tests are what make the port a port rather than a rewrite that
happens to resemble the donor.

Six of that file's tests are generic contract tests and are carried over here
with their assertions unchanged. The remainder exercise Sub's own customer and
subscriber list definitions (`app.services.web_customer_lists`,
`app.services.web_subscriber_lists`) and stay in Sub: those are product
vocabulary, not mechanism, and importing them here would drag Sub's domain into
the kernel's test suite.

Added beyond the donor: coverage for `request_needs_canonicalization` (used by
Sub in production but only exercised there through route tests) and for the SQL
helpers that moved in from `dotmac_kernel.query`, whose own tests live in
`tests/unit/test_query.py`.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
from dotmac_kernel.listing import (
    ListDefinition,
    ListFieldDefinition,
    ListQuery,
    PageMeta,
    request_needs_canonicalization,
)


def _definition() -> ListDefinition:
    return ListDefinition(
        key="example",
        fields=(
            ListFieldDefinition("name", "Name", searchable=True, sortable=True),
            ListFieldDefinition("status", "Status", filterable=True),
            ListFieldDefinition("created_at", "Created", sortable=True),
        ),
        default_sort="created_at",
    )


# ---------------------------------------------------------------------------
# Ported verbatim from the donor's generic contract tests
# ---------------------------------------------------------------------------


def test_list_definition_owns_normalization_capabilities_and_url_round_trip() -> None:
    definition = _definition()

    query = definition.build_query(
        search="  Acme & Sons  ",
        filters={"status": " active "},
        sort_by="name",
        sort_dir="asc",
        page=3,
        per_page=50,
    )

    assert definition.searchable_keys == ("name",)
    assert definition.filterable_keys == ("status",)
    assert definition.sortable_keys == ("name", "created_at")
    assert query.search == "Acme & Sons"
    assert query.filter_value("status") == "active"
    assert query.offset == 100

    url = query.url("/admin/example")
    params = parse_qs(urlsplit(url).query)
    assert params == {
        "search": ["Acme & Sons"],
        "status": ["active"],
        "sort": ["name"],
        "dir": ["asc"],
        "page": ["3"],
        "per_page": ["50"],
    }


def test_sort_change_resets_page_and_preserves_search_filters_and_page_size() -> None:
    query = _definition().build_query(
        search="needle",
        filters={"status": "active"},
        page=4,
        per_page=50,
    )

    params = parse_qs(
        urlsplit(query.url("/admin/example", sort_by="name", sort_dir="asc")).query
    )

    assert params["page"] == ["1"]
    assert params["per_page"] == ["50"]
    assert params["search"] == ["needle"]
    assert params["status"] == ["active"]


def test_filter_and_page_size_changes_reset_page_and_keep_canonical_state() -> None:
    query = _definition().build_query(
        search="needle",
        filters={"status": "active"},
        page=4,
        per_page=25,
    )

    filtered = parse_qs(
        urlsplit(
            query.url(
                "/admin/example",
                filters={"status": None},
                per_page=50,
            )
        ).query
    )

    assert "status" not in filtered
    assert filtered["search"] == ["needle"]
    assert filtered["page"] == ["1"]
    assert filtered["per_page"] == ["50"]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"filters": {"unknown": "x"}}, "Unsupported filters"),
        ({"sort_by": "unknown"}, "Unsupported sort field"),
        ({"sort_dir": "sideways"}, "sort_dir must be asc or desc"),
        ({"per_page": 20}, "per_page must be one of"),
    ],
)
def test_list_definition_rejects_undeclared_query_state(
    overrides: dict[str, object], message: str
) -> None:
    params: dict[str, object] = {
        "search": None,
        "filters": {},
        "sort_by": None,
        "sort_dir": None,
        "page": 1,
        "per_page": 25,
        **overrides,
    }

    with pytest.raises(ValueError, match=message):
        _definition().build_query(**params)  # type: ignore[arg-type]


def test_page_meta_clamps_out_of_range_page_and_builds_compact_navigation() -> None:
    query = _definition().build_query(
        search=None,
        filters={},
        page=99,
        per_page=10,
    )

    meta = PageMeta.from_query(query, total_items=101)

    assert meta.page == 11
    assert meta.start_item == 101
    assert meta.end_item == 101
    assert meta.has_previous is True
    assert meta.has_next is False
    assert meta.navigation == (1, None, 10, 11)


def test_empty_page_meta_uses_zero_item_range() -> None:
    query = _definition().build_query(
        search=None,
        filters={},
        page=1,
        per_page=25,
    )

    meta = PageMeta.from_query(query, total_items=0)

    assert meta.page == 1
    assert meta.total_pages == 1
    assert meta.start_item == 0
    assert meta.end_item == 0
    assert meta.navigation == (1,)


# ---------------------------------------------------------------------------
# Added here: the donor exercises these only through Sub's route tests
# ---------------------------------------------------------------------------


def test_definition_rejects_a_declaration_it_could_never_satisfy() -> None:
    """Validation is at DECLARATION time, so a bad list fails at import."""
    with pytest.raises(ValueError, match="Default sort is not sortable"):
        ListDefinition(
            key="example",
            fields=(ListFieldDefinition("name", "Name"),),
            default_sort="name",
        )
    with pytest.raises(ValueError, match="Duplicate fields"):
        ListDefinition(
            key="example",
            fields=(
                ListFieldDefinition("name", "Name", sortable=True),
                ListFieldDefinition("name", "Name again"),
            ),
            default_sort="name",
        )
    with pytest.raises(ValueError, match="Default page size is not allowed"):
        ListDefinition(
            key="example",
            fields=(ListFieldDefinition("name", "Name", sortable=True),),
            default_sort="name",
            default_per_page=7,
        )
    with pytest.raises(ValueError, match="key is required"):
        ListDefinition(
            key="   ",
            fields=(ListFieldDefinition("name", "Name", sortable=True),),
            default_sort="name",
        )


def test_canonicalization_is_needed_when_the_raw_request_differs() -> None:
    """The redirect signal: rows for one page, a different page in the URL."""
    query = _definition().build_query(
        search="needle", filters={"status": "active"}, page=1, per_page=25
    )

    assert request_needs_canonicalization(query, page=1) is False
    assert (
        request_needs_canonicalization(
            query,
            search="needle",
            filters={"status": "active"},
            sort_by=query.sort_by,
            sort_dir=query.sort_dir,
            page=1,
            per_page=25,
        )
        is False
    )

    assert request_needs_canonicalization(query, page=99) is True
    assert request_needs_canonicalization(query, page=1, per_page=50) is True
    assert request_needs_canonicalization(query, page=1, sort_by="name") is True
    assert request_needs_canonicalization(query, page=1, sort_dir="asc") is True
    assert request_needs_canonicalization(query, page=1, search="other") is True
    assert (
        request_needs_canonicalization(query, page=1, filters={"status": "closed"})
        is True
    )


def test_a_clamped_page_is_exactly_what_canonicalization_detects() -> None:
    """The two halves compose: PageMeta clamps, then the route can redirect."""
    query = _definition().build_query(search=None, filters={}, page=99, per_page=10)
    meta = PageMeta.from_query(query, total_items=101)

    canonical = query.with_page(meta.page)
    assert request_needs_canonicalization(canonical, page=query.page) is True
    assert request_needs_canonicalization(canonical, page=meta.page) is False


def test_query_transitions_are_immutable_and_reset_the_page() -> None:
    query = _definition().build_query(
        search="needle", filters={"status": "active"}, page=4, per_page=25
    )

    assert query.with_page(2) is not query
    assert query.page == 4, "the original must not be mutated"

    assert query.with_sort("name", "asc").page == 1
    assert query.with_filters({"status": "closed"}).page == 1
    assert query.with_per_page(50).page == 1

    with pytest.raises(ValueError, match="page must be at least 1"):
        query.with_page(0)
    with pytest.raises(ValueError, match="Unsupported sort field"):
        query.with_sort("unknown", "asc")
    with pytest.raises(ValueError, match="Unsupported filters"):
        query.with_filters({"unknown": "x"})
    with pytest.raises(ValueError, match="per_page must be one of"):
        query.with_per_page(7)


def test_filters_serialize_in_declaration_order_so_urls_are_stable() -> None:
    """A cache key or a deep link must not depend on dict iteration order."""
    definition = ListDefinition(
        key="example",
        fields=(
            ListFieldDefinition("created_at", "Created", sortable=True),
            ListFieldDefinition("alpha", "Alpha", filterable=True),
            ListFieldDefinition("beta", "Beta", filterable=True),
        ),
        default_sort="created_at",
    )

    first = definition.build_query(
        search=None, filters={"beta": "2", "alpha": "1"}, page=1
    )
    second = definition.build_query(
        search=None, filters={"alpha": "1", "beta": "2"}, page=1
    )

    assert first.filters == second.filters == (("alpha", "1"), ("beta", "2"))
    assert first.url("/x") == second.url("/x")


def test_blank_filter_and_search_values_are_dropped_not_carried_as_empty() -> None:
    query = _definition().build_query(
        search="   ", filters={"status": "   "}, page=1, per_page=25
    )

    assert query.search is None
    assert query.filters == ()
    assert "status" not in parse_qs(urlsplit(query.url("/x")).query)


def test_page_meta_treats_a_negative_total_as_empty() -> None:
    """`total_items` comes from a COUNT; defend the arithmetic anyway."""
    query = _definition().build_query(search=None, filters={}, page=1, per_page=25)
    meta = PageMeta.from_query(query, total_items=-5)

    assert meta.total_items == 0
    assert meta.total_pages == 1
    assert (meta.start_item, meta.end_item) == (0, 0)


def test_the_query_is_frozen() -> None:
    query = _definition().build_query(search=None, filters={}, page=1, per_page=25)
    with pytest.raises(AttributeError):
        query.page = 2  # type: ignore[misc]
    assert isinstance(query, ListQuery)
