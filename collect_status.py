#!/usr/bin/env python3
import glob, json, os, re, shutil, subprocess, time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path("/opt/ai-orchestrator")
JOBS = ROOT / "jobs"
HEALTH = ROOT / "monitor" / "status.json"
HISTORY = ROOT / "monitor" / "history"
OUT = Path(__file__).resolve().parent / "status.json"
LOCAL_TZ = ZoneInfo("Europe/Amsterdam")

def load(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {} if default is None else default

def iso(ts=None):
    dt = datetime.fromtimestamp(ts or time.time(), timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")

def redact(value):
    text = str(value or "")
    text = re.sub(r"/(?:home|opt|srv)/[^\s'\";,]+", "[path]", text)
    text = re.sub(r"https?://[^\s'\";,]+", "[link]", text)
    text = re.sub(r"(?i)(token|secret|password|api[_-]?key)\s*[:=]\s*\S+", r"\1=[redacted]", text)
    return text

def clip(value, n=260):
    text = " ".join(redact(value).split())
    return text if len(text) <= n else text[:n-1] + "…"
def classify(phase, human_question, error):
    p = str(phase or "UNKNOWN").upper()
    if p in {"DONE","COMPLETED","FINISHED","SUCCESS"}:
        return "done"
    if human_question or "HUMAN" in p or "APPROVAL" in p:
        return "action"
    if error or p in {"FAILED","ERROR","CRASHED"}:
        return "error"
    if p in {"WORKING","RUNNING","EXECUTING","PLANNING","EVIDENCE","COMMITTING"}:
        return "active"
    if p in {"REVIEWING","REVIEW"}:
        return "review"
    return "waiting"

def call_stats(path):
    stats = {"calls":0,"tokens":0,"cost_usd":0.0,"last_model":None,"last_provider":None}
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except Exception:
                continue
            stats["calls"] += 1
            stats["tokens"] += int(r.get("total_tokens") or 0)
            stats["cost_usd"] += float(r.get("total_cost_usd") or 0)
            models = r.get("models_used") or []
            stats["last_model"] = (models[-1] if models else r.get("model")) or stats["last_model"]
            stats["last_provider"] = r.get("provider") or stats["last_provider"]
    except Exception:
        pass
    stats["cost_usd"] = round(stats["cost_usd"], 2)
    return stats

def collect_job(jobdir):
    state = load(jobdir / "state.json", {})
    job = load(jobdir / "job.json", {})
    runtime = state.get("runtime") or {}
    batch = runtime.get("batch") or {}
    human = runtime.get("human") or {}
    phase = state.get("phase") or "UNKNOWN"
    hq = state.get("human_question") or human.get("question") or human.get("reason")
    err = state.get("last_error") or state.get("error")
    status = classify(phase, hq, err)
    goal = batch.get("goal") or job.get("goal") or jobdir.name.replace("-"," ")
    updated = state.get("updated_at")
    hb = load(jobdir / "heartbeat.json", {})
    heartbeat = hb.get("timestamp") or hb.get("at") or hb.get("updated_at")
    if not updated:
        try: updated = iso((jobdir / "state.json").stat().st_mtime)
        except Exception: updated = None
    if status == "action": now = "Jouw actie nodig: " + clip(hq or err or goal)
    elif status == "error": now = "Fout: " + clip(err or goal)
    elif status == "review": now = "Controleert het resultaat van deze stap."
    elif status == "active": now = "Werkt aan: " + clip(goal)
    elif status == "done": now = "Klaar."
    else: now = "Wacht op de volgende stap."
    return {
        "id": jobdir.name,
        "title": jobdir.name.replace("-"," ").replace("_"," ").title(),
        "status": status,
        "phase": phase,
        "now": now,
        "step": clip(batch.get("step_id") or state.get("step_id"), 100),
        "batch": batch.get("number"),
        "max_steps": job.get("max_steps"),
        "last_activity": updated,
        "heartbeat": heartbeat,
        "human_question": clip(hq, 320) if hq else None,
        "error": clip(err, 240) if err else None,
        **call_stats(jobdir / "calls.jsonl"),
    }

def service_snapshot():
    services = []
    health = load(HEALTH, {})
    for unit, state in (health.get("core_units") or {}).items():
        services.append({"key":unit,"label":unit.replace(".timer",""),"status":"running" if state=="active" else state,"count":1 if state=="active" else 0})
    try:
        out = subprocess.check_output(["systemctl","list-units","--type=service","--state=running","--no-legend","--no-pager"], text=True, timeout=4)
        orch = sum(1 for line in out.splitlines() if line.strip().startswith("orchestrator@"))
    except Exception:
        orch = 0
    services.append({"key":"orchestrator_jobs","label":"Actieve orchestrator-services","status":"running" if orch else "quiet","count":orch})
    return services

def history_snapshot():
    now = datetime.now(timezone.utc)
    raw24, daily = [], defaultdict(lambda: {"n":0,"ram":0.0,"ram_max":0.0,"cpu":0.0,"cpu_n":0,"cpu_max":0.0,"load":0.0,"disk_max":0.0})
    for fp in sorted(HISTORY.glob("*.jsonl")):
        try:
            day_dt = datetime.strptime(fp.stem, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if day_dt < now - timedelta(days=31):
            continue
        try:
            lines = fp.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        for idx, line in enumerate(lines):
            try: s = json.loads(line)
            except Exception: continue
            ts = s.get("timestamp")
            if not ts: continue
            dt = datetime.fromtimestamp(float(ts), timezone.utc)
            if dt >= now - timedelta(hours=24) and idx % 5 == 0:
                raw24.append({"ts":s.get("timestamp_iso") or iso(ts),"ram":s.get("ram_used_pct"),"cpu":s.get("cpu_used_pct"),"load":s.get("load_5m"),"disk":s.get("disk_used_pct"),"health":s.get("health_state")})
            key = dt.astimezone(LOCAL_TZ).date().isoformat()
            d = daily[key]; d["n"] += 1
            ram = float(s.get("ram_used_pct") or 0); d["ram"] += ram; d["ram_max"] = max(d["ram_max"], ram)
            cpu = s.get("cpu_used_pct")
            if cpu is not None:
                cpu=float(cpu); d["cpu"] += cpu; d["cpu_n"] += 1; d["cpu_max"] = max(d["cpu_max"], cpu)
            d["load"] += float(s.get("load_5m") or 0); d["disk_max"] = max(d["disk_max"], float(s.get("disk_used_pct") or 0))
    days=[]
    for day in sorted(daily):
        d=daily[day]; n=max(d["n"],1)
        days.append({"day":day,"ram_avg":round(d["ram"]/n,1),"ram_max":round(d["ram_max"],1),"cpu_avg":round(d["cpu"]/max(d["cpu_n"],1),1),"cpu_max":round(d["cpu_max"],1),"load_avg":round(d["load"]/n,2),"disk_max":round(d["disk_max"],1)})
    return {"last_24h":raw24[-300:],"daily_30d":days[-30:]}

def cost_snapshot():
    now_local = datetime.now(LOCAL_TZ)
    starts = {
        "today": now_local.replace(hour=0,minute=0,second=0,microsecond=0),
        "7d": now_local - timedelta(days=7),
        "30d": now_local - timedelta(days=30),
    }
    totals={k:{"cost_usd":0.0,"tokens":0,"calls":0} for k in starts}
    by_model=defaultdict(lambda: {"cost_usd":0.0,"tokens":0,"calls":0})
    by_provider=defaultdict(lambda: {"cost_usd":0.0,"tokens":0,"calls":0})
    for fp in JOBS.glob("*/calls.jsonl"):
        try: lines=fp.read_text(encoding="utf-8").splitlines()
        except Exception: continue
        for line in lines:
            try: r=json.loads(line)
            except Exception: continue
            raw=r.get("ts")
            try: dt=datetime.fromisoformat(raw.replace("Z","+00:00")).astimezone(LOCAL_TZ)
            except Exception: continue
            cost=float(r.get("total_cost_usd") or 0); tokens=int(r.get("total_tokens") or 0)
            models=r.get("models_used") or []; model=(models[-1] if models else r.get("model")) or "unknown"
            provider=r.get("provider") or "unknown"
            for key,start in starts.items():
                if dt >= start:
                    totals[key]["cost_usd"] += cost; totals[key]["tokens"] += tokens; totals[key]["calls"] += 1
            if dt >= starts["30d"]:
                by_model[model]["cost_usd"] += cost; by_model[model]["tokens"] += tokens; by_model[model]["calls"] += 1
                by_provider[provider]["cost_usd"] += cost; by_provider[provider]["tokens"] += tokens; by_provider[provider]["calls"] += 1
    for bucket in [totals, by_model, by_provider]:
        for v in bucket.values(): v["cost_usd"]=round(v["cost_usd"],2)
    models=sorted(({"name":k,**v} for k,v in by_model.items()),key=lambda x:x["cost_usd"],reverse=True)
    providers=sorted(({"name":k,**v} for k,v in by_provider.items()),key=lambda x:x["cost_usd"],reverse=True)
    return {"periods":totals,"models_30d":models,"providers_30d":providers}
def main():
    health=load(HEALTH,{})
    jobs=[collect_job(Path(d)) for d in glob.glob(str(JOBS/"*")) if Path(d).is_dir() and (Path(d)/"state.json").exists()]
    jobs.sort(key=lambda x:x.get("last_activity") or "", reverse=True)
    statuses=["active","waiting","review","done","error","action"]
    summary={k:sum(1 for j in jobs if j["status"]==k) for k in statuses}
    payload={
        "generated_at":iso(),
        "health_state":health.get("health_state","UNKNOWN"),
        "health_reasons":health.get("health_reasons") or [],
        "summary":summary,
        "vm":{
            "cpu_used_pct":health.get("cpu_used_pct"),"cpu_count":health.get("cpu_count"),
            "load_1m":health.get("load_1m"),"load_5m":health.get("load_5m"),"load_15m":health.get("load_15m"),
            "ram_used_pct":health.get("ram_used_pct"),"ram_used_gb":health.get("ram_used_gb"),
            "ram_available_gb":health.get("ram_available_gb"),"ram_total_gb":health.get("ram_total_gb"),
            "swap_used_pct":health.get("swap_used_pct"),"swap_total_gb":health.get("swap_total_gb"),
            "disk_used_pct":health.get("disk_used_pct"),"disk_free_gb":health.get("disk_free_gb"),
            "uptime_hours":health.get("uptime_hours")
        },
        "services":service_snapshot(),
        "jobs":jobs,
        "costs":cost_snapshot(),
        "history":history_snapshot(),
    }
    tmp=OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload,ensure_ascii=False,separators=(",",":"))+"\n",encoding="utf-8")
    tmp.replace(OUT)
    print(json.dumps({"generated_at":payload["generated_at"],"health":payload["health_state"],"jobs":len(jobs),"summary":summary,"today_cost":payload["costs"]["periods"]["today"]["cost_usd"]}))

if __name__=="__main__":
    main()
