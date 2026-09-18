#!/usr/bin/env python3
import json, os, glob, shutil, time, subprocess, re
from datetime import datetime, timezone

EXPORT="/srv/orchestrator-export"
OUT=os.path.join(os.path.dirname(__file__),"status.json")

def iso(ts=None):
    return datetime.fromtimestamp(ts or time.time(), timezone.utc).isoformat().replace("+00:00","Z")

def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as f: return json.load(f)
    except Exception: return default if default is not None else {}

def friendly_phase(phase, human_question, err):
    p=(phase or "UNKNOWN").upper()
    if p in {"DONE","COMPLETED","FINISHED","SUCCESS"}: return "done"
    if human_question: return "action"
    if err or p in {"FAILED","ERROR","CRASHED"}: return "error"
    if p in {"WORKING","RUNNING","EXECUTING","PLANNING"}: return "active"
    if p in {"REVIEWING","REVIEW"}: return "review"
    if p in {"WAITING","QUEUED","PENDING","PAUSED"}: return "waiting"
    if "HUMAN" in p or "APPROVAL" in p: return "action"
    return "waiting"
def redact(text):
    text=str(text or "")
    text=re.sub(r"/home/[^\s'\";,]+", "[VM-pad]", text)
    text=re.sub(r"https?://[^\s'\";,]+", "[link]", text)
    return text

def clip(text, n=220):
    text=" ".join(redact(text).split())
    return text if len(text)<=n else text[:n-1]+"…"

def cost_stats(jobdir):
    cost=tokens=calls=0
    last_model=None
    path=os.path.join(jobdir,"calls.jsonl")
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try: r=json.loads(line)
                except Exception: continue
                calls += 1
                cost += float(r.get("total_cost_usd") or 0)
                tokens += int(r.get("total_tokens") or 0)
                models=r.get("models_used") or []
                last_model=(models[-1] if models else r.get("model")) or last_model
    except Exception: pass
    return {"calls":calls,"tokens":tokens,"cost_usd":round(cost,2),"last_model":last_model}

def collect_job(jobdir):
    jid=os.path.basename(jobdir)
    live_dir=os.path.join("/home/myngle/orchestrator/jobs",jid)
    source_dir=live_dir if os.path.exists(os.path.join(live_dir,"state.json")) else jobdir
    state=load(os.path.join(source_dir,"state.json"),{})
    job=load(os.path.join(source_dir,"job.json"),{}) or load(os.path.join(jobdir,"job.json"),{})
    batch=(state.get("runtime") or {}).get("batch") or {}
    phase=state.get("phase")
    hq=state.get("human_question")
    err=state.get("last_error")
    status=friendly_phase(phase,hq,err)
    goal=batch.get("goal") or job.get("goal") or jid.replace("-"," ")
    number=batch.get("number")
    max_steps=job.get("max_steps")
    step=batch.get("step_id") or state.get("step_id")
    updated=state.get("updated_at")
    mtime=max([os.path.getmtime(p) for p in glob.glob(source_dir+"/*") if os.path.isfile(p)] or [time.time()])
    if status=="action":
        now="Jouw actie nodig: "+clip(hq or err or goal)
    elif status=="error":
        now="Fout: "+clip(err or goal)
    elif status=="review":
        now="Controleert het resultaat van deze stap."
    elif status=="active":
        now="Werkt aan: "+clip(goal)
    elif status=="done":
        now="Klaar."
    else:
        now="Wacht op de volgende stap."
    return {
      "id":jid,
      "title":jid.replace("-"," ").replace("_"," ").title(),
      "status":status,
      "phase":phase or "UNKNOWN",
      "now":now,
      "step":clip(step,100) if step else None,
      "batch":number,
      "max_steps":max_steps,
      "last_activity":updated or iso(mtime),
      "human_question":clip(hq,280) if hq else None,
      "error":clip(err,220) if err else None,
      **cost_stats(source_dir)
    }
def proc_snapshot():
    services={
      "orchestrator":{"label":"Orchestrator","patterns":["core.supervisor","core.ops_api"]},
      "hermes":{"label":"Hermes","patterns":["hermes"]},
      "dashboards":{"label":"Dashboards / webservers","patterns":["vite preview","http.server","streamlit"]},
      "sync":{"label":"Sync-processen","patterns":["nextcloud","rclone","syncthing"]},
      "ai":{"label":"Langdurige AI-taken","patterns":["claude --print","codex","gemini"]}
    }
    cmds=[]
    for p in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            raw=open(p,"rb").read().replace(b"\0",b" ").decode(errors="ignore").lower()
            if raw: cmds.append(raw)
        except Exception: pass
    out=[]
    for key,s in services.items():
        hits=sum(1 for c in cmds if any(x in c for x in s["patterns"]))
        out.append({"key":key,"label":s["label"],"status":"running" if hits else "quiet","count":hits})
    return out

def vm_stats():
    total,avail=0,0
    for line in open("/proc/meminfo"):
        if line.startswith("MemTotal:"): total=int(line.split()[1])*1024
        if line.startswith("MemAvailable:"): avail=int(line.split()[1])*1024
    du=shutil.disk_usage("/")
    uptime=float(open("/proc/uptime").read().split()[0])
    load=[float(x) for x in open("/proc/loadavg").read().split()[:3]]
    cpus=os.cpu_count() or 1
    return {
      "ram_used_pct":round((1-avail/total)*100,1) if total else None,
      "disk_used_pct":round(du.used/du.total*100,1),
      "disk_free_gb":round(du.free/1024**3,1),
      "uptime_hours":round(uptime/3600,1),
      "load_1m":load[0],
      "cpu_load_pct":round(min(load[0]/cpus*100,999),1)
    }

jobs=[]
for d in glob.glob(EXPORT+"/*"):
    if os.path.isdir(d) and os.path.exists(os.path.join(d,"state.json")):
        jobs.append(collect_job(d))

# Optional long-running tasks started outside the orchestrator.
manual_path=os.path.join(os.path.dirname(__file__),"manual_tasks.json")
for t in load(manual_path, []):
    if isinstance(t,dict) and t.get("id") and t.get("status") not in {"done","hidden"}:
        jobs.append({
          "id":str(t["id"]),
          "title":str(t.get("title") or t["id"]),
          "status":str(t.get("status") or "active"),
          "phase":str(t.get("phase") or "WORKING"),
          "now":clip(t.get("now") or "Langdurige taak draait op de VM.",220),
          "step":clip(t.get("step"),100) if t.get("step") else None,
          "batch":None,
          "max_steps":None,
          "progress":int(t.get("progress") or 0),
          "last_activity":str(t.get("last_activity") or iso()),
          "human_question":None,
          "error":None,
          "calls":0,"tokens":0,"cost_usd":0,"last_model":None
        })

# Explicit status contract for long-running jobs outside core.supervisor.
# A task writes task-status/<id>.json via task_status.py. This is authoritative.
task_dir=os.path.join(os.path.dirname(__file__),"task-status")
for sp in glob.glob(os.path.join(task_dir,"*.json")):
    t=load(sp,{})
    if not isinstance(t,dict) or not t.get("id"):
        continue
    jobs=[j for j in jobs if j.get("id") != t.get("id")]
    jobs.append({
      "id":str(t["id"]),
      "title":str(t.get("title") or t["id"]),
      "status":str(t.get("status") or "active"),
      "phase":str(t.get("phase") or "WORKING"),
      "now":clip(t.get("now") or ("Klaar." if t.get("status")=="done" else "Langdurige taak draait op de VM."),220),
      "step":clip(t.get("step"),100) if t.get("step") else None,
      "batch":None,
      "max_steps":None,
      "progress":int(t.get("progress") or (100 if t.get("status")=="done" else 0)),
      "last_activity":str(t.get("updated_at") or iso(os.path.getmtime(sp))),
      "human_question":None,
      "error":clip(t.get("error"),220) if t.get("error") else None,
      "calls":0,"tokens":0,"cost_usd":0,"last_model":None
    })

# Compatibility fallback for the import-flow worktree. Only infer ACTIVE while dirty.
# Completion is recorded explicitly through the status contract.
import_root="/home/myngle/worktrees/orchestrator-control-work"
if os.path.isdir(import_root):
    try:
        dirty=subprocess.run(
            ["git","-C",import_root,"status","--porcelain"],
            capture_output=True,text=True,timeout=5
        ).stdout.strip().splitlines()
        tracked=[os.path.join(import_root, line[3:]) for line in dirty if len(line) > 3]
        mtimes=[os.path.getmtime(x) for x in tracked if os.path.exists(x)]
        if dirty and not any(j.get("id")=="live-import-flow-update" for j in jobs):
            latest=max(mtimes or [time.time()])
            age_hours=(time.time()-latest)/3600
            jobs.append({
              "id":"live-import-flow-update",
              "title":"Live import flow update",
              "status":"active" if age_hours < 2 else "waiting",
              "phase":"WORKING" if age_hours < 2 else "PAUSED",
              "now":"Rondt de nieuwe import-wizard af en corrigeert de review-keuzes, daarna volgen build- en testcontroles.",
              "step":"import-wizard-final-corrections",
              "batch":None,
              "max_steps":None,
              "progress":90,
              "last_activity":iso(latest),
              "human_question":None,
              "error":None,
              "calls":0,
              "tokens":0,
              "cost_usd":0,
              "last_model":None
            })
    except Exception:
        pass

# Detect the newest HubSpot live audit even when launched outside core.supervisor.
# Source of truth is the audit Control Center JSON, not a hard-coded old log.
try:
    audit_files=glob.glob("/home/myngle/hubspot-audit-live-*/control_center.json")
    if audit_files:
        audit_file=max(audit_files,key=os.path.getmtime)
        audit=load(audit_file,{})
        run_check=subprocess.run(
            ["pgrep","-f","[r]un_hubspot_live_audit.*[.]py"],
            capture_output=True,text=True,timeout=3
        )
        running=run_check.returncode==0 and bool(run_check.stdout.strip())
        raw_status=str(audit.get("status") or "").lower()
        if running:
            hs_status="active"
        elif raw_status in {"completed","complete","done","success","finished"}:
            hs_status="done"
        elif raw_status in {"failed","error","crashed"}:
            hs_status="error"
        elif raw_status in {"running","working","active"}:
            hs_status="active"
        else:
            hs_status="waiting"
        hs_phase=str(audit.get("current_phase") or ("WORKING" if running else raw_status or "UNKNOWN")).upper()
        try:
            hs_progress=float(audit.get("overall_progress_pct") or 0)
        except Exception:
            hs_progress=0
        hs_now=str(audit.get("current_activity_plain_language") or audit.get("latest_update_plain_language") or "HubSpot-auditstatus wordt bijgewerkt.")
        hs_error=None
        if hs_status=="error":
            hs_error=str(audit.get("latest_update_plain_language") or "HubSpot-audit gestopt door een technische fout.")
        updated=str(audit.get("last_heartbeat") or iso(os.path.getmtime(audit_file)))
        run_id=str(audit.get("run_id") or os.path.basename(os.path.dirname(audit_file)))
        # Remove stale/older HubSpot audit cards. Only the newest source-of-truth card remains.
        jobs=[j for j in jobs if not ("hubspot" in (str(j.get("id",''))+" "+str(j.get("title",''))).lower() and "audit" in (str(j.get("id",''))+" "+str(j.get("title",''))).lower())]
        jobs.append({
          "id":run_id,
          "title":"HubSpot Live Audit",
          "status":hs_status,"phase":hs_phase,"now":hs_now,
          "step":"hubspot-live-audit","batch":None,"max_steps":None,
          "progress":hs_progress,"last_activity":updated,
          "human_question":None,"error":hs_error,
          "calls":0,"tokens":0,"cost_usd":float(audit.get("ai_cost_estimate") or 0),"last_model":"claude-sonnet-5"
        })
except Exception:
    pass

# Jobs explicitly removed from the Control Center are hidden from both monitors.
suppressions_path="/home/myngle/orchestrator/state/work_monitor_suppressions.json"
suppressions=load(suppressions_path,{})
if isinstance(suppressions,dict) and suppressions:
    hidden_ids=set(str(k) for k in suppressions)
    jobs=[j for j in jobs if str(j.get("id") or "") not in hidden_ids]

jobs.sort(key=lambda x:x["last_activity"], reverse=True)
counts={k:sum(1 for j in jobs if j["status"]==k) for k in ["active","waiting","review","done","error","action"]}
vm=vm_stats()
heavy=sum(1 for j in jobs if j["status"] in {"active","review"} and j.get("last_activity"))
pressure=max(
    float(vm.get("cpu_load_pct") or 0),
    float(vm.get("ram_used_pct") or 0),
    max(0.0,(float(vm.get("disk_used_pct") or 0)-70.0)*2.5),
    min(100.0,heavy*18.0)
)
if pressure>=90: level,label,advice="critical","KRITIEK","Geen extra zware job starten"
elif pressure>=75: level,label,advice="high","HOOG","Liever geen extra zware job starten"
elif pressure>=55: level,label,advice="medium","NORMAAL","Nog één zware taak kan waarschijnlijk"
else: level,label,advice="low","RUIMTE GENOEG","Er is ruimte voor extra werk"
payload={
  "generated_at":iso(),
  "summary":counts,
  "vm":vm,
  "capacity":{"level":level,"label":label,"advice":advice,"pressure_pct":round(pressure),"heavy_tasks":heavy},
  "services":proc_snapshot(),
  "jobs":jobs
}
tmp=OUT+".tmp"
with open(tmp,"w",encoding="utf-8") as f: json.dump(payload,f,ensure_ascii=False,separators=(",",":"))
os.replace(tmp,OUT)
print(json.dumps({"generated_at":payload["generated_at"],"jobs":len(jobs),"summary":counts}))
