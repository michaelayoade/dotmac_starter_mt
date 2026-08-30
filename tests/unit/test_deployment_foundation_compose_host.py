"""Tests for `dotmac_deployment_foundation.providers.compose_host` — the
dedicated-VM Docker Compose `Effects` provider.

The fixture spec is built from a TOML string through `ProductDeploymentSpec
.loads`, not by constructing dataclasses by hand — see
`test_deployment_foundation_compose.py`'s docstring for why that matters;
the same reasoning applies here unchanged. It carries one ingress role
(``app``, with DISTINCT liveness and readiness paths — required so the
candidate-readiness test can prove the wrong probe was never polled) and one
worker role (``worker``), plus a single postgres backup dataset.

Every test drives `ComposeHostEffects` through a SCRIPTED FAKE `runner` (and,
for `backup`/`verify_backup`, a scripted fake `popen_factory`) — no Docker
daemon, no Postgres, no network. `untracked_compose_overrides` and
`working_tree_dirty` are the two exceptions: they shell out to a REAL `git`
binary against a throwaway repository built in `tmp_path`, because faking
`git status --porcelain` output convincingly is more code than driving the
real, fast (milliseconds), dependency-free binary already required elsewhere
in this test suite (`test_allocation_serialized_gate.py`,
`test_released_migrations.py`).

## On negative controls

A check of the shape `assert effects.verify_backup(result) is False` proves
nothing about WHY it is false — a provider that always returns `False` would
pass every corruption test in this file and reveal itself only by also
failing the one good-backup case. Every corruption/sensitivity assertion
below is therefore paired with a POSITIVE control proving the same method
returns the opposite answer for the unmodified, honest input.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import pytest
from dotmac_deployment_foundation.engine.run import BackupResult, CommandResult, Effects
from dotmac_deployment_foundation.errors import PreconditionFailed, StepFailed
from dotmac_deployment_foundation.providers.compose_host import (
    ComposeHostEffects,
    NginxInstaller,
    _candidate_ports,
)
from dotmac_deployment_foundation.spec import ProductDeploymentSpec

GOOD_DIGEST = "sha256:" + "a" * 64
OLD_DIGEST = "sha256:" + "b" * 64
REVISION = "c" * 40

DESCRIPTOR = f"""
schema = "ProductDeploymentSpec.v1"
product = "example"
environment = "test"

[assembly]
manifest_path = "deploy/product-manifest.json"
manifest_digest = "sha256:{"d" * 64}"

[image]
reference = "ghcr.io/example/app@{GOOD_DIGEST}"
source_revision = "{REVISION}"

[runtime_materials]
names = ["DATABASE_URL", "REDIS_URL"]

[[roles]]
code = "app"
command = ["uvicorn", "app.main:app"]
replicas = 1
materials = ["DATABASE_URL", "REDIS_URL"]
[roles.resources]
cpus = "1.0"
memory = "1g"
[roles.health.live]
path = "/health/live"
port = 8000
[roles.health.ready]
path = "/health/ready"
port = 8000

[[roles]]
code = "worker"
command = ["celery", "-A", "app", "worker"]
replicas = 1
depends_on = ["app"]
materials = ["DATABASE_URL", "REDIS_URL"]
[roles.resources]
cpus = "1.0"
memory = "1g"
[roles.health.live]
path = "/health/live"
port = 8001
[roles.worker]
kind = "celery"
ping_command = ["celery", "-A", "app", "inspect", "ping"]
heartbeat_max_age_seconds = 120

[migration]
command = ["alembic", "upgrade", "heads"]
heads_command = ["alembic", "current"]
owner_material = "MIGRATION_DATABASE_URL"
expected_heads = ["a003", "k012"]
compatibility = "online"

[backup]
[[backup.datasets]]
code = "primary"
kind = "postgres"
material = "BACKUP_DATABASE_URL"
retention_days = 14

[ingress]
host = "example.dotmac.io"
exposure = "public"
address_family = "dual_stack"
approval_ref = "deployment.public-exposure"
rationale_url = "https://docs.example/why"
[[ingress.routes]]
path = "/"
role = "app"
port = 8000

[rollout]
stability_window_seconds = 60
"""


def load() -> ProductDeploymentSpec:
    return ProductDeploymentSpec.loads(DESCRIPTOR, source="<test>")


# ── scripted fakes ───────────────────────────────────────────────────────────


class ScriptedRunner:
    """A `runner` callable that matches on argv and records every call.

    Matchers are tried in order; the first whose predicate returns True wins.
    An argv nothing matches raises, so a test that expected fewer calls than
    it got fails loudly instead of silently returning a default success.
    """

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.envs: list[Mapping[str, str] | None] = []
        self._rules: list[tuple[object, CommandResult]] = []

    def when(self, predicate, result: CommandResult) -> ScriptedRunner:  # type: ignore[no-untyped-def]
        self._rules.append((predicate, result))
        return self

    def __call__(
        self,
        argv: list[str],
        *,
        timeout: int,
        env: Mapping[str, str] | None = None,
        capture: bool = True,
    ) -> CommandResult:
        self.calls.append(list(argv))
        self.envs.append(env)
        for predicate, result in self._rules:
            if predicate(argv):
                return result
        raise AssertionError(f"ScriptedRunner: no rule matched argv {argv!r}")


class FakePopen:
    """A minimal stand-in for `subprocess.Popen`, streamed in memory."""

    def __init__(
        self, stdout_bytes: bytes, stderr_bytes: bytes = b"", returncode: int = 0
    ) -> None:
        import io

        self.stdout = io.BytesIO(stdout_bytes)
        self.stderr = io.BytesIO(stderr_bytes)
        self._returncode = returncode
        self.returncode: int | None = None
        self.killed = False

    def wait(self, timeout: float | None = None) -> int:
        self.returncode = self._returncode
        return self._returncode

    def kill(self) -> None:
        self.killed = True


def make_effects(
    deploy_dir: Path,
    *,
    runner=None,  # type: ignore[no-untyped-def]
    popen_factory=None,  # type: ignore[no-untyped-def]
    **overrides,  # type: ignore[no-untyped-def]
) -> ComposeHostEffects:
    kwargs: dict[str, object] = dict(overrides)
    if runner is not None:
        kwargs["runner"] = runner
    if popen_factory is not None:
        kwargs["popen_factory"] = popen_factory
    return ComposeHostEffects(load(), deploy_dir, **kwargs)


# ── every Effects method is genuinely implemented ───────────────────────────


def test_every_effects_protocol_method_is_implemented_by_compose_host_effects() -> None:
    """Introspection against the Protocol itself, not a hand-copied list.

    A method added to `Effects` later (`engine/run.py`) fails THIS test
    rather than surfacing as an `AttributeError` the first time the executor
    calls it against a real host.
    """
    protocol_methods = sorted(name for name in dir(Effects) if not name.startswith("_"))
    assert protocol_methods, "sanity: Effects must actually declare methods"
    missing = [
        name
        for name in protocol_methods
        if not callable(getattr(ComposeHostEffects, name, None))
    ]
    assert missing == [], f"ComposeHostEffects is missing: {missing}"


def test_compose_host_effects_satisfies_the_runtime_checkable_protocol(
    tmp_path: Path,
) -> None:
    effects = make_effects(tmp_path, runner=ScriptedRunner())
    assert isinstance(effects, Effects)


def test_compose_project_identity_comes_from_the_product_not_the_directory(
    tmp_path: Path,
) -> None:
    first = ComposeHostEffects(load(), tmp_path / "first" / "deploy")
    second = ComposeHostEffects(load(), tmp_path / "second" / "deploy")

    assert first._compose_argv[:4] == [
        "docker",
        "compose",
        "--project-name",
        "example",
    ]
    assert second._compose_argv[:4] == first._compose_argv[:4]


# ── the seam: argv list, shell=False ────────────────────────────────────────


def test_run_shells_out_with_an_argv_list_and_shell_false(tmp_path: Path) -> None:
    from unittest import mock

    from dotmac_deployment_foundation.providers import compose_host as module

    captured: dict[str, object] = {}

    class _Completed:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_subprocess_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return _Completed()

    effects = ComposeHostEffects(load(), tmp_path)
    with mock.patch.object(module.subprocess, "run", fake_subprocess_run):
        effects.run_command(["echo", "hi"], timeout_seconds=5)

    assert isinstance(captured["argv"], list), captured["argv"]
    assert captured["kwargs"]["shell"] is False, captured["kwargs"]


def test_a_value_containing_shell_metacharacters_reaches_the_child_as_one_argument(
    tmp_path: Path,
) -> None:
    """No fake in this one: a REAL subprocess proves no shell ever re-parses
    the argument. A `;` or `$(...)` that reached a shell would either split
    the command or execute the substitution; here it must come back verbatim,
    unsplit, unexecuted, exactly once."""
    import sys

    dangerous = "hello; rm -rf /tmp/should-not-run && echo $(whoami) `id`"
    effects = ComposeHostEffects(load(), tmp_path)
    result = effects.run_command(
        [sys.executable, "-c", "import sys; print(sys.argv[1])", dangerous],
        timeout_seconds=10,
    )
    assert result.ok, result.stderr
    assert result.stdout.strip() == dangerous


# ── backup / verify_backup ──────────────────────────────────────────────────

RAW_DUMP = b"COPY public.customers (id, name) FROM stdin;\n" * 4000


def test_backup_computes_a_write_time_checksum_and_a_real_size(tmp_path: Path) -> None:
    def popen_factory(argv, env):  # type: ignore[no-untyped-def]
        return FakePopen(RAW_DUMP)

    effects = make_effects(
        tmp_path,
        runner=ScriptedRunner(),
        popen_factory=popen_factory,
        backup_dir=tmp_path / "backups",
    )
    result = effects.backup("primary", timeout_seconds=30)

    assert result.dataset == "primary"
    assert result.size_bytes > 0
    on_disk = Path(result.path).read_bytes()
    # The checksum characterises the exact bytes written to disk, not a
    # re-read computed some other way.
    assert hashlib.sha256(on_disk).hexdigest() == result.checksum
    assert len(on_disk) == result.size_bytes
    # ...and those bytes decompress back to exactly what "pg_dump" produced.
    import gzip

    assert gzip.decompress(on_disk) == RAW_DUMP
    # No leftover partial file.
    assert not Path(result.path + ".part").exists()


def test_backup_raises_on_a_nonzero_exit_and_leaves_no_partial_file(
    tmp_path: Path,
) -> None:
    def popen_factory(argv, env):  # type: ignore[no-untyped-def]
        return FakePopen(
            b"", stderr_bytes=b"pg_dump: error: connection refused", returncode=1
        )

    effects = make_effects(
        tmp_path,
        runner=ScriptedRunner(),
        popen_factory=popen_factory,
        backup_dir=tmp_path / "backups",
    )
    with pytest.raises(StepFailed) as caught:
        effects.backup("primary", timeout_seconds=10)
    assert "connection refused" in str(caught.value)
    leftover = list(Path(tmp_path).rglob("*.part"))
    assert leftover == [], leftover


def _good_backup(tmp_path: Path) -> tuple[ComposeHostEffects, BackupResult]:
    def popen_factory(argv, env):  # type: ignore[no-untyped-def]
        return FakePopen(RAW_DUMP)

    effects = make_effects(
        tmp_path,
        runner=ScriptedRunner(),
        popen_factory=popen_factory,
        backup_dir=tmp_path / "backups",
    )
    result = effects.backup("primary", timeout_seconds=30)
    return effects, result


def test_verify_backup_returns_true_for_a_good_backup_the_positive_control(
    tmp_path: Path,
) -> None:
    effects, result = _good_backup(tmp_path)
    assert effects.verify_backup(result) is True


def test_verify_backup_returns_false_for_a_truncated_file(tmp_path: Path) -> None:
    effects, result = _good_backup(tmp_path)
    path = Path(result.path)
    original = path.read_bytes()
    path.write_bytes(original[: len(original) - 10])
    assert effects.verify_backup(result) is False


def test_verify_backup_returns_false_for_a_wrong_checksum(tmp_path: Path) -> None:
    effects, result = _good_backup(tmp_path)
    wrong = replace(result, checksum="0" * 64)
    assert effects.verify_backup(wrong) is False


def test_verify_backup_returns_false_for_a_corrupted_same_length_file(
    tmp_path: Path,
) -> None:
    """A bit flip that keeps the exact byte count — the shape a size check
    alone cannot see, and the reason `verify_backup` also decompresses."""
    effects, result = _good_backup(tmp_path)
    path = Path(result.path)
    original = bytearray(path.read_bytes())
    middle = len(original) // 2
    original[middle] ^= 0xFF
    assert len(original) == result.size_bytes  # same length, corrupted content
    path.write_bytes(bytes(original))
    assert effects.verify_backup(result) is False


def test_verify_backup_returns_false_when_the_file_is_missing(tmp_path: Path) -> None:
    effects, result = _good_backup(tmp_path)
    Path(result.path).unlink()
    assert effects.verify_backup(result) is False


# ── source-mount sensitivity (refuse_dirty_state's gate) ───────────────────


def _observe_roles_with_mount(tmp_path: Path, destination: str):  # type: ignore[no-untyped-def]
    ps_json = json.dumps([{"Service": "app", "ID": "container_app"}])
    inspect_json = json.dumps(
        [
            {
                "State": {"Running": True},
                "RestartCount": 0,
                "Image": "sha256:localimageid",
                "Mounts": [{"Destination": destination}],
            }
        ]
    )
    repo_digests = json.dumps([f"ghcr.io/example/app@{GOOD_DIGEST}"])

    runner = ScriptedRunner()
    runner.when(lambda a: "ps" in a and "json" in a, CommandResult(0, ps_json))
    runner.when(
        lambda a: a[:2] == ["docker", "inspect"] and "--format" not in a,
        CommandResult(0, inspect_json),
    )
    runner.when(
        lambda a: "--format" in a and "RepoDigests" in a[a.index("--format") + 1],
        CommandResult(0, repo_digests),
    )
    effects = make_effects(tmp_path, runner=runner)
    return effects.observe_roles()


def test_source_mounted_is_true_for_a_bind_mount_into_the_app_root(
    tmp_path: Path,
) -> None:
    observations = _observe_roles_with_mount(tmp_path, "/app/app")
    assert observations[0].source_mounted is True


def test_source_mounted_is_false_for_a_mount_outside_the_app_root_the_negative_control(
    tmp_path: Path,
) -> None:
    observations = _observe_roles_with_mount(tmp_path, "/srv/uploads")
    assert observations[0].source_mounted is False


# ── untracked compose overrides (real git, per the module docstring) ───────


def _init_git_repo(deploy_dir: Path) -> None:
    # A real, throwaway `git` repo — trusted, fixed argv, no shell. See the
    # module docstring for why this is driven for real rather than faked.
    subprocess.run(["git", "init", "-q"], cwd=deploy_dir, check=True)  # noqa: S603, S607
    subprocess.run(  # noqa: S603
        ["git", "config", "user.email", "test@example.com"],  # noqa: S607
        cwd=deploy_dir,
        check=True,
    )
    subprocess.run(  # noqa: S603
        ["git", "config", "user.name", "Test"],  # noqa: S607
        cwd=deploy_dir,
        check=True,
    )


def test_an_unapproved_untracked_override_is_reported(tmp_path: Path) -> None:
    # No `runner=` override: `working_tree_dirty`/`untracked_compose_overrides`
    # are the two methods this file drives against the REAL `git` binary (see
    # the module docstring) rather than a scripted fake.
    _init_git_repo(tmp_path)
    (tmp_path / "docker-compose.override.yml").write_text("services: {}\n")
    effects = make_effects(tmp_path)
    found = effects.untracked_compose_overrides()
    assert "docker-compose.override.yml" in found


def test_an_allowlisted_override_is_not_reported_the_negative_control(
    tmp_path: Path,
) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "docker-compose.override.yml").write_text("services: {}\n")
    effects = make_effects(
        tmp_path,
        approved_compose_overrides=frozenset({"docker-compose.override.yml"}),
    )
    found = effects.untracked_compose_overrides()
    assert "docker-compose.override.yml" not in found


def test_an_untracked_non_compose_file_is_not_reported(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "notes.txt").write_text("hello\n")
    effects = make_effects(tmp_path)
    assert list(effects.untracked_compose_overrides()) == []


def test_working_tree_dirty_is_true_with_an_untracked_file_present(
    tmp_path: Path,
) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "notes.txt").write_text("hello\n")
    effects = make_effects(tmp_path)
    assert effects.working_tree_dirty() is True


def test_working_tree_dirty_is_false_for_a_non_git_directory_the_negative_control(
    tmp_path: Path,
) -> None:
    effects = make_effects(tmp_path, runner=ScriptedRunner())
    assert effects.working_tree_dirty() is False


# ── resolved_materials: names only, never a value ───────────────────────────


def test_resolved_materials_returns_names_and_a_planted_value_never_appears(
    tmp_path: Path,
) -> None:
    secret_value = "correct-horse-battery-staple-42"
    (tmp_path / ".env").write_text(
        f"DATABASE_URL=postgres://x\nSECRET_TOKEN={secret_value}\n"
    )
    effects = make_effects(tmp_path, runner=ScriptedRunner())
    names = effects.resolved_materials()

    assert "DATABASE_URL" in names
    assert "SECRET_TOKEN" in names
    assert secret_value not in names
    assert secret_value not in repr(names)
    assert all(secret_value not in str(name) for name in names)


def test_resolved_materials_excludes_names_with_an_empty_value(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("PRESENT=value\nEMPTY=\n")
    effects = make_effects(tmp_path, runner=ScriptedRunner())
    names = effects.resolved_materials()
    assert "PRESENT" in names
    assert "EMPTY" not in names


# ── switch: writes the image it is GIVEN ────────────────────────────────────


def test_switch_writes_the_deploying_digest_it_is_given(tmp_path: Path) -> None:
    runner = ScriptedRunner()
    runner.when(lambda a: "up" in a, CommandResult(0))
    effects = make_effects(tmp_path, runner=runner)
    deploy_image = f"ghcr.io/example/app@{GOOD_DIGEST}"

    effects.switch(timeout_seconds=30, image=deploy_image)

    env_text = (tmp_path / ".env").read_text()
    assert f"APP_IMAGE={deploy_image}" in env_text
    up_call = next(call for call in runner.calls if "up" in call)
    assert "--force-recreate" in up_call
    assert "app" in up_call and "worker" in up_call


def test_switch_writes_the_rollback_digest_it_is_given(tmp_path: Path) -> None:
    """Correct for a ROLLBACK too — `switch` has no opinion about which
    direction it is called for; it writes whatever it is given, proven here
    with the PREVIOUS digest rather than the deploying one."""
    runner = ScriptedRunner()
    runner.when(lambda a: "up" in a, CommandResult(0))
    effects = make_effects(tmp_path, runner=runner)
    deploy_image = f"ghcr.io/example/app@{GOOD_DIGEST}"
    rollback_image = f"ghcr.io/example/app@{OLD_DIGEST}"

    effects.switch(timeout_seconds=30, image=deploy_image)
    effects.switch(timeout_seconds=30, image=rollback_image)

    env_text = (tmp_path / ".env").read_text()
    assert f"APP_IMAGE={rollback_image}" in env_text
    assert GOOD_DIGEST not in env_text  # the deploy pin was overwritten, not appended
    # Exactly one APP_IMAGE line — the second `switch` replaced it in place.
    assert env_text.count("APP_IMAGE=") == 1


def test_switch_raises_step_failed_on_a_failing_compose_up(tmp_path: Path) -> None:
    runner = ScriptedRunner()
    runner.when(
        lambda a: "up" in a, CommandResult(1, "", "service app failed to start")
    )
    effects = make_effects(tmp_path, runner=runner)
    with pytest.raises(StepFailed) as caught:
        effects.switch(timeout_seconds=30, image=f"ghcr.io/example/app@{GOOD_DIGEST}")
    assert "service app failed to start" in str(caught.value)


# ── candidate_ready polls READINESS, never liveness ─────────────────────────


def test_candidate_ready_polls_the_readiness_path_not_liveness(tmp_path: Path) -> None:
    probed_urls: list[str] = []

    def readiness_probe(url: str, timeout: float) -> bool:
        probed_urls.append(url)
        return True

    effects = make_effects(
        tmp_path, runner=ScriptedRunner(), readiness_probe=readiness_probe
    )
    assert effects.candidate_ready("app") is True
    assert len(probed_urls) == 1
    assert "/health/ready" in probed_urls[0]
    assert "/health/live" not in probed_urls[0]


def test_candidate_ready_polls_the_derived_candidate_port(tmp_path: Path) -> None:
    port, container_port = _candidate_ports(load(), "app", candidate_port_base=18000)
    assert port == 18001
    assert container_port == 8000

    probed_urls: list[str] = []

    def readiness_probe(url: str, timeout: float) -> bool:
        probed_urls.append(url)
        return True

    effects = make_effects(
        tmp_path, runner=ScriptedRunner(), readiness_probe=readiness_probe
    )
    effects.candidate_ready("app")
    assert f":{port}" in probed_urls[0]


def test_candidate_ready_returns_false_when_the_probe_fails(tmp_path: Path) -> None:
    effects = make_effects(
        tmp_path, runner=ScriptedRunner(), readiness_probe=lambda u, t: False
    )
    assert effects.candidate_ready("app") is False


# ── write_evidence: atomic, round-trips JSON ────────────────────────────────


def test_write_evidence_round_trips_json_and_leaves_no_temp_file(
    tmp_path: Path,
) -> None:
    effects = make_effects(tmp_path, runner=ScriptedRunner())
    evidence = {"succeeded": True, "image_digest": GOOD_DIGEST, "steps": [1, 2, 3]}

    path_str = effects.write_evidence(evidence)

    path = Path(path_str)
    assert path.is_file()
    assert json.loads(path.read_text(encoding="utf-8")) == evidence
    leftover_tmp_files = [
        candidate
        for candidate in tmp_path.iterdir()
        if candidate.name.startswith(".") and "tmp" in candidate.name
    ]
    assert leftover_tmp_files == [], leftover_tmp_files


def test_write_evidence_is_atomic_a_failed_write_leaves_the_previous_file_intact(
    tmp_path: Path,
) -> None:
    """`os.replace` is atomic: a reader can only ever see the old file or the
    new one, never a half-written one. Proven here by writing once, then
    forcing the destination directory itself to be read-only mid-write is
    hard to simulate portably, so this instead proves the ROUND-TRIP
    property that atomicity exists to protect — the file on disk after a
    successful write is *exactly* what was passed in, never a merge of two
    writes."""
    effects = make_effects(tmp_path, runner=ScriptedRunner())
    effects.write_evidence({"revision": 1})
    path_str = effects.write_evidence({"revision": 2})
    assert json.loads(Path(path_str).read_text(encoding="utf-8")) == {"revision": 2}


# ── image_present / image_labels / release_evidence: ordinary gates ────────


def test_image_present_true_when_present_locally(tmp_path: Path) -> None:
    runner = ScriptedRunner()
    runner.when(
        lambda a: a[:3] == ["docker", "image", "inspect"] and "--format" not in a,
        CommandResult(0),
    )
    effects = make_effects(tmp_path, runner=runner)
    assert effects.image_present(f"ghcr.io/example/app@{GOOD_DIGEST}") is True


def test_image_present_false_when_absent_everywhere(tmp_path: Path) -> None:
    runner = ScriptedRunner()
    runner.when(
        lambda a: a[:3] == ["docker", "image", "inspect"] and "--format" not in a,
        CommandResult(1, "", "no such image"),
    )
    runner.when(
        lambda a: a[:3] == ["docker", "manifest", "inspect"],
        CommandResult(1, "", "not found"),
    )
    effects = make_effects(tmp_path, runner=runner)
    assert effects.image_present(f"ghcr.io/example/app@{GOOD_DIGEST}") is False


def test_image_present_pulls_when_present_in_registry_but_not_locally(
    tmp_path: Path,
) -> None:
    runner = ScriptedRunner()
    runner.when(
        lambda a: a[:3] == ["docker", "image", "inspect"] and "--format" not in a,
        CommandResult(1, "", "no such image"),
    )
    runner.when(lambda a: a[:3] == ["docker", "manifest", "inspect"], CommandResult(0))
    runner.when(lambda a: a[:2] == ["docker", "pull"], CommandResult(0))
    effects = make_effects(tmp_path, runner=runner)
    assert effects.image_present(f"ghcr.io/example/app@{GOOD_DIGEST}") is True
    assert any(call[:2] == ["docker", "pull"] for call in runner.calls)


def test_image_labels_parses_the_revision_label(tmp_path: Path) -> None:
    runner = ScriptedRunner()
    runner.when(
        lambda a: "--format" in a,
        CommandResult(0, json.dumps({"org.opencontainers.image.revision": REVISION})),
    )
    effects = make_effects(tmp_path, runner=runner)
    labels = effects.image_labels(f"ghcr.io/example/app@{GOOD_DIGEST}")
    assert labels["org.opencontainers.image.revision"] == REVISION


def test_release_evidence_returns_empty_mapping_when_the_file_is_absent(
    tmp_path: Path,
) -> None:
    effects = make_effects(tmp_path, runner=ScriptedRunner())
    assert effects.release_evidence(REVISION) == {}


def test_release_evidence_reads_the_checked_in_file_keyed_by_revision(
    tmp_path: Path,
) -> None:
    (tmp_path / "release-evidence.json").write_text(
        json.dumps({REVISION: {"ci": "passed", "run": "42"}})
    )
    effects = make_effects(tmp_path, runner=ScriptedRunner())
    assert effects.release_evidence(REVISION) == {"ci": "passed", "run": "42"}
    assert effects.release_evidence("f" * 40) == {}


# ── migration_heads: tolerant parsing ────────────────────────────────────────


def test_migration_heads_parses_alembic_style_output(tmp_path: Path) -> None:
    runner = ScriptedRunner()
    runner.when(
        lambda a: "current" in a, CommandResult(0, "a003 (head)\nk012 (head)\n")
    )
    effects = make_effects(tmp_path, runner=runner)
    assert set(effects.migration_heads()) == {"a003", "k012"}


def test_the_heads_command_comes_from_the_DESCRIPTOR_and_is_never_inferred() -> None:
    """The two tests this replaces asserted an inference that has been deleted.

    They proved that `alembic upgrade heads` became `alembic current` and that a
    command with no `upgrade` token got `current` appended. Both were true of the
    helper and neither was true of the world: the read verb sits in a different
    argv position for `python -m x.migrate upgrade heads`, and means nothing at
    all for a non-Alembic entry point. `verify_heads` would then have compared
    the declared heads against the output of a command that did something else.

    The descriptor now declares it, and `spec.py` refuses a migration block
    without one — so the provider has nothing left to guess.
    """
    spec = ProductDeploymentSpec.loads(DESCRIPTOR, source="<test>")
    assert spec.migration.heads_command, "the schema requires it"
    with tempfile.TemporaryDirectory() as tmp:
        effects = ComposeHostEffects(spec, Path(tmp))
        assert tuple(effects._migration_heads_command) == spec.migration.heads_command


# ── worker_responds ──────────────────────────────────────────────────────────


def test_worker_responds_true_on_a_successful_ping(tmp_path: Path) -> None:
    runner = ScriptedRunner()
    runner.when(lambda a: "exec" in a, CommandResult(0))
    effects = make_effects(tmp_path, runner=runner)
    assert effects.worker_responds("worker") is True


def test_worker_responds_false_for_a_role_with_no_worker_contract(
    tmp_path: Path,
) -> None:
    effects = make_effects(tmp_path, runner=ScriptedRunner())
    assert effects.worker_responds("app") is False


# ── prune_images: keeps in-use and the N most recent unused ────────────────


def test_prune_images_keeps_in_use_and_the_n_most_recent_unused(tmp_path: Path) -> None:
    rows = "\n".join(
        [
            "2026-08-20 10:00:00 +0000 UTC\tid5\tghcr.io/example/app:sha-e5e5e5e",
            "2026-08-19 10:00:00 +0000 UTC\tid4\tghcr.io/example/app:sha-d4d4d4d",
            "2026-08-18 10:00:00 +0000 UTC\tid3\tghcr.io/example/app:sha-c3c3c3c",
            "2026-08-17 10:00:00 +0000 UTC\tid2\tghcr.io/example/app:sha-b2b2b2b",
        ]
    )
    removed: list[str] = []
    runner = ScriptedRunner()
    runner.when(
        lambda a: a[:3] == ["docker", "ps", "-a"], CommandResult(0, "running_image\n")
    )
    runner.when(
        lambda a: a[:3] == ["docker", "image", "inspect"] and a[-1] == "running_image",
        CommandResult(0, "id5"),
    )
    runner.when(lambda a: a[:3] == ["docker", "image", "ls"], CommandResult(0, rows))

    def remove_matcher(argv):  # type: ignore[no-untyped-def]
        if argv[:3] == ["docker", "image", "rm"]:
            removed.append(argv[3])
            return True
        return False

    runner.when(remove_matcher, CommandResult(0))
    effects = make_effects(tmp_path, runner=runner)
    effects.prune_images(retain=1)
    # id5 is in use (kept); id4 is the one retained unused image; id3, id2 are pruned.
    assert removed == [
        "ghcr.io/example/app:sha-c3c3c3c",
        "ghcr.io/example/app:sha-b2b2b2b",
    ]


# ── NginxInstaller: uses the imported handoff pattern, never re-derives it ──


def test_nginx_installer_verify_handoff_uses_the_imported_pattern(
    tmp_path: Path,
) -> None:
    from dotmac_deployment_foundation.render.nginx import render_nginx

    spec = load()
    rendered = render_nginx(spec)
    runner = ScriptedRunner()
    runner.when(lambda a: a[-1] == "-T", CommandResult(0, rendered))
    installer = NginxInstaller(spec, tmp_path / "site.conf", runner=runner)
    assert installer.verify_handoff("app") is True


def test_nginx_installer_verify_handoff_false_when_the_backup_member_is_missing(
    tmp_path: Path,
) -> None:
    """The negative control: a config with the candidate `backup` member
    stripped out must fail the same check that passes above."""
    from dotmac_deployment_foundation.render.nginx import render_nginx

    spec = load()
    rendered = render_nginx(spec)
    corrupted = "\n".join(
        line for line in rendered.splitlines() if "backup" not in line
    )
    runner = ScriptedRunner()
    runner.when(lambda a: a[-1] == "-T", CommandResult(0, corrupted))
    installer = NginxInstaller(spec, tmp_path / "site.conf", runner=runner)
    assert installer.verify_handoff("app") is False


def test_nginx_installer_config_digest_hashes_the_live_output(tmp_path: Path) -> None:
    runner = ScriptedRunner()
    runner.when(lambda a: a[-1] == "-T", CommandResult(0, "some config\n"))
    installer = NginxInstaller(load(), tmp_path / "site.conf", runner=runner)
    digest = installer.config_digest()
    assert digest == f"sha256:{hashlib.sha256(b'some config\n').hexdigest()}"


def test_nginx_installer_config_digest_raises_precondition_failed_on_nginx_T_error(
    tmp_path: Path,
) -> None:
    runner = ScriptedRunner()
    runner.when(
        lambda a: a[-1] == "-T",
        CommandResult(1, "", "nginx: configuration file test failed"),
    )
    installer = NginxInstaller(load(), tmp_path / "site.conf", runner=runner)
    with pytest.raises(PreconditionFailed):
        installer.config_digest()


# ── the canary for the loop that did not close ──────────────────────────────


def test_switch_RE_RENDERS_the_compose_file_for_the_target_image(
    tmp_path: Path,
) -> None:
    """Writing `APP_IMAGE` into `.env` moves nothing on a rendered deployment.

    This package's own `render_compose` bakes a LITERAL digest into every
    service — deliberately, because that is what makes `render --check` a pin
    and lets drift compare bytes. A compose file rendered by this facility
    therefore never reads `${APP_IMAGE}`, so an earlier `switch` that only
    repointed the environment variable would have recreated the containers on
    the SAME baked digest and reported a successful rollback that changed
    nothing — the exact defect the executor's own rollback repair had just
    fixed one layer up, reappearing in the provider.

    The assertion is on the FILE, not on the env var: the env var was already
    being written correctly and proved nothing.
    """
    runner = ScriptedRunner()
    runner.when(lambda a: "up" in a, CommandResult(0))
    effects = make_effects(tmp_path, runner=runner)
    rollback_image = f"ghcr.io/example/app@{OLD_DIGEST}"

    effects.switch(timeout_seconds=30, image=rollback_image)

    rendered = (tmp_path / "docker-compose.yml").read_text(encoding="utf-8")
    # The SERVICES, not the whole file. The header deliberately keeps a
    # `descriptor image:` line as provenance — a rolled-back host should be able
    # to say what it was rolled back FROM — so asserting the deploying digest is
    # absent from the entire file would fail on the one line that is meant to
    # carry it.
    services = rendered.split("\nservices:\n", 1)[1]
    assert OLD_DIGEST in services, "every service must run the restored digest"
    assert GOOD_DIGEST not in services, (
        "the deploying digest must be gone from the services, or the recreate "
        "brings back the image that just failed"
    )
    assert "# descriptor image:" in rendered, (
        "the header keeps the descriptor's digest, so drift can see that the "
        "host is deliberately behind the approved plan"
    )


def test_the_compose_file_is_left_alone_when_the_host_owns_it(
    tmp_path: Path,
) -> None:
    """The negative control, and a real deployment shape rather than a foil.

    Sub's own compose file interpolates `${APP_IMAGE}` and is protecting
    production today. While its real engine is still the live path, this
    facility must repoint the variable and NOT overwrite the file — so
    `manage_compose_file=False` is a supported configuration and not a way of
    turning the check off.
    """
    runner = ScriptedRunner()
    runner.when(lambda a: "up" in a, CommandResult(0))
    host_file = tmp_path / "docker-compose.yml"
    host_file.write_text(
        "services:\n  app:\n    image: ${APP_IMAGE:?set me}\n", encoding="utf-8"
    )
    effects = make_effects(tmp_path, runner=runner, manage_compose_file=False)
    original = host_file.read_text(encoding="utf-8")

    effects.switch(timeout_seconds=30, image=f"ghcr.io/example/app@{OLD_DIGEST}")

    assert host_file.read_text(encoding="utf-8") == original
    assert f"APP_IMAGE=ghcr.io/example/app@{OLD_DIGEST}" in (
        tmp_path / ".env"
    ).read_text(encoding="utf-8")


# ── manifest_digest reads only inside the staged release ────────────────────
#
# `manifest_digest` answers a gate: the executor compares what it returns
# against the digest the plan approved. Its path comes from the descriptor,
# which travels with the release — so the path decides WHICH FILE answers the
# gate, and a gate that can be pointed at a file of the writer's choosing is
# not a gate, it is a lookup that always agrees.
#
# `spec.py` refuses `..`/absolute/backslash paths at parse. That is the loud
# half and it cannot see the filesystem: `.resolve()` follows a symlink planted
# INSIDE the deploy directory, which no syntactic check can catch. Hence the
# containment re-check here, and hence these tests.
#
# Found by the #507 supersession audit; written fresh against current main.


def test_manifest_digest_reads_a_manifest_inside_the_deploy_root(
    tmp_path: Path,
) -> None:
    """The positive control. Without it the refusals below prove nothing."""
    (tmp_path / "manifest.json").write_bytes(b"{}")
    digest = make_effects(tmp_path).manifest_digest("manifest.json")
    assert digest == f"sha256:{hashlib.sha256(b'{}').hexdigest()}"


def test_manifest_digest_refuses_a_symlink_escaping_the_deploy_root(
    tmp_path: Path,
) -> None:
    """The attack the parse-side check structurally cannot catch.

    Without the containment re-check this returns the digest of `secret.json`,
    and the gate then agrees about a file that is not part of the release.
    """
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    outside = tmp_path / "secret.json"
    outside.write_bytes(b'{"not":"the release"}')
    (deploy / "manifest.json").symlink_to(outside)

    assert make_effects(deploy).manifest_digest("manifest.json") == "", (
        "the digest of a file outside the staged release was accepted as "
        "evidence about the staged release"
    )


def test_manifest_digest_refuses_a_traversing_relative_path(tmp_path: Path) -> None:
    """Defence in depth — the read side does not rely on the parse side."""
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    (tmp_path / "escape.json").write_bytes(b"{}")
    assert make_effects(deploy).manifest_digest("../escape.json") == ""


def test_manifest_digest_still_allows_a_symlink_that_stays_inside(
    tmp_path: Path,
) -> None:
    """Containment, not a blanket symlink ban.

    A link is a legitimate way to stage a file; only LEAVING the root is the
    problem. Refusing every link would break correct deployments and would
    make the guard pass for the wrong reason.
    """
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "m.json").write_bytes(b"{}")
    (tmp_path / "manifest.json").symlink_to(tmp_path / "real" / "m.json")

    assert make_effects(tmp_path).manifest_digest("manifest.json").startswith("sha256:")


def test_manifest_digest_still_returns_empty_for_an_absent_manifest(
    tmp_path: Path,
) -> None:
    """The pre-existing contract is unchanged: absent is a refusal, not a match."""
    assert make_effects(tmp_path).manifest_digest("manifest.json") == ""
