#!/bin/sh
set -eu
LOCK=/tmp/vm-monitor-publish.lock
exec 9>"$LOCK"
flock -n 9 || exit 0
BASE=/home/myngle/vm-monitor
LIVE=/home/myngle/vm-monitor-live
/usr/bin/python3 "$BASE/refresh_hubspot_audit_status.py" || true
/usr/bin/python3 "$BASE/collect_status.py"
cp "$BASE/status.json" "$LIVE/status.json"
AUDIT=/home/myngle/hubspot-audit-live-20260918-r2/control_center.json
if [ -f "$AUDIT" ]; then
  mkdir -p "$LIVE/hubspot-audit"
  cp "$AUDIT" "$LIVE/hubspot-audit/control_center.json"
fi
cd "$LIVE"
git add status.json
[ -f hubspot-audit/control_center.json ] && git add hubspot-audit/control_center.json
git diff --cached --quiet && exit 0
git commit --amend --no-edit >/dev/null
git push --force origin live >/dev/null 2>&1
