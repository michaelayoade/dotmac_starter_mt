"""Product-first contract tests for server-owned list request state."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
from dotmac_kernel.listing import (
    ListDefinition,
    ListFieldDefinition,
    PageMeta,
    request_needs_canonicalization,
)


def _definition() -> ListDefinition:
    return ListDefinition(
        key="customers",
        fields=(
            ListFieldDefinition("name", "Name", searchable=True, sortable=True),
            ListFieldDefinition("status", "Status", filterable=True),
            ListFieldDefinition("created_at", "Created", sortable=True),
        ),
        default_sort="created_at",
    )


def test_definition_owns_capabilities_normalization_and_url_round_trip() -> None:
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
    assert parse_qs(urlsplit(query.url("/customers")).query) == {
        "search": ["Acme & Sons"],
        "status": ["active"],
        "sort": ["name"],
        "dir": ["asc"],
        "page": ["3"],
        "per_page": ["50"],
    }


def test_sort_filter_and_page_size_changes_reset_the_page() -> None:
    query = _definition().build_query(
        search="needle",
        filters={"status": "active"},
        page=4,
        per_page=25,
    )

    sorted_params = parse_qs(
        urlsplit(query.url("/customers", sort_by="name", sort_dir="asc")).query
    )
    filtered_params = parse_qs(
        urlsplit(
            query.url(
                "/customers",
                filters={"status": None},
                per_page=50,
            )
        ).query
    )

    assert sorted_params["page"] == ["1"]
    assert sorted_params["search"] == ["needle"]
    assert sorted_params["status"] == ["active"]
    assert filtered_params["page"] == ["1"]
    assert filtered_params["per_page"] == ["50"]
    assert filtered_params["search"] == ["needle"]
    assert "status" not in filtered_params


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"filters": {"unknown": "x"}}, "Unsupported filters"),
        ({"sort_by": "unknown"}, "Unsupported sort field"),
        ({"sort_dir": "sideways"}, "sort_dir must be asc or desc"),
        ({"per_page": 20}, "per_page must be one of"),
    ],
)
def test_definition_rejects_undeclared_request_state(
    overrides: dict[str, object], message: str
) -> None:
    parameters: dict[str, object] = {
        "search": None,
        "filters": {},
        "sort_by": None,
        "sort_dir": None,
        "page": 1,
        "per_page": 25,
        **overrides,
    }

    with pytest.raises(ValueError, match=message):
        _definition().build_query(**parameters)  # type: ignore[arg-type]


def test_page_meta_clamps_range_and_builds_compact_navigation() -> None:
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


def test_empty_page_meta_uses_a_zero_item_range() -> None:
    query = _definition().build_query(search=None, filters={})

    meta = PageMeta.from_query(query, total_items=0)

    assert meta.page == 1
    assert meta.total_pages == 1
    assert meta.start_item == 0
    assert meta.end_item == 0
    assert meta.navigation == (1,)


def test_canonicalization_detects_raw_state_that_differs_from_projection() -> None:
    query = _definition().build_query(
        search="Acme",
        filters={"status": "active"},
        page=3,
        per_page=50,
    )
    clamped = query.with_page(2)

    assert request_needs_canonicalization(
        clamped,
        search=" Acme ",
        filters={"status": "active"},
        page=3,
        per_page=50,
    )
    assert not request_needs_canonicalization(
        query,
        search=" Acme ",
        filters={"status": " active "},
        page=3,
        per_page=50,
    )
