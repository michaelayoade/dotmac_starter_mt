"""Record a pinned source review; this is evidence, not a rights-verification gate."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tomllib
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]

    def git(*arguments: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(root), *arguments], text=True, encoding="utf-8"
        )

    revision = git("rev-parse", "--verify", args.revision + "^{commit}").strip()
    tree = git("ls-tree", "-r", revision)
    entries = [line.split("\t", 1) for line in tree.splitlines()]
    paths = {path for _, path in entries}
    directories = sorted(
        {path.split("/")[1] for path in paths if path.startswith("packages/")}
    )
    packages = []
    leads = []
    pattern = re.compile(
        r"ported from|adapted from|copied from|copyright|SPDX-License-Identifier",
        re.IGNORECASE,
    )
    for directory in directories:
        prefix = "packages/" + directory + "/"
        metadata = tomllib.loads(
            git("show", revision + ":" + prefix + "pyproject.toml")
        )
        extraction = tomllib.loads(
            git("show", revision + ":" + prefix + "EXTRACTION.toml")
        )
        poetry = metadata["tool"]["poetry"]
        package_paths = sorted(path for path in paths if path.startswith(prefix))
        packages.append(
            {
                "directory": prefix.rstrip("/"),
                "distribution": poetry["name"],
                "version": poetry["version"],
                "source_tree": git(
                    "rev-parse", revision + ":" + prefix.rstrip("/")
                ).strip(),
                "tracked_files": len(package_paths),
                "classification": extraction.get("classification"),
                "engineering_status": extraction.get("status"),
                "source_mode": extraction.get("source_mode"),
                "functional_owner": extraction.get("owner"),
                "declared_source_repositories": extraction.get(
                    "source_repositories", []
                ),
                "declared_source_revisions": extraction.get("source_revisions", []),
                "declared_source_paths": extraction.get("source_paths", []),
                "included_notices": [
                    path
                    for path in package_paths
                    if any(
                        word in Path(path).name.upper()
                        for word in ("LICENSE", "NOTICE", "OFL")
                    )
                ],
                "rights_basis": (
                    "requester ownership attestation and public-use authorization; "
                    "third-party rights reserved"
                ),
            }
        )
        for path in package_paths:
            if Path(path).suffix not in {
                ".py",
                ".rs",
                ".js",
                ".css",
                ".html",
                ".c",
                ".h",
            }:
                continue
            content = git("show", revision + ":" + path)
            for number, line in enumerate(content.splitlines(), 1):
                if pattern.search(line):
                    leads.append(f"{path}:{number}: {line[:500]}")
    output = root / "docs/inventories"
    output.mkdir(parents=True, exist_ok=True)
    (output / "starter-public-packages.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_revision": revision,
                "package_count": len(packages),
                "scope": "all tracked packages at this revision; no packages excluded",
                "evidence_limits": (
                    "Technical declarations and requester-attested authority, not "
                    "independently verified chain of title or runtime qualification."
                ),
                "packages": packages,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (output / "starter-public-source-files.txt").write_text(
        tree, encoding="utf-8", newline="\n"
    )
    (output / "starter-public-provenance-leads.txt").write_text(
        "\n".join(leads) + "\n", encoding="utf-8", newline="\n"
    )
    print(
        f"Recorded {len(packages)} packages at {revision}; "
        f"{len(leads)} provenance leads"
    )


if __name__ == "__main__":
    main()
