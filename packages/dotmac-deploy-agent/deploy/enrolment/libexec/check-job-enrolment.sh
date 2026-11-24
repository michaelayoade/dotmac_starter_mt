#!/bin/sh
# Enrolment read-back for ONE job render unit (run as root at enrolment, and by CI
# against a fake systemctl). Usage: check-job-enrolment.sh <ns> <role> <name>...
# <name> are the material names of the set (any case). Fails (non-zero) unless:
#  - the unit stem equals <ns>-<role>-<S12> for the set (compact JSON, s12-vectors.json);
#  - the per-set render drop-in and the per-role purge drop-in are installed;
#  - the job instances are NOT enabled;
#  - the placeholder units dotmac-secrets-unenrolled / dotmac-bao-unenrolled do not exist.
# Environment: SYSTEMCTL (default systemctl), UNIT_DIR (default /etc/systemd/system).
set -eu
[ "$#" -ge 3 ] || exit 2
ns=$1
role=$2
shift 2
systemctl=${SYSTEMCTL:-systemctl}
unit_dir=${UNIT_DIR:-/etc/systemd/system}
names=$(printf '%s\n' "$@" | tr 'A-Z' 'a-z' | LC_ALL=C sort | sed 's/.*/"&"/' | paste -sd, -)
s12=$(printf '[%s]' "$names" | sha256sum | cut -c1-12)
stem="$ns-$role-$s12"
[ -f "$unit_dir/openbao-agent@$stem.service.d/job-render.conf" ] || exit 1
[ -f "$unit_dir/dotmac-secrets-purge@$ns-$role.service.d/secrets-purge.conf" ] || exit 1
for unit in "openbao-agent@$stem.service" "dotmac-secrets-purge@$ns-$role.service"; do
  state=$("$systemctl" is-enabled "$unit" 2>/dev/null || true)
  case $state in enabled*|alias|linked*) exit 1 ;; esac
done
for placeholder in dotmac-secrets-unenrolled dotmac-bao-unenrolled; do
  if "$systemctl" cat "$placeholder.service" >/dev/null 2>&1; then exit 1; fi
  if getent passwd "$placeholder" >/dev/null 2>&1; then exit 1; fi
done
printf '%s\n' "$stem"
