#!/bin/sh
# Enrolment read-back for the agent config. Usage: check-agent-config.sh <path>
# The file is root:dotmac-deploy and mode 0640: READABLE by the non-root agent through
# its group (a 0600 file would make it unreadable and the agent would refuse to start),
# not world-readable (it names the namespace, the roles and the CP keys), never
# group/other writable. Environment: EXPECT_OWNER (default 0), EXPECT_GROUP (default
# dotmac-deploy).
set -eu
file=$1
owner=${EXPECT_OWNER:-0}
group=${EXPECT_GROUP:-dotmac-deploy}
[ -f "$file" ] && [ ! -L "$file" ] || exit 1
[ -n "$(find "$file" -maxdepth 0 -type f -perm 0640 -user "$owner" -group "$group")" ] || exit 1
printf '%s\n' "$file"
