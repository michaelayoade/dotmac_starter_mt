"""T12 (spec parse refusals) and T13 (existing descriptors unchanged)."""

from __future__ import annotations

import pytest
from dotmac_deployment_foundation.errors import SpecError
from dotmac_deployment_foundation.render.compose import render_compose
from dotmac_deployment_foundation.spec import ProductDeploymentSpec

from tests.unit.foundation_operator_job_support import DESCRIPTOR, JOB_TOML, make_spec

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
        ProductDeploymentSpec.loads(DESCRIPTOR + job_toml)


def test_t12_an_unknown_operator_key_is_refused() -> None:
    with pytest.raises(SpecError):
        ProductDeploymentSpec.loads(DESCRIPTOR + JOB_TOML + "surprise = 1\n")


def test_t13_existing_descriptor_has_no_operator_and_is_unchanged() -> None:
    spec = ProductDeploymentSpec.loads(DESCRIPTOR)
    assert all(job.operator is None for job in spec.compose_topology.jobs)


def test_t13_a_job_without_the_table_defaults_off() -> None:
    spec = ProductDeploymentSpec.loads(DESCRIPTOR + PLAIN_JOB)
    assert spec.compose_topology.jobs[-1].operator is None


def test_t13_the_operator_table_changes_neither_render_nor_canonical_document() -> None:
    plain = ProductDeploymentSpec.loads(DESCRIPTOR + PLAIN_JOB)
    operator = make_spec()
    assert render_compose(plain) == render_compose(operator)
    assert (
        plain.to_canonical_document().canonical_bytes()
        == operator.to_canonical_document().canonical_bytes()
    )
