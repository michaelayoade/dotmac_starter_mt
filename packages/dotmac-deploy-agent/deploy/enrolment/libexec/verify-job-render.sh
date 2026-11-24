#!/bin/sh
# Installed as /usr/local/libexec/dotmac/verify-job-render (root-owned, 0755). Run as the
# render unit's ExecStartPost, as the CONTAINER uid, so it alone may look inside
# `secrets/` (0700). Usage: verify-job-render <secrets dir> <declared file name>...
# Fails (non-zero) unless every declared file is a regular, non-empty, 0400 file
# owned by the calling uid AND the directory holds nothing else. The unit then
# fails, and the deploy agent reads only its Result and ExecMainStatus.
set -eu
dir=$1
shift
uid=$(id -u)
for name in "$@"; do
  f="$dir/$name"
  [ -f "$f" ] && [ ! -L "$f" ] && [ -s "$f" ] || exit 1
  [ -n "$(find "$f" -maxdepth 0 -type f -perm 0400 -user "$uid")" ] || exit 1
done
for path in "$dir"/* "$dir"/.[!.]* "$dir"/..?*; do
  [ -e "$path" ] || [ -L "$path" ] || continue
  base=${path##*/}
  found=0
  for name in "$@"; do
    [ "$base" = "$name" ] && found=1
  done
  [ "$found" = 1 ] || exit 1
done
exit 0
