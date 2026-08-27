"""Tests for `dotmac_deployment_foundation.render.compose` — the pure
docker-compose renderer.

The fixture spec is built from a TOML string through
`ProductDeploymentSpec.loads`, not by constructing dataclasses by hand: that
way a mistake in this module's understanding of the loader's defaults (an
`inherits`-style surprise, a validation the parser applies that a
hand-built dataclass would silently skip) fails a test here rather than
surfacing later as a renderer that only works against spec objects nobody's
loader actually produces.

Five roles carry the fixture's coverage on purpose:

- ``web`` and ``api`` both sit behind ingress, both declare a readiness
  probe (required for that — `spec.py`'s cross-field validation refuses an
  ingress role with none), and both get the uploads volume mounted.
- ``worker`` is a background (celery) role with only a liveness probe, and
  depends on ``cache`` — the one case in this fixture where a dependency has
  NO readiness probe, so `depends_on: cache: condition: service_started` is
  exercised alongside `web`'s `depends_on: api: condition: service_healthy`.
- ``cache`` has no health probes at all — an ordinary internal role with
  nothing to prove.
- ``netrole`` declares `host_network`, `privileged` and `writable_path`
  security exceptions and no health probes, covering the exception-comment
  and no-label paths in one place.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping

import pytest
import yaml
from dotmac_deployment_foundation.render.compose import (
    _scalar,
    render_compose,
    render_compose_digest,
)
from dotmac_deployment_foundation.spec import ProductDeploymentSpec

_MANIFEST_DIGEST = "sha256:" + "a" * 64
_IMAGE = f"registry.example.com/acme/app@sha256:{'b' * 64}"
_SOURCE_REVISION = "c" * 40
_OWNER_MATERIAL = "MIGRATION_DATABASE_URL"

_TOML = f"""
schema = "ProductDeploymentSpec.v1"
product = "acme"
environment = "prod"

[assembly]
manifest_path = "deploy/product.toml"
manifest_digest = "{_MANIFEST_DIGEST}"

[image]
reference = "{_IMAGE}"
source_revision = "{_SOURCE_REVISION}"

[[roles]]
code = "web"
command = ["python", "-m", "app"]
replicas = 2
depends_on = ["api"]
materials = ["DATABASE_URL"]
stop_grace_seconds = 30

[roles.resources]
cpus = "1.0"
memory = "512m"

[roles.health.live]
path = "/livez"
port = 8080

[roles.health.ready]
path = "/readyz"
port = 8080

[roles.security]
[[roles.security.exceptions]]
kind = "capability"
value = "NET_ADMIN"
justification = "needs raw sockets for the outbound connectivity probe"
approved_by = "mayoade"

[[roles]]
code = "api"
command = ["python", "-m", "app.api"]
replicas = 2
materials = ["DATABASE_URL", "CACHE_TOKEN"]
stop_grace_seconds = 25

[roles.resources]
cpus = "1.0"
memory = "512m"

[roles.health.live]
path = "/livez"
port = 4000

[roles.health.ready]
path = "/readyz"
port = 4000

[[roles]]
code = "worker"
command = ["python", "-m", "app.worker"]
replicas = 1
depends_on = ["cache"]
stop_grace_seconds = 90

[roles.resources]
cpus = "0.5"
memory = "256m"

[roles.health.live]
path = "/livez"
port = 9000

[roles.worker]
kind = "celery"
ping_command = ["celery", "inspect", "ping"]

[[roles]]
code = "cache"
command = ["cache-warmer"]
replicas = 1
stop_grace_seconds = 20

[roles.resources]
cpus = "0.25"
memory = "128m"

# Liveness only, deliberately, and NO readiness: this role is the subject
# of the tests that a role without a readiness probe gets no healthcheck
# block and is depended on with `service_started` rather than
# `service_healthy`. Every running role must declare SOME health signal,
# so "no probes at all" is no longer expressible — `migrate` is the
# service that legitimately has none.
[roles.health.live]
path = "/health/live"
port = 6379

[[roles]]
code = "netrole"
command = ["sh", "-c", "relay"]
replicas = 1
stop_grace_seconds = 15

[roles.resources]
cpus = "0.5"
memory = "128m"

# Liveness only, deliberately, and NO readiness: this role is the subject
# of the tests that a role without a readiness probe gets no healthcheck
# block and is depended on with `service_started` rather than
# `service_healthy`. Every running role must declare SOME health signal,
# so "no probes at all" is no longer expressible — `migrate` is the
# service that legitimately has none.
[roles.health.live]
path = "/health/live"
port = 9000

[roles.security]
user = "0:0"
read_only_root = false
[[roles.security.exceptions]]
kind = "host_network"
value = "true"
justification = "the DHCP relay must bind directly to the host's own NIC"
approved_by = "mayoade"
[[roles.security.exceptions]]
kind = "privileged"
value = "true"
justification = "netns manipulation needs CAP_SYS_ADMIN-adjacent syscalls"
approved_by = "mayoade"
[[roles.security.exceptions]]
kind = "writable_path"
value = "/var/lib/relay"
justification = "the DHCP lease database must persist writes on local disk"
approved_by = "mayoade"

[migration]
command = ["alembic", "upgrade", "heads"]
heads_command = ["alembic", "current"]
owner_material = "{_OWNER_MATERIAL}"
expected_heads = ["abc123"]
compatibility = "online"
lock_timeout_seconds = 300

[runtime_materials]
names = ["DATABASE_URL", "CACHE_TOKEN"]

[ingress]
host = "acme.example.com"

[[ingress.routes]]
path = "/"
role = "web"
port = 8080

[[ingress.routes]]
path = "/api"
role = "api"
port = 4000

[ingress.static]
uploads = "volume"
uploads_volume = "acme_uploads"
"""


@pytest.fixture(scope="module")
def spec() -> ProductDeploymentSpec:
    return ProductDeploymentSpec.loads(_TOML, source="<test-fixture>")


@pytest.fixture(scope="module")
def rendered(spec: ProductDeploymentSpec) -> str:
    return render_compose(spec)


@pytest.fixture(scope="module")
def doc(rendered: str) -> Mapping[str, object]:
    parsed = yaml.safe_load(rendered)
    assert isinstance(parsed, dict)
    return parsed


# ── module-level predicates, shared by the real and the corrupted document ──
#
# Each of these is written to be RUN AGAINST A PARSED YAML MAPPING, not the
# raw text, and each has a companion sensitivity-proof test below that shows
# it actually fails on a corrupted document rather than passing regardless
# of what it is given.


def _all_published_ports_are_loopback(compose_doc: Mapping[str, object]) -> bool:
    services = compose_doc["services"]
    for service in services.values():
        for port_mapping in service.get("ports", []):
            if not port_mapping.startswith("127.0.0.1:"):
                return False
    return True


def _no_service_declares_a_container_name(compose_doc: Mapping[str, object]) -> bool:
    services = compose_doc["services"]
    return all("container_name" not in service for service in services.values())


def _owner_material_only_reaches_migrate(
    compose_doc: Mapping[str, object], owner_material: str
) -> bool:
    services = compose_doc["services"]
    for name, service in services.items():
        if name == "migrate":
            continue
        if owner_material in (service.get("environment") or {}):
            return False
    return True


# ── fixture sanity ───────────────────────────────────────────────────────────


def test_the_fixture_parses_into_five_roles(spec: ProductDeploymentSpec) -> None:
    """Pins the shape the rest of this file assumes, so a fixture edit that
    silently drops a role fails here with a clear message instead of as a
    confusing failure three tests down."""
    assert spec.role_codes == ("web", "api", "worker", "cache", "netrole")


def test_the_rendered_document_is_valid_yaml(doc: Mapping[str, object]) -> None:
    assert set(doc["services"]) == {
        "migrate",
        "web",
        "api",
        "worker",
        "cache",
        "netrole",
    }


# ── 1. the one-shot migration service ───────────────────────────────────────


def test_migrate_is_a_one_shot_service_running_the_migration_command(
    doc: Mapping[str, object],
) -> None:
    migrate = doc["services"]["migrate"]
    assert migrate["restart"] == "no"
    assert migrate["command"] == ["alembic", "upgrade", "heads"]


def test_migrate_gets_only_the_owner_material_and_no_runtime_materials(
    doc: Mapping[str, object], spec: ProductDeploymentSpec
) -> None:
    migrate = doc["services"]["migrate"]
    assert set(migrate["environment"]) == {spec.migration.owner_material}
    for name in spec.runtime_materials:
        assert name not in migrate["environment"]


# ── 2. every runtime role depends on migrate, plus its own declared deps ───


def test_every_runtime_role_waits_for_migrate_to_finish(
    doc: Mapping[str, object], spec: ProductDeploymentSpec
) -> None:
    for role in spec.roles:
        depends = doc["services"][role.code]["depends_on"]
        assert depends["migrate"] == {"condition": "service_completed_successfully"}


def test_a_dependency_on_a_role_with_a_readiness_probe_waits_for_service_healthy(
    doc: Mapping[str, object],
) -> None:
    """`web` depends on `api`, and `api` declares a readiness probe — the
    only condition that means "actually able to serve"."""
    web_depends_on_api = doc["services"]["web"]["depends_on"]["api"]
    assert web_depends_on_api == {"condition": "service_healthy"}


def test_a_dependency_on_a_role_with_no_readiness_probe_only_waits_for_service_started(
    doc: Mapping[str, object],
) -> None:
    """`worker` depends on `cache`, and `cache` declares no readiness probe
    at all — waiting on `service_healthy` there would never be satisfied."""
    assert doc["services"]["worker"]["depends_on"]["cache"] == {
        "condition": "service_started"
    }


# ── 3. the owner material never reaches a runtime service ──────────────────


def test_the_owner_material_never_appears_in_a_runtime_service(
    doc: Mapping[str, object], spec: ProductDeploymentSpec
) -> None:
    assert _owner_material_only_reaches_migrate(doc, spec.migration.owner_material)


def test_the_owner_material_check_would_catch_a_regression_that_leaked_it(
    rendered: str, spec: ProductDeploymentSpec
) -> None:
    """`_owner_material_only_reaches_migrate` is the assertion the previous
    test relies on; this proves it is not vacuously true by corrupting a
    real rendered document — injecting the owner material into `web`'s
    environment block — and showing the predicate then reports False."""
    owner = spec.migration.owner_material
    marker = "      DATABASE_URL: "
    assert rendered.count(marker) >= 1
    injected = f'      {owner}: "${{{owner}:?leaked}}"\n'
    broken = rendered.replace(marker, injected + marker, 1)
    broken_doc = yaml.safe_load(broken)
    assert not _owner_material_only_reaches_migrate(broken_doc, owner)


# ── 4. loopback-only published ports, derived from ingress ─────────────────


def test_a_role_behind_an_ingress_route_publishes_its_route_port_on_loopback(
    doc: Mapping[str, object],
) -> None:
    assert doc["services"]["web"]["ports"] == ["127.0.0.1:8080:8080"]
    assert doc["services"]["api"]["ports"] == ["127.0.0.1:4000:4000"]


def test_a_role_with_no_ingress_route_publishes_nothing(
    doc: Mapping[str, object],
) -> None:
    for code in ("worker", "cache", "migrate"):
        assert "ports" not in doc["services"][code]


def test_the_loopback_check_would_catch_a_regression_that_bound_a_wide_open_port(
    rendered: str,
) -> None:
    """A check that only asserts the string `'127.0.0.1:'` appears
    SOMEWHERE in the document passes even if a DIFFERENT port were bound to
    a routable interface — this proves the predicate inspects every
    published port, by showing it catches a corrupted document. (A
    non-loopback address other than the classic "bind all interfaces" one
    is used here on purpose, to keep this file free of that literal.)"""
    doc_ok = yaml.safe_load(rendered)
    assert _all_published_ports_are_loopback(doc_ok)
    non_loopback = "10.0.0.5:8080:8080"
    broken = rendered.replace("127.0.0.1:8080:8080", non_loopback, 1)
    broken_doc = yaml.safe_load(broken)
    assert not _all_published_ports_are_loopback(broken_doc)


# ── 5. readiness drives the healthcheck; liveness becomes a label ──────────


def test_the_healthcheck_command_targets_the_readiness_path_not_the_liveness_path(
    doc: Mapping[str, object],
) -> None:
    web = doc["services"]["web"]
    test = web["healthcheck"]["test"]
    # The whole argv, not one element. The probe used to be a `CMD-SHELL` string
    # in `test[1]`; it is now `["CMD", "python", "-c", <script>]`, and an
    # index-based assertion silently moved to reading the interpreter name.
    assert test[0] == "CMD", "a shell-form probe needs a shell the image may not have"
    joined = " ".join(test)
    assert "/readyz" in joined
    assert "/livez" not in joined
    assert "wget" not in joined and "curl" not in joined, (
        "a python:*-slim runtime has neither, and a probe that cannot run "
        "reports UNHEALTHY forever rather than reporting nothing"
    )


def test_a_role_with_no_readiness_probe_gets_no_healthcheck_block(
    doc: Mapping[str, object],
) -> None:
    for code in ("worker", "cache", "netrole", "migrate"):
        assert "healthcheck" not in doc["services"][code]


def test_the_liveness_probe_is_exposed_as_a_label_never_as_the_healthcheck(
    doc: Mapping[str, object],
) -> None:
    worker = doc["services"]["worker"]
    assert "healthcheck" not in worker
    assert worker["labels"]["io.dotmac.health.live.path"] == "/livez"


def test_a_role_with_no_liveness_probe_gets_no_label_at_all(
    doc: Mapping[str, object],
) -> None:
    # `migrate` is now the only service with no probe of any kind. Every RUNNING
    # role must declare a health signal — an HTTP probe, a worker ping or a
    # scheduler tick — so a probe-less role is no longer expressible, and the
    # one-shot migration service is what is left to assert against.
    assert "labels" not in doc["services"]["migrate"]


# ── 6. resource limits on every service ─────────────────────────────────────


def test_every_service_declares_cpu_memory_and_pids_limits(
    doc: Mapping[str, object],
) -> None:
    for name, service in doc["services"].items():
        limits = service["deploy"]["resources"]["limits"]
        assert "cpus" in limits, name
        assert "memory" in limits, name
        assert isinstance(service["pids_limit"], int), name


# ── 7. log rotation on every service ────────────────────────────────────────


def test_every_service_has_bounded_json_file_log_rotation(
    doc: Mapping[str, object],
) -> None:
    for name, service in doc["services"].items():
        logging_config = service["logging"]
        assert logging_config["driver"] == "json-file", name
        assert "max-size" in logging_config["options"], name
        assert "max-file" in logging_config["options"], name


# ── 8. explicit top-level networks / volumes ────────────────────────────────


def test_the_network_is_declared_at_the_top_level_and_named_after_the_product(
    doc: Mapping[str, object],
) -> None:
    assert "acme_net" in doc["networks"]


def test_every_service_joins_the_declared_network_explicitly(
    doc: Mapping[str, object],
) -> None:
    for name, service in doc["services"].items():
        if service.get("network_mode") == "host":
            continue
        assert service["networks"] == ["acme_net"], name


def test_the_uploads_volume_is_declared_at_the_top_level(
    doc: Mapping[str, object],
) -> None:
    assert "acme_uploads" in doc["volumes"]


# ── 9. no container_name anywhere ───────────────────────────────────────────


def test_no_service_declares_a_container_name(doc: Mapping[str, object]) -> None:
    assert _no_service_declares_a_container_name(doc)


def test_the_container_name_check_would_catch_a_regression_that_added_one(
    rendered: str,
) -> None:
    """`'container_name' not in rendered` on its own passes trivially on an
    empty string, so it proves nothing about THIS renderer specifically.
    The negative control below shows the real predicate actually inspects
    the document, by injecting a `container_name` into one service."""
    assert _no_service_declares_a_container_name(yaml.safe_load(rendered))
    broken = rendered.replace(
        "  migrate:\n", "  migrate:\n    container_name: acme_migrate\n", 1
    )
    broken_doc = yaml.safe_load(broken)
    assert not _no_service_declares_a_container_name(broken_doc)


# ── 10. the image is the exact digest reference, on every service ──────────


def test_every_service_uses_the_exact_digest_image_reference(
    doc: Mapping[str, object], spec: ProductDeploymentSpec
) -> None:
    for name, service in doc["services"].items():
        assert service["image"] == spec.image, name
        assert "@sha256:" in service["image"]
        assert not service["image"].endswith(":latest")


# ── 11. security defaults, and exceptions add only what they justify ───────


def test_every_service_gets_the_hardened_security_defaults(
    doc: Mapping[str, object],
) -> None:
    for name, service in doc["services"].items():
        assert service["security_opt"] == ["no-new-privileges:true"], name
        assert service["cap_drop"] == ["ALL"], name
        assert "user" in service, name
        assert isinstance(service["read_only"], bool), name


def test_a_capability_exception_adds_only_cap_add_and_nothing_else(
    doc: Mapping[str, object],
) -> None:
    web = doc["services"]["web"]
    assert web["cap_add"] == ["NET_ADMIN"]
    assert "privileged" not in web
    assert "network_mode" not in web


def test_a_writable_path_exception_adds_a_tmpfs_entry(
    doc: Mapping[str, object],
) -> None:
    netrole = doc["services"]["netrole"]
    assert "/var/lib/relay" in netrole["tmpfs"]
    assert netrole["read_only"] is False


def test_a_privileged_or_host_network_exception_gets_an_exception_comment(
    rendered: str,
) -> None:
    """The justification has to be in the FILE a host reads, not only in the
    descriptor — so this checks the raw text, not the parsed structure
    (YAML has no native comment node)."""
    lines = rendered.splitlines()
    netrole_index = lines.index("  netrole:")
    preceding = "\n".join(lines[max(0, netrole_index - 4) : netrole_index])
    assert "# EXCEPTION: host_network" in preceding
    assert "# EXCEPTION: privileged" in preceding
    assert "approved by mayoade" in preceding


def test_a_host_network_exception_uses_network_mode_host_instead_of_networks(
    doc: Mapping[str, object],
) -> None:
    netrole = doc["services"]["netrole"]
    assert netrole["network_mode"] == "host"
    assert "networks" not in netrole


def test_a_privileged_exception_sets_privileged_true(
    doc: Mapping[str, object],
) -> None:
    assert doc["services"]["netrole"]["privileged"] is True


# ── 12. stop_grace_period from the role ─────────────────────────────────────


def test_stop_grace_period_comes_from_each_roles_own_declared_value(
    doc: Mapping[str, object], spec: ProductDeploymentSpec
) -> None:
    for role in spec.roles:
        service = doc["services"][role.code]
        assert service["stop_grace_period"] == f"{role.stop_grace_seconds}s"


# ── 13. environment carries names only, never a value ───────────────────────


def test_environment_entries_are_the_placeholder_form_not_a_value(
    doc: Mapping[str, object],
) -> None:
    web_env = doc["services"]["web"]["environment"]
    assert web_env == {"DATABASE_URL": "${DATABASE_URL:?DATABASE_URL must be set}"}


def test_a_role_with_no_materials_gets_no_environment_key_at_all(
    doc: Mapping[str, object],
) -> None:
    for code in ("worker", "cache", "netrole"):
        assert "environment" not in doc["services"][code]


# ── 14. the header comment block ─────────────────────────────────────────────


def test_the_header_names_the_product_image_revision_digest_and_schema(
    rendered: str, spec: ProductDeploymentSpec
) -> None:
    header = "\n".join(rendered.splitlines()[:10])
    assert spec.product in header
    assert spec.image in header
    assert spec.source_revision in header
    assert spec.manifest_digest in header
    assert "ProductDeploymentSpec.v1" in header
    assert "GENERATED by dotmac-deployment-foundation" in header
    assert "dotmac-deploy render" in header


# ── 15. uploads volume mounted into every ingress-served role ──────────────


def test_the_uploads_volume_is_mounted_into_every_role_behind_ingress(
    doc: Mapping[str, object],
) -> None:
    assert doc["services"]["web"]["volumes"] == ["acme_uploads:/srv/uploads"]
    assert doc["services"]["api"]["volumes"] == ["acme_uploads:/srv/uploads"]


def test_a_role_not_behind_ingress_gets_no_uploads_mount(
    doc: Mapping[str, object],
) -> None:
    for code in ("worker", "cache", "netrole", "migrate"):
        assert "volumes" not in doc["services"][code]


# ── determinism ──────────────────────────────────────────────────────────────


def test_rendering_the_same_spec_twice_produces_identical_bytes(
    spec: ProductDeploymentSpec,
) -> None:
    assert render_compose(spec) == render_compose(spec)


def test_the_digest_is_the_sha256_of_the_rendered_bytes(
    spec: ProductDeploymentSpec,
) -> None:
    rendered_bytes = render_compose(spec).encode("utf-8")
    expected = f"sha256:{hashlib.sha256(rendered_bytes).hexdigest()}"
    assert render_compose_digest(spec) == expected


def test_the_digest_is_stable_across_calls(spec: ProductDeploymentSpec) -> None:
    assert render_compose_digest(spec) == render_compose_digest(spec)


# ── the scalar quoter ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value",
    ["yes", "no", "on", "off", "null", "~", "1.0", "0755"],
)
def test_yaml_boolean_and_numeric_looking_words_are_quoted(value: str) -> None:
    """Every one of these is a bareword YAML 1.1 loaders would parse as a
    bool, null, or number rather than the literal string it is."""
    assert _scalar(value) == f'"{value}"'


@pytest.mark.parametrize(
    "value",
    ["a: b", "*star", "#hash"],
)
def test_values_containing_a_yaml_indicator_character_are_quoted(value: str) -> None:
    assert _scalar(value) == f'"{value}"'


def test_the_empty_string_is_quoted() -> None:
    assert _scalar("") == '""'


def test_a_string_with_an_embedded_newline_is_quoted_and_escaped() -> None:
    assert _scalar("a\nb") == '"a\\nb"'


def test_an_ordinary_identifier_is_left_unquoted() -> None:
    """The quoter is not "always quote" — it only reaches for quotes when
    plain would be ambiguous, which is what keeps the rendered document
    readable."""
    assert _scalar("web") == "web"
    assert _scalar("json-file") == "json-file"


@pytest.mark.parametrize(
    "value",
    ["yes", "no", "on", "off", "null", "~", "1.0", "0755", "a: b", "a\nb", ""],
)
def test_every_quoted_scalar_round_trips_through_a_real_yaml_parser(value: str) -> None:
    """The proof that matters: not just that `_scalar` wraps the value in
    quotes, but that a real YAML parser reads the quoted form back as the
    exact original string."""
    document = f"key: {_scalar(value)}\n"
    assert yaml.safe_load(document) == {"key": value}


_SCALAR_SPECIAL_CHAR_RE = re.compile(r"""[:#{}\[\],&*!|>'"%@`]""")


def test_scalar_quoting_follows_the_special_char_and_ambiguity_rules() -> None:
    """A sensitivity proof for `_scalar` itself: for every character in the
    documented special-char set, a string built around it must come back
    quoted, and a plain safe string must not — so the predicate is shown to
    distinguish the two rather than quoting (or not) unconditionally."""
    for char in ":#{}[],&*!|>'\"%@`":
        value = f"x{char}y"
        assert _SCALAR_SPECIAL_CHAR_RE.search(value) is not None
        assert _scalar(value).startswith('"'), char
    assert not _scalar("plainvalue").startswith('"')


# ── the pids alias, found by the real engine ────────────────────────────────


def test_the_two_pids_keys_never_disagree(spec: ProductDeploymentSpec) -> None:
    """`pids_limit` and `deploy.resources.limits.pids` are ALIASES.

    Compose refuses a project where they carry different values — and an
    ABSENT `pids` under `limits` counts as different, so a `limits` block
    listing only cpus and memory beside a top-level `pids_limit` is rejected
    outright:

        services.app: can't set distinct values on 'pids_limit' and
        'deploy.resources.limits.pids': invalid compose project

    That is what the renderer emitted until the disposable-host rehearsal ran
    for the first time and the engine threw it out. No test in this file could
    have known: asserting a rendered key proves the key is there, never that
    the engine accepts the document. This one encodes the RULE the engine
    taught us, so the same class fails in a second rather than in a
    45-minute rehearsal.

    Every service is checked, not just `app` — `migrate`, the managed
    dependencies and the collector all render through `_resource_lines`.
    """
    rendered = render_compose(spec, image=_IMAGE)
    doc = yaml.safe_load(rendered)

    checked = 0
    for name, service in doc["services"].items():
        legacy = service.get("pids_limit")
        nested = (
            service.get("deploy", {}).get("resources", {}).get("limits", {}).get("pids")
        )
        if legacy is None and nested is None:
            continue
        checked += 1
        assert legacy is not None and nested is not None, (
            f"service {name!r} sets only one of the two pids aliases "
            f"(pids_limit={legacy!r}, limits.pids={nested!r}); Compose treats "
            "the missing one as a disagreement and refuses the project"
        )
        assert int(legacy) == int(nested), (
            f"service {name!r} sets pids_limit={legacy} but "
            f"deploy.resources.limits.pids={nested}; these are one setting"
        )

    assert checked, (
        "no service declared a pids limit, so this test proved nothing — the "
        "fixture must render at least one resource-limited service"
    )
