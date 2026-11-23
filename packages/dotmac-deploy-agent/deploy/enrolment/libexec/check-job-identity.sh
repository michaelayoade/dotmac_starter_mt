#!/bin/sh
# Enrolment read-back for the job identity file. Usage: check-job-identity.sh
# Environment: IDENTITY_FILE (default /etc/dotmac/deploy-agent/job-identity.toml),
# EXPECT_OWNER (uid that must own it, default 0), CHECK_USER (default 1: the named
# user must exist and own the stated uid; set 0 only in CI). Prints "<user> <uid> <inputs_root>".
set -eu
file=${IDENTITY_FILE:-/etc/dotmac/deploy-agent/job-identity.toml}
owner=${EXPECT_OWNER:-0}
[ -f "$file" ] && [ ! -L "$file" ] || exit 1
[ -n "$(find "$file" -maxdepth 0 -type f -perm 0644 -user "$owner")" ] || exit 1
uid=$(sed -n 's/^job_uid = \([0-9][0-9]*\)$/\1/p' "$file")
user=$(sed -n 's/^job_user = "\([a-z_][a-z0-9_-]*\)"$/\1/p' "$file")
inputs=$(sed -n 's/^inputs_root = "\(\/[A-Za-z0-9._\/-]*\)"$/\1/p' "$file")
[ -n "$uid" ] && [ -n "$user" ] && [ -n "$inputs" ] || exit 1
case $inputs in */../*|*/..|/run/dotmac|/run/dotmac/*|/) exit 1 ;; esac
# AGENT_CONFIG (optional): the agent's job_work_root must be the same directory.
if [ -n "${AGENT_CONFIG:-}" ]; then
  root=$(sed -n 's/^job_work_root = "\(.*\)"$/\1/p' "$AGENT_CONFIG")
  [ "$root" = "$inputs" ] || exit 1
fi
[ "$uid" -ge 1000 ] && [ "$user" != root ] || exit 1  # never root, never a system uid
if [ "${CHECK_USER:-1}" = 1 ]; then
  [ "$(id -u "$user" 2>/dev/null)" = "$uid" ] || exit 1
  id -Gn "$user" | tr ' ' '\n' | grep -qx docker && exit 1
fi
printf '%s %s %s\n' "$user" "$uid" "$inputs"
