"""The reusable image-audit collector produces complete evidence or refuses.

The image contract requires a configured numeric non-root ``USER``.  Running
the filesystem walk as that user made root-owned base-image paths unreadable;
the old workflow then discarded the partial listing and handed an empty file to
the audit.  Inspection privilege and runtime privilege are different facts:
the collector may be a one-shot uid/gid 0 process while ``audit_image`` still
checks the configured ``Config.User`` from Docker inspect.

These tests execute the exact collector block extracted from the reusable
workflow behind a fake Docker boundary.  The negative control proves a complete
walk proceeds; planted failures prove a missing root override and a partial
walk cannot masquerade as audit evidence (ADR-0018).
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "deployment-conformance.yml"


def _collector_block() -> str:
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = document["jobs"]["image"]["steps"]
    audit_step = next(
        step
        for step in steps
        if step.get("name") == "Audit the image the descriptor pins"
    )
    source = str(audit_step["run"])
    match = re.search(r"(?ms)^if ! docker run .*?^fi$", source)
    assert match is not None, "the image-audit step has no explicit collector refusal"
    return match.group(0)


@dataclass(frozen=True, slots=True)
class CollectorResult:
    process: subprocess.CompletedProcess[str]
    docker_call: str
    layers: str
    continued: bool


def _execute_collector(
    tmp_path: Path,
    *,
    docker_mode: str,
    source: str | None = None,
) -> CollectorResult:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" > "$AUDIT_DOCKER_CALL"
case " $* " in
  *" --user 0:0 "*) ;;
  *) echo "collector did not request inspection uid/gid 0" >&2; exit 97 ;;
esac
printf '%s\n' '/opt/app/app/main.py'
if [ "$AUDIT_DOCKER_MODE" = "partial-failure" ]; then
  echo "find: /root-owned: Permission denied" >&2
  exit 23
fi
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)

    layers = tmp_path / "layers.txt"
    docker_call = tmp_path / "docker-call.txt"
    continued = tmp_path / "continued"
    script = "\n".join(
        (
            "set -euo pipefail",
            'reference="registry.example.invalid/product@sha256:' + "a" * 64 + '"',
            'layers_file="$AUDIT_LAYERS_FILE"',
            source or _collector_block(),
            'printf reached > "$AUDIT_CONTINUED"',
        )
    )
    environment = os.environ.copy()
    environment.update(
        {
            "AUDIT_CONTINUED": str(continued),
            "AUDIT_DOCKER_CALL": str(docker_call),
            "AUDIT_DOCKER_MODE": docker_mode,
            "AUDIT_LAYERS_FILE": str(layers),
            "PATH": f"{fake_bin}:{environment['PATH']}",
        }
    )
    # The script is extracted from this repository's reviewed workflow and
    # every injected path/value above is test-owned, never user input.
    process = subprocess.run(  # noqa: S603
        ["/bin/bash", "-c", script],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )
    return CollectorResult(
        process=process,
        docker_call=docker_call.read_text(encoding="utf-8"),
        layers=layers.read_text(encoding="utf-8") if layers.exists() else "",
        continued=continued.exists(),
    )


def test_a_complete_root_inspection_proceeds_with_its_evidence(tmp_path: Path) -> None:
    result = _execute_collector(tmp_path, docker_mode="complete")

    assert result.process.returncode == 0, result.process.stderr
    assert result.continued
    assert result.layers == "/opt/app/app/main.py\n"
    assert "--user 0:0" in result.docker_call
    assert "--entrypoint sh" in result.docker_call
    assert "find / -xdev -type f" in result.docker_call


def test_a_partial_walk_refuses_and_preserves_diagnostics(tmp_path: Path) -> None:
    result = _execute_collector(tmp_path, docker_mode="partial-failure")

    assert result.process.returncode == 1
    assert not result.continued, "the audit must not consume partial evidence"
    assert (
        result.layers == "/opt/app/app/main.py\n"
    ), "partial evidence should remain available for diagnosis, never be truncated"
    assert "find: /root-owned: Permission denied" in result.process.stderr
    assert "filesystem inspection failed" in result.process.stderr
    assert "image audit was not run against partial evidence" in result.process.stderr


def test_removing_the_inspection_root_override_is_a_planted_failure(
    tmp_path: Path,
) -> None:
    mutated = _collector_block().replace("--user 0:0 ", "", 1)
    assert mutated != _collector_block(), "the mutation did not alter the collector"

    result = _execute_collector(tmp_path, docker_mode="complete", source=mutated)

    assert result.process.returncode == 1
    assert not result.continued
    assert "collector did not request inspection uid/gid 0" in result.process.stderr


def test_the_old_truncating_fallback_would_defeat_the_failure_fixture(
    tmp_path: Path,
) -> None:
    old_collector = (
        'docker run --rm --entrypoint sh "$reference" '
        "-c 'find / -xdev -type f 2>/dev/null' "
        '> "$layers_file" || : > "$layers_file"'
    )
    # Give the historical shape the root override so this mutation isolates the
    # second defect: converting a failed partial walk into empty "evidence".
    old_collector = old_collector.replace("--rm ", "--rm --user 0:0 ", 1)

    result = _execute_collector(
        tmp_path,
        docker_mode="partial-failure",
        source=old_collector,
    )

    assert result.process.returncode == 0
    assert result.continued, "the historical fallback incorrectly continued"
    assert result.layers == "", "the historical fallback truncated partial output"
