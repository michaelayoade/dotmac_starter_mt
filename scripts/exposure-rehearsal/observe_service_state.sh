#!/usr/bin/env bash
# Which ports the TARGET is actually listening on, right now.
#
# Item 13 requires the negative probe to be measured against a RUNNING service.
# Until 2026-09-04 every probe carried `"service_running": true` as a LITERAL,
# and the runner read an ABSENT key as `True` — so an evidence file that simply
# omitted the field was indistinguishable from one that had measured the service
# up. Both are now impossible: this measures, and the runner refuses an absent
# key rather than defaulting it.
#
# The rule this enforces is the collector's own: NEVER PROBE A NEGATIVE AFTER
# TEARDOWN. A refusal against a port where nothing is listening measures an
# absent service, not an enforced exposure. A literal `true` was worse than
# wrong — it was about no moment at all.
#
# Read through the RESTRICTED observation identity: `ss -tln` is a host-level
# fact and needs neither sudo nor the docker group. Container state is
# deliberately not consulted; what matters to a negative probe is whether the
# socket is open, not what is behind it.
set -euo pipefail

TARGET="${1:?usage: observe_service_state.sh <target> <obs-user> <obs-key>}"
OBS_USER="${2:?usage: observe_service_state.sh <target> <obs-user> <obs-key>}"
OBS_KEY="${3:?usage: observe_service_state.sh <target> <obs-user> <obs-key>}"

SSH_OPTS=(
  -o BatchMode=yes
  -o ConnectTimeout=10
  -o StrictHostKeyChecking=yes
  -o IdentitiesOnly=yes
  -i "${OBS_KEY}"
)

# One space-separated list of listening TCP ports. `ss -tln` prints
# `LISTEN 0 4096 127.0.0.1:18443 0.0.0.0:*`; the port is whatever follows the
# LAST colon of the local address, which is the only parse that survives both
# `0.0.0.0:18443` and `[::1]:18443`.
ssh "${SSH_OPTS[@]}" "${OBS_USER}@${TARGET}" \
  "ss -tlnH 2>/dev/null | awk '{print \$4}' | sed 's/.*://' | sort -un | tr '\n' ' '"
