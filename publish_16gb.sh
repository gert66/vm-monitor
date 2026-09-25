#!/bin/sh
set -eu
cd /home/orchestrator/vm-monitor-16gb
/opt/ai-orchestrator/bin/export_for_hermes.sh >/dev/null 2>&1 || true
/usr/bin/python3 collect_status.py
git checkout live-16gb >/dev/null 2>&1
git add status.json
git diff --cached --quiet && { git checkout monitor-16gb >/dev/null 2>&1; exit 0; }
git commit --amend --no-edit >/dev/null
git push --force origin live-16gb >/dev/null 2>&1
git checkout monitor-16gb >/dev/null 2>&1
