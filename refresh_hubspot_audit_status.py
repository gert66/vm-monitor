#!/usr/bin/env python3
import json
import os
import subprocess
from pathlib import Path

OUT = Path("/home/myngle/hubspot-audit-live-20260918-r2")
CC = OUT / "control_center.json"
CP = OUT / "raw" / "_checkpoint.json"

if not (CC.exists() and CP.exists()):
    raise SystemExit(0)

running = subprocess.run(
    ["pgrep", "-f", "[r]un_hubspot_live_audit_r2[.]py"],
    capture_output=True,
    text=True,
).returncode == 0
if not running:
    raise SystemExit(0)

cc = json.loads(CC.read_text(encoding="utf-8"))
cp = json.loads(CP.read_text(encoding="utf-8"))

incomplete = []
for name, meta in cp.items():
    if not isinstance(meta, dict):
        continue
    if meta.get("complete"):
        continue
    updated = str(meta.get("updated_at") or "")
    incomplete.append((updated, name, meta))

if not incomplete:
    raise SystemExit(0)

updated, name, meta = max(incomplete)
count = int(meta.get("record_count") or 0)
pages = int(meta.get("pages") or 0)
label = name.capitalize()

cc["status"] = "running"
cc["current_phase"] = "extraction"
cc["current_activity_plain_language"] = (
    f"{label} live uitlezen uit HubSpot: {count:,} records opgehaald in {pages} pagina's. "
    "De audit gaat automatisch verder vanaf het laatste checkpoint."
)
cc["latest_update_plain_language"] = (
    f"Laatste succesvolle HubSpot-pagina voor {name}: totaal {count:,} records."
)
if updated:
    cc["last_heartbeat"] = updated

tmp = CC.with_suffix(".json.tmp")
tmp.write_text(json.dumps(cc, indent=2, ensure_ascii=False), encoding="utf-8")
os.replace(tmp, CC)
