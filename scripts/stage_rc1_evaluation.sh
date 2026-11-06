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

tar --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner -cf "$destination.tar" .
tar -tf "$destination.tar" | LC_ALL=C sort > "$destination.files"
sha256sum "$destination.tar"
