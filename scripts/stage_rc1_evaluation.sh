#!/usr/bin/env bash
set -euo pipefail

destination=$1
mkdir "$destination"
git archive 5406b7381118be73f05ac818db0a2e5e5b10b9ba | tar -xf - -C "$destination"
mkdir -p "$destination/deploy/rc1-handoff/scripts"
for file in .env.example docker-compose.yml database-roles.sql scripts/create_platform_admin.py; do
  cp "deploy/rc1-handoff/$file" "$destination/deploy/rc1-handoff/$file"
done

cd "$destination"
sed -i '/"app.features.custom_fields",/d' app/features/__init__.py
sed -i '/^from app.features.custom_fields import (/,/^)/d' alembic/env.py
sed -i -e 's/down_revision = "a001_adopt_cfd"/down_revision = None/' \
  -e 's/branch_labels = None/branch_labels = ("assembly",)/' \
  -e 's/depends_on = None/depends_on = "0007_platform_identity"/' \
  alembic/versions/a002_tenant_applied_licences.py
sed -i 's/("custom_fields.use", "template_studio.use")/("template_studio.use",)/' \
  alembic/versions/a004_backfill_capability_grants.py
sed -i 's/{"auth", "audit", "custom_fields"}/{"auth", "audit"}/' app/features/settings/feature.py
sed -i "/{% if 'custom_fields' in enabled_features %}/,/{% endif %}/d" \
  packages/dotmac-kernel/src/dotmac_kernel/templates/admin/parties/detail.html
rm -r app/features/custom_fields \
  packages/dotmac-kernel/src/dotmac_kernel/templates/admin/custom_fields
rm alembic/versions/a001_adopt_custom_field_definitions.py

# Machine credential storage remains part of the kernel migration lineage.
# The evaluation archive omits the authentication and rotation implementation.
kernel=packages/dotmac-kernel/src/dotmac_kernel
python3 - "$kernel" <<'PY'
import ast
import json
import sys
from pathlib import Path

kernel = Path(sys.argv[1])
modules = {"dotmac_kernel.machine_auth", "dotmac_kernel.machine_rotation"}
init = kernel / "__init__.py"
source = init.read_text()
imports = [
    node for node in ast.parse(source).body
    if isinstance(node, ast.ImportFrom) and node.module in modules
]
assert {node.module for node in imports} == modules
symbols = {alias.asname or alias.name for node in imports for alias in node.names}
removed_lines = {
    line for node in imports for line in range(node.lineno, node.end_lineno + 1)
}
init.write_text("".join(
    line for number, line in enumerate(source.splitlines(keepends=True), 1)
    if number not in removed_lines
    and line.strip().rstrip(",").strip('"') not in symbols | modules
))
exports = kernel / "public_exports.json"
contract = json.loads(exports.read_text())
for module in modules:
    del contract["modules"][module]
    (kernel / (module.rsplit(".", 1)[1] + ".py")).unlink()
exports.write_text(json.dumps(contract, indent=2) + "\n")
PY

# These optional presentation slices have no callers in the RC1 assembly.
# Keep the empty-state component pending its separate provenance review.
python3 - <<'PY'
import ast
from pathlib import Path

ui = Path("packages/dotmac-ui/src/dotmac_ui")
excluded = {
    "MAP_FRAME", "DASHBOARD_GRID", "DASHBOARD_PANEL", "METRIC_CARD",
    "NOTICE_BANNER", "DASHBOARD_LINE_CHART", "NOTIFICATION_TOAST",
}
components = ui / "components.py"
source = components.read_text()
removed = set()
for node in ast.parse(source).body:
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        if node.target.id in excluded:
            removed.update(range(node.lineno, node.end_lineno + 1))
for path in (components, ui / "__init__.py"):
    text = path.read_text()
    path.write_text("".join(
        line for number, line in enumerate(text.splitlines(keepends=True), 1)
        if (path != components or number not in removed)
        and line.strip().rstrip(",").strip('"') not in excluded
    ))
for name in excluded:
    (ui / "templates/dotmac_ui/components" / (name.lower() + ".html")).unlink()
PY

# Distribute only the package set the reference runtime actually composes.
# Resolve the smaller dependency graph in a hosted container before archiving
# so the archive carries the exact lockfile used by its Docker build.
python3 - <<'PY'
import json
import re
import shutil
import tomllib
from pathlib import Path

root = Path.cwd().resolve()
manifest = json.loads(Path("deploy/runtime-packages.json").read_text())
selected = {value.split("[", 1)[0] for value in manifest["distributions"]}
project = Path("pyproject.toml")
text = project.read_text()
dependencies = tomllib.loads(text)["tool"]["poetry"]["dependencies"]
omitted = {
    name: spec["path"] for name, spec in dependencies.items()
    if isinstance(spec, dict) and "path" in spec and name not in selected
}
assert len(omitted) == 97
for name, relative in omitted.items():
    pattern = rf"(?m)^{re.escape(name)}\s*=\s*\{{[^\n]*\}}\n"
    text, count = re.subn(pattern, "", text)
    assert count == 1, name
    path = (root / relative).resolve()
    assert path.is_relative_to(root / "packages")
    shutil.rmtree(path)
project.write_text(text)
# The published RC1 lock belongs to the full source tree. Generate a new
# evaluation lock instead of presenting that lock as valid for this subset.
Path("poetry.lock").unlink()
PY
docker run --rm --volume "$destination:/work" --workdir /work \
  python:3.12-slim-bookworm sh -ec '
    python -m pip install --disable-pip-version-check --no-cache-dir \
      --require-hashes --only-binary=:all: -r .github/bootstrap/poetry-requirements-py312.txt
    poetry lock --no-interaction
  '
tar --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner -cf "$destination.tar" .
tar -tf "$destination.tar" | LC_ALL=C sort > "$destination.files"
sha256sum "$destination.tar"
