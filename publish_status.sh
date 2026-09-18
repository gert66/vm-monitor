#!/bin/sh
set -eu
LOCK=/tmp/vm-monitor-publish.lock
exec 9>"$LOCK"
flock -n 9 || exit 0
BASE=/home/myngle/vm-monitor
LIVE=/home/myngle/vm-monitor-live
/usr/bin/python3 "$BASE/collect_status.py"
cp "$BASE/status.json" "$LIVE/status.json"
cd "$LIVE"
git add status.json
git diff --cached --quiet && exit 0
git commit --amend --no-edit >/dev/null
git push --force origin live >/dev/null 2>&1
