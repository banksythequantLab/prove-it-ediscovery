#!/usr/bin/env python3
"""Live smoke test: load the demo collection, wait for ingest + scoring, check containment, custody and exports.
Usage: smoke.py https://host [--no-load MATTER_ID]"""
import json, sys, time, urllib.request
B = sys.argv[1].rstrip("/")
def get(p, raw=False):
    with urllib.request.urlopen(B + p, timeout=60) as r: return r.read().decode() if raw else json.load(r)
def post(p, b=None):
    rq = urllib.request.Request(B + p, data=json.dumps(b or {}).encode(), headers={"content-type": "application/json"}, method="POST")
    with urllib.request.urlopen(rq, timeout=120) as r: return json.load(r)
h = get("/api/health"); print("health:", h["sandbox"], h["scorer"])
mid = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[2] == "--no-load" else post("/api/demo/load")["matter_id"]
t0 = time.time()
while True:
    s = get(f"/api/matters/{mid}"); js = s["jobs"]
    print(f"\r{int(time.time()-t0):4d}s " + " | ".join(f"{j['kind']} {j['status']} {j['done']}/{j['total']}" for j in js), end="", flush=True)
    if len(js) >= 2 and all(j["status"] in ("done", "failed") for j in js): break
    time.sleep(5)
print(); c, k = s["counts"], s["scores"]
print(json.dumps({"counts": c, "scores": {x: k[x] for x in ("scored", "errors", "tin", "tout", "responsive", "priv", "hot", "cost_est_usd")},
                  "score_wall_s": round((k["t1"] or 0) - (k["t0"] or 0))}, indent=1))
for r in get(f"/api/matters/{mid}/docs?view=manual")["rows"]: print("MANUAL", r["control"], r["path"], "->", r["status_reason"])
for r in get(f"/api/matters/{mid}/docs?limit=12")["rows"]: print("TOP", r["relevance"], r["issues"], r["control"], r["custodian"], (r["subject"] or "")[:70], "|", r["rationale"])
cv = post(f"/api/matters/{mid}/custody/verify"); print("custody:", cv["matched"], "/", cv["checked"], "chain ok:", cv["audit_chain"]["ok"])
for e in ("coding.csv", "privlog.csv", "audit.csv"): print("export", e, len(get(f"/api/matters/{mid}/export/{e}", raw=True).splitlines()), "lines")
assert c["manual"] >= 1 and cv["matched"] == cv["checked"] and cv["audit_chain"]["ok"]
print("SMOKE OK matter", mid)
