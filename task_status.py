#!/usr/bin/env python3
import argparse, json, os, time
from datetime import datetime, timezone

ROOT=os.path.join(os.path.dirname(__file__),"task-status")
os.makedirs(ROOT,exist_ok=True)

def iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00","Z")

def load(path):
    try:
        with open(path,encoding="utf-8") as f: return json.load(f)
    except Exception:
        return {}
p=argparse.ArgumentParser()
p.add_argument("action",choices=["start","update","done","error"])
p.add_argument("id")
p.add_argument("--title")
p.add_argument("--progress",type=int)
p.add_argument("--message")
p.add_argument("--step")
args=p.parse_args()

path=os.path.join(ROOT,args.id+".json")
data=load(path)
data["id"]=args.id
if args.title: data["title"]=args.title
if args.progress is not None: data["progress"]=max(0,min(100,args.progress))
if args.message: data["now"]=args.message
if args.step: data["step"]=args.step
data["updated_at"]=iso()
if args.action=="start":
    data["status"]="active"
    data["phase"]="WORKING"
    data.setdefault("progress",0)
elif args.action=="update":
    data["status"]="active"
    data["phase"]="WORKING"
elif args.action=="done":
    data["status"]="done"
    data["phase"]="DONE"
    data["progress"]=100
    data.setdefault("now","Klaar.")
elif args.action=="error":
    data["status"]="error"
    data["phase"]="ERROR"

tmp=path+".tmp"
with open(tmp,"w",encoding="utf-8") as f:
    json.dump(data,f,ensure_ascii=False,separators=(",",":"))
os.replace(tmp,path)
print(json.dumps(data,ensure_ascii=False))
