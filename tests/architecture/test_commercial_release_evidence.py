"""Tagged commercial releases are recorded without claiming adoption."""

from __future__ import annotations

import subprocess  # nosec B404 - fixed git argv; no shell or user input
import tomllib
from collections.abc import Mapping
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RELEASES = {
    "dotmac-billing": (
        "dotmac-billing-v0.1.0a1",
        "92a1626b16d7e068f92536d8cfcb2ef9b6f270c2",
    ),
    "dotmac-collections": (
        "dotmac-collections-v0.1.0a1",
        "6ecf518a6985b8bf4b163eccb3de2fef171ecccc",
    ),
}
EVIDENCE_FIELDS = frozenset({"kind", "tag", "peeled_commit"})


def _peeled_commit(tag: str) -> str:
    result = subprocess.run(  # noqa: S603 # nosec B603
        ["git", "rev-parse", f"{tag}^{{}}"],  # noqa: S607 # nosec B607
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"the publication oracle tag {tag!r} cannot be resolved: "
        f"{result.stderr.strip()}"
    )
    return result.stdout.strip()


def _evidence_problems(
    *,
    distribution: str,
    dossier: Mapping[str, object],
    current_version: str,
    expected_tag: str,
    expected_commit: str,
    actual_commit: str,
) -> list[str]:
    problems: list[str] = []
    current_tag = f"{distribution}-v{current_version}"
    if expected_tag != current_tag:
        problems.append(
            f"{distribution}: recorded released tag {expected_tag!r} is not "
            f"the current package tag {current_tag!r}"
        )
    if dossier.get("status") != "audit-complete":
        problems.append(
            f"{distribution}: publication is not adoption; status must remain "
            "audit-complete"
        )
    if dossier.get("contract_consumers") != []:
        problems.append(
            f"{distribution}: publication proves no consumer; "
            "contract_consumers must remain empty"
        )

    evidence = dossier.get("publication_evidence")
    if not isinstance(evidence, Mapping):
        problems.append(f"{distribution}: publication_evidence must be a table")
        return problems
    if set(evidence) != EVIDENCE_FIELDS:
        problems.append(
            f"{distribution}: publication_evidence fields are "
            f"{sorted(evidence)}, expected {sorted(EVIDENCE_FIELDS)}"
        )
        return problems
    if evidence["kind"] != "peeled_tag":
        problems.append(
            f"{distribution}: publication_evidence.kind must be 'peeled_tag'"
        )
    if evidence["tag"] != expected_tag:
        problems.append(
            f"{distribution}: publication evidence names {evidence['tag']!r}, "
            f"expected {expected_tag!r}"
        )
    if evidence["peeled_commit"] != expected_commit:
        problems.append(
            f"{distribution}: dossier commit is {evidence['peeled_commit']!r}, "
            f"expected {expected_commit!r}"
        )
    if actual_commit != expected_commit:
        problems.append(
            f"{distribution}: git resolves {expected_tag!r} to "
            f"{actual_commit!r}, expected {expected_commit!r}"
        )
    return problems


def test_tagged_commercial_releases_are_evidenced_without_adoption() -> None:
    problems: list[str] = []
    for distribution, (tag, commit) in RELEASES.items():
        package = REPO / "packages" / distribution
        pyproject = tomllib.loads(
            (package / "pyproject.toml").read_text(encoding="utf-8")
        )
        dossier = tomllib.loads(
            (package / "EXTRACTION.toml").read_text(encoding="utf-8")
        )
        problems.extend(
            _evidence_problems(
                distribution=distribution,
                dossier=dossier,
                current_version=pyproject["tool"]["poetry"]["version"],
                expected_tag=tag,
                expected_commit=commit,
                actual_commit=_peeled_commit(tag),
            )
        )
    assert not problems, "commercial release evidence:\n" + "\n".join(problems)


def test_the_guard_rejects_false_adoption_and_a_moved_tag() -> None:
    planted = {
        "status": "adopted",
        "contract_consumers": ["dotmac_sub"],
        "publication_evidence": {
            "kind": "peeled_tag",
            "tag": "dotmac-example-v0.1.0a1",
            "peeled_commit": "0" * 40,
        },
    }
    problems = _evidence_problems(
        distribution="dotmac-example",
        dossier=planted,
        current_version="0.1.0a1",
        expected_tag="dotmac-example-v0.1.0a1",
        expected_commit="0" * 40,
        actual_commit="1" * 40,
    )
    assert len(problems) == 3
    assert "status must remain audit-complete" in problems[0]
    assert "publication proves no consumer" in problems[1]
    assert "git resolves" in problems[2]
