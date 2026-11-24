"""T12 (spec parse refusals) and T13 (existing descriptors unchanged)."""

from __future__ import annotations

import re
import tomllib

import pytest
from dotmac_deployment_foundation.errors import SpecError
from dotmac_deployment_foundation.render.compose import render_compose
from dotmac_deployment_foundation.spec import ProductDeploymentSpec

from tests.unit.foundation_operator_job_support import (
    DESCRIPTOR,
    JOB_TOML,
    make_document,
    make_spec,
)
from tests.unit.test_deployment_foundation_material_readiness import (
    descriptor as material_readiness_descriptor,
)

PLAIN_JOB = JOB_TOML.split("[compose_topology.jobs.operator]")[0]


def test_operator_table_parses_into_a_frozen_value() -> None:
    job = make_spec().compose_topology.jobs[-1]
    assert job.operator is not None
    assert job.operator.command_for("import_execute") == (
        "python",
        "-m",
        "importer",
        "execute",
    )
    assert job.operator.command_for("nope") is None
    assert job.operator.inputs_target == "/job/in"
    assert job.operator.outputs_target == "/job/out"


def _mutated(old: str, new: str) -> str:
    assert old in JOB_TOML, old
    return JOB_TOML.replace(old, new)


@pytest.mark.parametrize(
    "job_toml",
    [
        pytest.param(
            _mutated(
                'profiles = ["ops"]', 'profiles = ["ops"]\nrun_during_deploy = true'
            ),
            id="run_during_deploy",
        ),
        pytest.param(
            _mutated('image_source = "product"', 'image_source = "dependency:db"'),
            id="image_source",
        ),
        pytest.param(
            _mutated(
                'profiles = ["ops"]',
                'profiles = ["ops"]\nroot_justification = "x"\nroot_approved_by = "y"',
            ),
            id="root_justification",
        ),
        pytest.param(_mutated("entrypoint = []\n", ""), id="entrypoint_unset"),
        pytest.param(
            _mutated('import_execute = ["python", "-m", "importer", "execute"]', ""),
            id="commands_empty",
        ),
        pytest.param(
            _mutated("import_execute =", "Import-Execute ="), id="bad_command_name"
        ),
        pytest.param(
            _mutated(
                '["python", "-m", "importer", "execute"]', '["python", "-m", "x"]'
            ),
            id="command_not_prefixed",
        ),
        pytest.param(
            _mutated(
                'inputs_root_material = "JOB_INPUTS_ROOT"',
                'inputs_root_material = "JOB_INPUTS_ROOT"\ninputs_target = "/in"',
            ),
            id="inputs_target",
        ),
        pytest.param(
            _mutated(
                'inputs_root_material = "JOB_INPUTS_ROOT"',
                'inputs_root_material = "JOB_INPUTS_ROOT"\noutputs_target = "/out"',
            ),
            id="outputs_target",
        ),
        pytest.param(
            _mutated('"/run/secrets/dotmac"', '"run/secrets"'), id="secrets_relative"
        ),
        pytest.param(
            _mutated('"/run/secrets/dotmac"', '"/run/../etc"'), id="secrets_dotdot"
        ),
        pytest.param(_mutated('profiles = ["ops"]', "profiles = []"), id="profiles"),
        pytest.param(
            _mutated('networks = ["back"]', 'network_mode = "none"'), id="network_mode"
        ),
        pytest.param(
            _mutated("timeout_seconds = 2400", "timeout_seconds = 3601"), id="timeout"
        ),
        pytest.param(
            _mutated('inputs_root_material = "JOB_INPUTS_ROOT"', ""),
            id="inputs_root_missing",
        ),
    ],
)
def test_t12_each_parse_refusal(job_toml: str) -> None:
    with pytest.raises(SpecError):
        make_spec(job_toml)


def test_t12_an_unknown_operator_key_is_refused() -> None:
    with pytest.raises(SpecError):
        make_spec(JOB_TOML + "surprise = 1\n")


def test_t13_existing_descriptor_has_no_operator_and_is_unchanged() -> None:
    spec = ProductDeploymentSpec.loads(DESCRIPTOR)
    assert all(job.operator is None for job in spec.compose_topology.jobs)


def test_t13_a_job_without_the_table_defaults_off() -> None:
    spec = ProductDeploymentSpec.loads(DESCRIPTOR + PLAIN_JOB)
    assert spec.compose_topology.jobs[-1].operator is None


def _digest(doc: dict) -> str:
    return (
        ProductDeploymentSpec.from_document(doc).to_canonical_document().sha256_digest()
    )


def _plain_v5() -> ProductDeploymentSpec:
    return make_spec(PLAIN_JOB)


_CONFIG_DIGEST_LINE = re.compile(
    r"^.*io\.dotmac\.deployment\.configuration\.digest:.*$", re.M
)


def test_t13_the_operator_table_changes_the_v5_digest_but_not_the_render_shape() -> (
    None
):
    plain, operator = _plain_v5(), make_spec()
    assert (
        plain.to_canonical_document().canonical_bytes()
        != operator.to_canonical_document().canonical_bytes()
    )
    # The rendered compose bytes differ only in the configuration-digest
    # label, which IS the approved descriptor digest and so must change.
    strip = lambda text: _CONFIG_DIGEST_LINE.sub("", text)  # noqa: E731
    assert strip(render_compose(plain)) == strip(render_compose(operator))
    assert render_compose(plain) != render_compose(operator)


def _older(schema: str, job_toml: str) -> dict:
    """A valid pre-v5 descriptor document with the given job tables appended."""
    base = DESCRIPTOR if schema.endswith("v3") else material_readiness_descriptor()
    doc = tomllib.loads(base)
    assert doc["schema"] == schema
    doc["compose_topology"]["jobs"] = [
        *doc["compose_topology"]["jobs"],
        *tomllib.loads(job_toml)["compose_topology"]["jobs"],
    ]
    return doc


_OLDER = ["ProductDeploymentSpec.v3", "ProductDeploymentSpec.v4"]


@pytest.mark.parametrize("schema", _OLDER)
def test_t13_older_schema_canonical_bytes_never_carry_the_operator_key(
    schema: str,
) -> None:
    older = ProductDeploymentSpec.from_document(_older(schema, PLAIN_JOB))
    assert b'"operator"' not in older.to_canonical_document().canonical_bytes()


def test_t13_v5_materializes_the_operator_key() -> None:
    assert b'"operator"' in _plain_v5().to_canonical_document().canonical_bytes()


@pytest.mark.parametrize("schema", _OLDER)
def test_t13_an_operator_table_on_an_older_schema_is_refused(schema: str) -> None:
    # positive control: the same document without the operator table parses
    ProductDeploymentSpec.from_document(_older(schema, PLAIN_JOB))
    with pytest.raises(SpecError, match="requires ProductDeploymentSpec.v5"):
        ProductDeploymentSpec.from_document(_older(schema, JOB_TOML))


def _operator(doc: dict) -> dict:
    return doc["compose_topology"]["jobs"][-1]["operator"]


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(
            lambda op: op["commands"].update(
                import_execute=["python", "-m", "importer", "execute", "--apply"]
            ),
            id="command_entry_changed",
        ),
        pytest.param(
            lambda op: op["commands"].update(
                import_dryrun=["python", "-m", "importer", "execute"]
            ),
            id="command_entry_added",
        ),
        pytest.param(
            lambda op: op.update(secrets_target="/run/secrets/other"),
            id="secrets_target",
        ),
        pytest.param(
            lambda op: op.update(inputs_root_material="OTHER_INPUTS_ROOT"),
            id="inputs_root_material",
        ),
    ],
)
def test_t13_every_operator_field_is_inside_the_v5_digest(mutate) -> None:
    base = make_document()
    changed = make_document()
    mutate(_operator(changed))
    assert _digest(changed) != _digest(base)
    assert _digest(make_document()) == _digest(base)  # deterministic control
