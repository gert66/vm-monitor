#!/bin/sh
set -eu
LOCK=/tmp/myngle-control-center-publish.lock
exec 9>"$LOCK"
flock -n 9 || exit 0
BASE=/home/orchestrator/vm-monitor
cd "$BASE"
python3 collect_status.py >/dev/null
git add status.json
git diff --cached --quiet && exit 0
git commit --amend --no-edit >/dev/null
git push --force-with-lease origin live >/dev/null 2>&1
