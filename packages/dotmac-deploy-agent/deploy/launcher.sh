#!/bin/sh
# Installed root-owned release only. Enrolment pins the artefact digest and the
# Sigstore identity in root-owned files; this script verifies, then execs.
# The signed build itself is a separate gate (WP9); nothing here builds one.
set -eu
PATH=/usr/local/bin:/usr/bin:/bin
export PATH
BASE=/opt/dotmac-deploy-agent
RELEASE="$BASE/release"
ETC=/etc/dotmac-deploy-agent
for directory in "$BASE" "$RELEASE" "$ETC"; do
  [ -d "$directory" ] && [ ! -L "$directory" ] || exit 1
  [ "$(stat -c %u "$directory")" = 0 ] || exit 1
  [ -z "$(find "$directory" -maxdepth 0 -perm /022 -print)" ] || exit 1
done
for path in "$RELEASE/agent.pyz" "$RELEASE/agent.bundle.json" "$ETC/agent.sha256" \
  "$ETC/sigstore-identity" "$ETC/sigstore-issuer"; do
  [ -f "$path" ] && [ ! -L "$path" ] || exit 1
  [ "$(stat -c %u "$path")" = 0 ] || exit 1
  [ -z "$(find "$path" -perm /022 -print)" ] || exit 1
done
cd "$RELEASE"
sha256sum --check --strict "$ETC/agent.sha256"
env -i PATH="$PATH" LANG=C.UTF-8 /usr/local/bin/cosign verify-blob \
  --bundle agent.bundle.json \
  --certificate-identity "$(cat "$ETC/sigstore-identity")" \
  --certificate-oidc-issuer "$(cat "$ETC/sigstore-issuer")" \
  agent.pyz >/dev/null
# Nothing is inherited: no DB name, no registry credential, no token.
exec env -i PATH="$PATH" LANG=C.UTF-8 \
  ${CREDENTIALS_DIRECTORY:+CREDENTIALS_DIRECTORY="$CREDENTIALS_DIRECTORY"} \
  /usr/bin/python3 agent.pyz "$@"
