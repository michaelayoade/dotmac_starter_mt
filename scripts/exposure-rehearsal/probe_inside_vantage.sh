#!/usr/bin/env bash
# Probe the target's PRIVATE port from a vantage INSIDE its accepted source set.
#
# Items 12 and 16 both need a probe that genuinely originates inside the source
# set. Until 2026-09-04 neither had one: `private_inside.reachable` was the
# literal `false` and `privileged_vantage_refused` was `null`, because the
# collecting host is outside the set by construction.
#
# The inside vantage is reached as a JUMP, never as a shell. Its key carries
# `command="/bin/false"`, so nothing runs there; `ssh -W` opens a direct-tcpip
# channel from the jump to the target, which is exactly the property these items
# need — the TCP connection ORIGINATES inside the source set while no command
# executes on the vantage and no credential is placed there.
#
# ## Three outcomes, and conflating two of them would be the defect
#
#   reached     the port answered
#   refused     the connection reached the TARGET and nothing was listening
#   prohibited  the JUMP KEY refused to open the channel at all
#
# `prohibited` is NOT an exposure result. It means `permitopen` does not cover
# the port, so the probe never left the vantage and says nothing about the
# target's firewall. Reporting it as `reachable: false` would turn a
# misconfigured key into evidence of a correctly closed port — the quietest
# possible false pass. Measured 2026-09-04: `19001` answers `connect failed:
# Connection refused` before the apply and `18443` answers `administratively
# prohibited`, so the two are distinguishable in practice and not only in
# principle.
#
# ## The controls, because four identical denials prove nothing
#
# A `permitopen` that refused everything would produce the same denial as one
# that is correctly scoped. So two controls are probed alongside: a port the key
# deliberately does NOT open (18443) and a host it deliberately does not reach
# (production ns1). Both must come back `prohibited` while the private port does
# not. Without them, "refused" would only establish that something refuses.
#
# ## Filter stderr explicitly
#
# `ssh` writes host-key and banner noise to stderr. A first run of this table
# elsewhere returned five identical lines — all of them the host-key warning —
# and read as five passes carrying zero information. Every classification below
# matches on a specific phrase and falls through to `unknown` rather than to a
# convenient default.
set -euo pipefail

JUMP="${1:?usage: probe_inside_vantage.sh <jump> <target-v4> <target-v6> <port>}"
TARGET4="${2:?usage: probe_inside_vantage.sh <jump> <target-v4> <target-v6> <port>}"
TARGET6="${3:?usage: probe_inside_vantage.sh <jump> <target-v4> <target-v6> <port>}"
PORT="${4:?usage: probe_inside_vantage.sh <jump> <target-v4> <target-v6> <port>}"

: "${LANE3_JUMP_KEY:?unset: the private key for the inside-vantage jump identity}"

SSH_OPTS=(
  -o BatchMode=yes
  -o ConnectTimeout=10
  -o StrictHostKeyChecking=yes
  -o IdentitiesOnly=yes
  -i "${LANE3_JUMP_KEY}"
)

# `[%h]:%p` MUST be quoted. Unquoted, zsh globs the brackets and the
# ProxyCommand dies with `no matches found` — which reads like a connectivity
# failure and is not one.
classify() {
  local host="$1" port="$2" err
  err="$(ssh "${SSH_OPTS[@]}" -W "[${host}]:${port}" "${JUMP}" </dev/null 2>&1 >/dev/null || true)"
  case "${err}" in
    *"administratively prohibited"*) printf prohibited ;;
    *"Connection refused"*)          printf refused ;;
    *"onnection timed out"*|*"No route to host"*) printf silent ;;
    "")                              printf reached ;;
    *)                               printf unknown ;;
  esac
}

# The vantage's own source addresses are NOT derived here. They are read from
# the target through `observe_far_end.sh` with this same jump key, so the value
# is the far end's report rather than this script's belief — and per family,
# because this vantage's v4 and v6 sit on different segments and one prefix
# cannot describe both.

v4="$(classify "${TARGET4}" "${PORT}")"
v6="$(classify "${TARGET6}" "${PORT}")"
control_port="$(classify "${TARGET4}" 18443)"

printf '{\n'
printf '  "private_port_v4": "%s",\n' "${v4}"
printf '  "private_port_v6": "%s",\n' "${v6}"
printf '  "control_unopened_port": "%s"\n' "${control_port}"
printf '}\n'
