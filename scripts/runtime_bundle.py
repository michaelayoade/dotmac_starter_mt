"""Build a fresh production bundle from installed, locked distributions.

The assembly declares local distributions; non-path runtime requirements come
from the root pyproject. Follow installed Requires-Dist and extras rather than
copying the development environment or importing candidate module code.
"""

from __future__ import annotations

import argparse
import importlib.metadata as metadata
import json
import shutil
import sysconfig
import tomllib
from collections import deque
from collections.abc import Callable, Iterable
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def dependency_closure(
    roots: Iterable[str], requires: Callable[[str], Iterable[str]]
) -> set[str]:
    pending = deque(Requirement(root) for root in roots)
    visited: dict[str, set[str]] = {}
    while pending:
        requirement = pending.popleft()
        name = canonicalize_name(requirement.name)
        extras = set(requirement.extras) | {""}
        if name in visited and extras <= visited[name]:
            continue
        visited.setdefault(name, set()).update(extras)
        for raw in requires(name):
            dependency = Requirement(raw)
            if dependency.marker is None or any(
                dependency.marker.evaluate({"extra": extra}) for extra in visited[name]
            ):
                pending.append(dependency)
    return set(visited)


def bundle(root: Path, output: Path) -> None:
    if output.exists():
        raise ValueError("runtime bundle destination must not already exist")
    project = tomllib.loads((root / "pyproject.toml").read_text())["tool"]["poetry"]
    dependencies = project["dependencies"]
    profile_path = root / "deploy/runtime-packages.json"
    selected = json.loads(profile_path.read_text())["distributions"]
    selected_names = [canonicalize_name(Requirement(item).name) for item in selected]
    local = {
        canonicalize_name(name): (root / value["path"]).resolve()
        for name, value in dependencies.items()
        if isinstance(value, dict) and "path" in value
    }
    if len(selected_names) != len(set(selected_names)) or not set(
        selected_names
    ) <= set(local):
        raise ValueError(
            "runtime profile must uniquely name declared path distributions"
        )
    roots = list(selected)
    for name, declaration in dependencies.items():
        if name == "python" or canonicalize_name(name) in local:
            continue
        extras = declaration.get("extras", []) if isinstance(declaration, dict) else []
        roots.append(name + ("[" + ",".join(extras) + "]" if extras else ""))
    closure = dependency_closure(
        roots, lambda name: metadata.distribution(name).requires or ()
    )
    site = Path(sysconfig.get_path("purelib")).resolve()
    scripts = Path(sysconfig.get_path("scripts")).resolve()
    selected_sources = {name: local[name] for name in closure if name in local}
    output.mkdir(parents=True)
    for name in sorted(closure):
        distribution = metadata.distribution(name)
        if distribution.files is None:
            raise ValueError(f"distribution has no file record: {name}")
        for entry in distribution.files:
            source = Path(distribution.locate_file(entry)).resolve()
            if not source.is_file():
                if source.suffix == ".pyc":
                    continue
                raise ValueError(f"missing runtime distribution file: {name}")
            if source.is_relative_to(site):
                destination = output / "site-packages" / source.relative_to(site)
            elif source.is_relative_to(scripts):
                destination = output / "bin" / source.relative_to(scripts)
            elif any(source.is_relative_to(path) for path in selected_sources.values()):
                continue
            else:
                raise ValueError(
                    f"distribution file lies outside runtime roots: {name}"
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    for source in selected_sources.values():
        relative = source.relative_to(root.resolve())
        shutil.copytree(source, output / relative)
    (output / "runtime-inventory.json").write_text(
        json.dumps(
            {"distributions": sorted(closure), "local": sorted(selected_sources)},
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    bundle(args.root.resolve(), args.output.resolve())
