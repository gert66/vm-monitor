#!/bin/sh
set -eu
if [ "$#" -lt 3 ]; then
  echo "usage: $0 <task-id> <title> <command...>" >&2
  exit 2
fi
ID="$1"
TITLE="$2"
shift 2
STATUS=/home/myngle/vm-monitor/task_status.py
python3 "$STATUS" start "$ID" --title "$TITLE" --progress 1 --message "Taak gestart op de VM."
set +e
"$@"
RC=$?
set -e
if [ "$RC" -eq 0 ]; then
  python3 "$STATUS" done "$ID" --title "$TITLE" --message "Taak succesvol afgerond."
else
  python3 "$STATUS" error "$ID" --title "$TITLE" --message "Taak gestopt met foutcode $RC."
fi
exit "$RC"
