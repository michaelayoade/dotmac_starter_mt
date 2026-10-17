"""Hosted-only capture from the immutable PRECHANGE Foundation checkout.

This script is intentionally not executed on the workstation. It runs a fresh
interpreter rooted exclusively at a clean immutable baseline checkout. Changed
implementation bytes cannot be used as the historical oracle.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

BASELINE = "1750e2776d5f2374892761763067723ff27595f5"

# Imported only by the separate hosted interpreter, never this generator's
# checkout. This payload performs pure parsing/rendering/serialization only.
CAPTURE = r"""
import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path

import dotmac_deployment_foundation as foundation
from dotmac_deployment_foundation.deployment_evidence import RunStanding
from dotmac_deployment_foundation.engine.plan import build_plan
from dotmac_deployment_foundation.engine.run import DeploymentOutcome
from dotmac_deployment_foundation.execution_plan import (
    HostPrestateV1,
    render_execution_plan,
)
from dotmac_deployment_foundation.render.compose import render_compose
from dotmac_deployment_foundation.spec import SCHEMA_V2, ProductDeploymentSpec

from tests.unit.test_deployment_foundation_compose_host import DESCRIPTOR as v1
from tests.unit.test_deployment_foundation_compose_topology import DESCRIPTOR as v3
from tests.unit.test_deployment_foundation_database_drift import (
    _descriptor_text_with_catalog,
)
from tests.unit.test_deployment_foundation_material_readiness import descriptor as v4

baseline, destination, checkout = sys.argv[1:]
if not Path(foundation.__file__).resolve().is_relative_to(Path(checkout)):
    raise SystemExit("Foundation imports escaped the immutable baseline checkout")
root = Path(destination)
if root.exists():
    raise SystemExit("golden destination must be absent; never overwrite an oracle")
root.mkdir(parents=True)
manifest = {
    "schema": "FoundationLegacyGoldenFixtures.v1",
    "baseline_commit": baseline,
    "files": [],
}
for version, source in (
    ("v1", v1),
    ("v2", _descriptor_text_with_catalog(SCHEMA_V2)),
    ("v3", v3),
    ("v4", v4()),
):
    spec = ProductDeploymentSpec.loads(source)
    canonical = spec.to_canonical_document()
    plan = build_plan(spec)
    execution = render_execution_plan(
        spec,
        plan,
        target="legacy-golden-target",
        operation="deploy",
        descriptor_digest=canonical.sha256_digest(),
        prestate=HostPrestateV1.first_deploy(),
        application_profile_digest="",
    )
    outcome = DeploymentOutcome(
        plan=plan,
        succeeded=True,
        mutated=True,
        descriptor_digest=canonical.sha256_digest(),
        execution_plan_digest=execution.digest(),
        control_plan_digest="c" * 64,
        execution_sequence=1,
        attempt_no=1,
        operation="deploy",
        standing=RunStanding.SUCCEEDED,
    )
    encode = lambda value: json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    artifacts = {
        "descriptor.toml": source.encode(),
        "canonical.json": canonical.canonical_bytes(),
        "compose.yml": render_compose(spec).encode(),
        "execution-plan.json": execution.canonical_bytes(),
        "evidence-v1.json": encode(outcome.as_evidence()),
        "evidence-v2.json": encode(
            replace(
                outcome,
                control_consumption_ref="control-dispatch:legacy-golden-dispatch",
            ).as_evidence()
        ),
    }
    for name, content in artifacts.items():
        relative = version + "/" + name
        path = root / relative
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(content)
        manifest["files"].append(
            {
                "path": relative,
                "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
            }
        )
(root / "manifest.json").write_text(
    json.dumps(manifest, sort_keys=True, indent=2) + "\n"
)
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-checkout", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--git-bin", type=Path, default=Path("/usr/bin/git"))
    args = parser.parse_args()
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("legacy fixture capture is restricted to Git-hosted CI")
    if not args.git_bin.is_absolute():
        raise SystemExit("git binary must be absolute")
    root = args.baseline_checkout.resolve(strict=True)
    head = subprocess.check_output(  # noqa: S603 -- absolute git, read-only argv
        [str(args.git_bin), "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()
    status = subprocess.check_output(  # noqa: S603 -- absolute git, read-only argv
        [
            str(args.git_bin),
            "-C",
            str(root),
            "status",
            "--porcelain",
            "--untracked-files=all",
        ],
        text=True,
    )
    if head != BASELINE or status:
        raise SystemExit("baseline checkout must be the exact clean immutable revision")
    output = args.output_dir.resolve()
    if output.is_relative_to(root):
        raise SystemExit("write fixtures outside the immutable baseline checkout")
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(root / "packages/dotmac-deployment-foundation/src"), str(root)]
    )
    subprocess.run(  # noqa: S603 -- fixed interpreter, fixed payload, verified source
        [sys.executable, "-c", CAPTURE, BASELINE, str(output), str(root)],
        cwd=root,
        env=environment,
        check=True,
    )


if __name__ == "__main__":
    main()
