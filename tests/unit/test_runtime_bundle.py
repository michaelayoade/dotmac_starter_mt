"""Production closure must follow dependency metadata, including extras."""

from __future__ import annotations

from scripts.runtime_bundle import dependency_closure


def test_runtime_closure_includes_extras_and_excludes_uncomposed_packages():
    graph = {
        "kernel": ["db[driver]>=1", "never; python_version < '2'"],
        "db": ["driver; extra == 'driver'", "kernel"],
        "driver": [],
        "uncomposed": ["unrelated"],
    }
    assert dependency_closure(["kernel"], graph.__getitem__) == {
        "kernel",
        "db",
        "driver",
    }


def test_extra_discovered_after_a_dependency_was_visited_is_not_lost():
    graph = {
        "a": ["common", "b"],
        "b": ["common[shared]"],
        "common": ["extra-dependency; extra == 'shared'"],
        "extra-dependency": [],
    }
    assert dependency_closure(["a"], graph.__getitem__) == {
        "a",
        "b",
        "common",
        "extra-dependency",
    }
