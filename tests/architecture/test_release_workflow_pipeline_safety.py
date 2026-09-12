"""Workflow runner shape is the failure-propagation control.

This test intentionally does not parse arbitrary Bash.  Every workflow's
``run`` blocks use one explicit hardened GitHub Actions shell invocation, so
pipeline status is supplied by the runner rather than inferred from source.
Opaque shell execution constructs are closed by a small textual ban instead
of a partial shell interpreter.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
CANONICAL_SHELL = "bash --noprofile --norc -eo pipefail {0}"

# These constructs create another evaluation boundary or revoke the runner's
# failure rule. They are forbidden in workflow run source; this is a closed
# textual policy, not a claim that the patterns parse arbitrary Bash. Nested
# shells and option weakening are position-independent. Source/dot and eval
# need command-token positions so ordinary paths and words remain usable.
_COMMAND_TOKEN_PREFIX = (
    r"(?:^|[;\n]|&&|\|\||\||[({])\s*"
    r"(?:(?:if|then|do|else|elif|!|command|builtin)\s+)*"
)
_BANNED_RUN_SOURCE = {
    "nested bash/sh -c": re.compile(r"(?:\S*/)?(?:bash|sh)\b[^\n]*\s-c\b"),
    "shell heredoc": re.compile(r"(?:\S*/)?(?:bash|sh)\b[^\n]*<<"),
    "eval": re.compile(_COMMAND_TOKEN_PREFIX + r"eval\b"),
    "source/dot": re.compile(_COMMAND_TOKEN_PREFIX + r"(?:source\b|\.\s+)"),
    "option weakening": re.compile(r"\bset\s+\+(?:[A-Za-z]*e[A-Za-z]*|o\s+\S+)"),
}


def _run_blocks(document: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    blocks: list[tuple[str, dict[str, Any]]] = []
    for job_name, job in document.get("jobs", {}).items():
        if not isinstance(job, dict):
            continue
        for step in job.get("steps", []):
            if isinstance(step, dict) and isinstance(step.get("run"), str):
                blocks.append((job_name, step))
    return blocks


def _workflow_violations(path: Path, document: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    shell = document.get("defaults", {}).get("run", {}).get("shell")
    if shell != CANONICAL_SHELL:
        violations.append(f"{path.name}: defaults.run.shell is not canonical")
    for job_name, job in document.get("jobs", {}).items():
        if not isinstance(job, dict):
            continue
        job_shell = job.get("defaults", {}).get("run", {}).get("shell")
        if job_shell is not None and job_shell != CANONICAL_SHELL:
            violations.append(
                f"{path.name}:{job_name}: job defaults shell escapes "
                "the canonical shell"
            )
    for job_name, step in _run_blocks(document):
        override = step.get("shell")
        if override is not None and override != CANONICAL_SHELL:
            violations.append(
                f"{path.name}:{job_name}: step shell escapes the canonical shell"
            )
        run = step["run"]
        for name, pattern in _BANNED_RUN_SOURCE.items():
            if pattern.search(run):
                violations.append(
                    f"{path.name}:{job_name}: forbidden {name} in run source"
                )
    return violations


def _load(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict), path
    return document


def _run_canonical(tmp_path: Path, source: str) -> subprocess.CompletedProcess[str]:
    script = tmp_path / "workflow-step.sh"
    script.write_text(source, encoding="utf-8")
    argv = shlex.split(CANONICAL_SHELL.format(script))
    return subprocess.run(argv, capture_output=True, text=True)  # noqa: S603


def test_every_workflow_uses_the_canonical_hardened_shell() -> None:
    violations = [
        violation
        for path in sorted(WORKFLOW_DIR.glob("*.yml"))
        for violation in _workflow_violations(path, _load(path))
    ]
    assert not violations, "\n".join(violations)


def test_guard_detects_a_removed_workflow_default() -> None:
    document = {"defaults": {"run": {"shell": "bash"}}, "jobs": {}}
    assert _workflow_violations(Path("planted.yml"), document) == [
        "planted.yml: defaults.run.shell is not canonical"
    ]


def test_guard_detects_a_step_shell_override() -> None:
    document = {
        "defaults": {"run": {"shell": CANONICAL_SHELL}},
        "jobs": {"check": {"steps": [{"run": "true", "shell": "bash"}]}},
    }
    assert _workflow_violations(Path("planted.yml"), document) == [
        "planted.yml:check: step shell escapes the canonical shell"
    ]


def test_guard_detects_a_job_default_shell_override() -> None:
    document = {
        "defaults": {"run": {"shell": CANONICAL_SHELL}},
        "jobs": {
            "check": {
                "defaults": {"run": {"shell": "sh"}},
                "steps": [{"run": 'false | tee -a "$GITHUB_OUTPUT"'}],
            }
        },
    }
    assert _workflow_violations(Path("planted.yml"), document) == [
        "planted.yml:check: job defaults shell escapes the canonical shell"
    ]


def test_guard_refuses_opaque_shell_boundaries() -> None:
    for name, source in {
        "nested bash/sh -c": "bash -c 'false | tee'",
        "shell heredoc": "bash <<'SCRIPT'\nfalse | tee\nSCRIPT",
        "eval": "eval 'false | tee'",
        "source/dot": ". scripts/setup.sh",
        "option weakening": "set +o pipefail",
    }.items():
        document = {
            "defaults": {"run": {"shell": CANONICAL_SHELL}},
            "jobs": {"check": {"steps": [{"run": source}]}},
        }
        assert _workflow_violations(Path("planted.yml"), document) == [
            f"planted.yml:check: forbidden {name} in run source"
        ]


def test_guard_refuses_wrapped_and_combined_option_weakening() -> None:
    for source in (
        "set +e",
        "set +eu",
        "command set +o pipefail",
        "builtin set +o pipefail",
        'opt=pipefail; set +o "$opt"',
        "f() { set +o pipefail; }",
    ):
        document = {
            "defaults": {"run": {"shell": CANONICAL_SHELL}},
            "jobs": {"check": {"steps": [{"run": source}]}},
        }
        assert _workflow_violations(Path("planted.yml"), document) == [
            "planted.yml:check: forbidden option weakening in run source"
        ]


def test_guard_refuses_position_independent_boundary_and_weakening_forms() -> None:
    for name, source in (
        ("nested bash/sh -c", "command bash -c 'false | tee'"),
        ("nested bash/sh -c", "if bash -c 'false | tee'; then true; fi"),
        ("option weakening", "if set +e; then true; fi"),
        ("option weakening", "true && set +e"),
        ("option weakening", "( set +e )"),
    ):
        document = {
            "defaults": {"run": {"shell": CANONICAL_SHELL}},
            "jobs": {"check": {"steps": [{"run": source}]}},
        }
        assert _workflow_violations(Path("planted.yml"), document) == [
            f"planted.yml:check: forbidden {name} in run source"
        ]


def test_guard_refuses_wrapped_dynamic_boundaries_but_not_command_v() -> None:
    for source in (
        "command eval 'false | tee'",
        "builtin eval 'false | tee'",
        "command . x",
    ):
        document = {
            "defaults": {"run": {"shell": CANONICAL_SHELL}},
            "jobs": {"check": {"steps": [{"run": source}]}},
        }
        assert _workflow_violations(Path("planted.yml"), document) == [
            "planted.yml:check: forbidden "
            f"{'source/dot' if source.endswith('x') else 'eval'} in run source"
        ]

    command_v = {
        "defaults": {"run": {"shell": CANONICAL_SHELL}},
        "jobs": {"check": {"steps": [{"run": "command -v docker >/dev/null"}]}},
    }
    assert _workflow_violations(Path("planted.yml"), command_v) == []


def test_guard_recognizes_source_dot_as_command_tokens_only() -> None:
    for source in (
        '. "$SCRIPT"',
        'source "$SCRIPT"',
        'command . "$SCRIPT"',
        'command source "$SCRIPT"',
        'if source "$SCRIPT"; then true; fi',
        'true && . "$SCRIPT"',
        '( command source "$SCRIPT" )',
    ):
        document = {
            "defaults": {"run": {"shell": CANONICAL_SHELL}},
            "jobs": {"check": {"steps": [{"run": source}]}},
        }
        assert _workflow_violations(Path("planted.yml"), document) == [
            "planted.yml:check: forbidden source/dot in run source"
        ]

    for source in (
        "python tool.py --root .",
        ".lane3-foundation/bin/python -E",
        "python verify-source --help",
        'echo "source address"',
    ):
        document = {
            "defaults": {"run": {"shell": CANONICAL_SHELL}},
            "jobs": {"check": {"steps": [{"run": source}]}},
        }
        assert _workflow_violations(Path("planted.yml"), document) == []


def test_canonical_shell_propagates_resolver_failure_through_tee(
    tmp_path: Path,
) -> None:
    output = tmp_path / "github-output"
    failed = _run_canonical(tmp_path, f'false | tee -a "{output}"\n')
    assert failed.returncode != 0

    succeeded = _run_canonical(tmp_path, f'printf "answer=42\\n" | tee -a "{output}"\n')
    assert succeeded.returncode == 0, succeeded.stderr
    assert output.read_text(encoding="utf-8") == "answer=42\n"


def test_set_plus_e_can_hide_a_failed_pipeline_from_the_canonical_shell(
    tmp_path: Path,
) -> None:
    output = tmp_path / "github-output"
    weakened = _run_canonical(
        tmp_path,
        f'set +e\nfalse | tee -a "{output}"\ntrue\n',
    )
    assert weakened.returncode == 0
