#!/usr/bin/env python3
import json
import os
import subprocess
from pathlib import Path

OUT = Path("/home/myngle/hubspot-audit-live-20260918-r2")
CC = OUT / "control_center.json"
CP = OUT / "raw" / "_checkpoint.json"
GAPS = OUT / "gaps.json"

# Known baseline sizes from the HubSpot zero measurement. These are reference
# totals for a visual estimate only, never treated as exact extraction totals.
EXPECTED_TOTALS = {
    "companies": 275466,
    "contacts": 447063,
    "deals": 18665,
}
LABELS = {
    "companies": "Companies",
    "contacts": "Contacts",
    "deals": "Deals",
    "calls": "Calls",
    "meetings": "Meetings",
    "emails": "Emails",
    "notes": "Notes",
    "tasks": "Tasks",
}
ORDER = ["companies", "contacts", "deals", "calls", "meetings", "emails", "notes", "tasks"]

if not (CC.exists() and CP.exists()):
    raise SystemExit(0)

running = subprocess.run(
    ["pgrep", "-f", "[r]un_hubspot_live_audit_r2[.]py"],
    capture_output=True,
    text=True,
).returncode == 0

cc = json.loads(CC.read_text(encoding="utf-8"))
cp = json.loads(CP.read_text(encoding="utf-8"))
gaps = json.loads(GAPS.read_text(encoding="utf-8")) if GAPS.exists() else []
gap_objects = {str(g.get("object_name") or "") for g in gaps if isinstance(g, dict)}

completed_phases = set(cc.get("completed_phases") or [])
subprocesses = []

# Capability probe is a real, discrete subprocess.
cap_done = "capability_probe" in completed_phases or cc.get("current_phase") != "capability_probe"
subprocesses.append({
    "id": "capability_probe",
    "label": "Toegangscontrole",
    "status": "done" if cap_done else ("active" if running else "waiting"),
    "progress_pct": 100 if cap_done else None,
    "progress_is_estimate": False,
    "detail": "Controleert welke HubSpot-objecten veilig read-only leesbaar zijn.",
    "heartbeat": cc.get("last_heartbeat"),
})

# Extraction subprocesses. Missing checkpoint means waiting or unavailable.
for name in ORDER:
    meta = cp.get(name) if isinstance(cp.get(name), dict) else None
    unavailable = name in gap_objects
    if unavailable:
        subprocesses.append({
            "id": f"extract_{name}",
            "label": LABELS[name],
            "status": "gap",
            "progress_pct": None,
            "progress_is_estimate": False,
            "records": 0,
            "pages": 0,
            "detail": "Niet volledig leesbaar via deze HubSpot-scope; vastgelegd als datagap.",
            "heartbeat": cc.get("last_heartbeat"),
        })
        continue

    if not meta:
        subprocesses.append({
            "id": f"extract_{name}",
            "label": LABELS[name],
            "status": "waiting",
            "progress_pct": 0 if name in EXPECTED_TOTALS else None,
            "progress_is_estimate": name in EXPECTED_TOTALS,
            "records": 0,
            "pages": 0,
            "detail": "Wacht op dit extractieonderdeel.",
            "heartbeat": None,
        })
        continue

    records = int(meta.get("record_count") or 0)
    pages = int(meta.get("pages") or 0)
    complete = bool(meta.get("complete"))
    updated = meta.get("updated_at")
    expected = EXPECTED_TOTALS.get(name)
    progress = None
    estimated = False
    if complete:
        progress = 100
    elif expected:
        progress = round(min(99.0, records / expected * 100), 1)
        estimated = True

    status = "done" if complete else "waiting"
    if running and not complete:
        # The checkpoint with the freshest successful page is the active extractor.
        status = "queued"

    detail = f"{records:,} records · {pages:,} pagina's"
    if expected and not complete:
        detail += f" · ≈ {progress:.1f}% van referentietotaal {expected:,}"
    elif complete:
        detail += " · afgerond"

    subprocesses.append({
        "id": f"extract_{name}",
        "label": LABELS[name],
        "status": status,
        "progress_pct": progress,
        "progress_is_estimate": estimated,
        "records": records,
        "pages": pages,
        "expected_records": expected,
        "detail": detail,
        "heartbeat": updated,
    })

# Determine the actually active extractor from the freshest incomplete checkpoint.
incomplete = []
for name, meta in cp.items():
    if not isinstance(meta, dict) or meta.get("complete"):
        continue
    updated = str(meta.get("updated_at") or "")
    incomplete.append((updated, name, meta))

if running and incomplete:
    updated, active_name, active_meta = max(incomplete)
    for item in subprocesses:
        if item["id"] == f"extract_{active_name}":
            item["status"] = "active"
            break

    count = int(active_meta.get("record_count") or 0)
    pages = int(active_meta.get("pages") or 0)
    label = LABELS.get(active_name, active_name.capitalize())
    cc["status"] = "running"
    cc["current_phase"] = "extraction"
    cc["current_activity_plain_language"] = (
        f"{label} live uitlezen uit HubSpot: {count:,} records opgehaald in {pages:,} pagina's. "
        "De audit gaat automatisch verder vanaf het laatste checkpoint."
    )
    cc["latest_update_plain_language"] = (
        f"Laatste succesvolle HubSpot-pagina voor {active_name}: totaal {count:,} records."
    )
    if updated:
        cc["last_heartbeat"] = updated

# Later phases become visible even while they wait.
LATER_PHASES = [
    ("normalization", "Normalisatie"),
    ("company_analysis", "Company-analyse"),
    ("contact_analysis", "Contact-analyse"),
    ("deal_analysis", "Deal-analyse"),
    ("activity_analysis", "Activity-analyse"),
    ("property_analysis", "Property-analyse"),
    ("pipeline_analysis", "Pipeline-analyse"),
    ("historical_imports", "Historische imports"),
    ("cross_object", "Cross-object analyse"),
    ("ai_interpretation", "AI-onderzoek"),
    ("report_generation", "Rapportopbouw"),
]
current_phase = str(cc.get("current_phase") or "")
for phase_id, label in LATER_PHASES:
    if phase_id in completed_phases:
        status, pct, detail = "done", 100, "Afgerond."
    elif current_phase == phase_id and running:
        status, pct, detail = "active", None, "Bezig; detailstatus wordt bijgewerkt bij nieuwe resultaten."
    else:
        status, pct, detail = "waiting", 0, "Wacht op eerdere stappen."
    subprocesses.append({
        "id": phase_id,
        "label": label,
        "status": status,
        "progress_pct": pct,
        "progress_is_estimate": False,
        "detail": detail,
        "heartbeat": cc.get("last_heartbeat") if status in {"active", "done"} else None,
    })

cc["subprocesses"] = subprocesses

tmp = CC.with_suffix(".json.tmp")
tmp.write_text(json.dumps(cc, indent=2, ensure_ascii=False), encoding="utf-8")
os.replace(tmp, CC)
