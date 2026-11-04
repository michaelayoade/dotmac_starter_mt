"""Production closure must follow dependency metadata, including extras."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts import runtime_bundle
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


@pytest.mark.parametrize("outside", [False, True])
def test_bundle_preserves_installed_headers_and_refuses_outside_files(
    tmp_path, monkeypatch, outside
):
    root = tmp_path / "project"
    (root / "deploy").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[tool.poetry.dependencies]\npython = ">=3.12"\ngreenlet = "3.1.1"\n'
    )
    (root / "deploy/runtime-packages.json").write_text('{"distributions": []}')
    prefix = tmp_path / "installed"
    paths = {
        "purelib": prefix / "lib/site-packages",
        "scripts": prefix / "bin",
        "include": prefix / "include/python3.12",
    }
    header = (
        tmp_path / "outside/greenlet.h"
        if outside
        else paths["include"] / "greenlet/greenlet.h"
    )
    header.parent.mkdir(parents=True)
    header.write_text("/* installed wheel header */")
    distribution = SimpleNamespace(
        requires=[], files=[header], locate_file=lambda entry: entry
    )
    monkeypatch.setattr(runtime_bundle.metadata, "distribution", lambda _: distribution)
    monkeypatch.setattr(
        runtime_bundle.sysconfig, "get_path", lambda key: str(paths[key])
    )
    output = tmp_path / "runtime"
    if outside:
        with pytest.raises(ValueError, match="outside runtime roots"):
            runtime_bundle.bundle(root, output)
    else:
        runtime_bundle.bundle(root, output)
        assert (
            output / "include/greenlet/greenlet.h"
        ).read_text() == header.read_text()
