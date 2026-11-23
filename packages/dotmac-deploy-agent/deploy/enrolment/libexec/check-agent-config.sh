#!/bin/sh
# Enrolment read-back for the agent config. Usage: check-agent-config.sh <path>
# The file is root-owned and mode 0644: READABLE by the non-root agent (a 0600 file
# would make it unreadable and the agent would refuse to start), never group/other
# writable. Environment: EXPECT_OWNER (default 0).
set -eu
file=$1
owner=${EXPECT_OWNER:-0}
[ -f "$file" ] && [ ! -L "$file" ] || exit 1
[ -n "$(find "$file" -maxdepth 0 -type f -perm 0644 -user "$owner")" ] || exit 1
printf '%s\n' "$file"
