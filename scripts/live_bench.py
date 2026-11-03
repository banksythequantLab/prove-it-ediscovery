"""Run every flow against a server and print status/citations/latency. Usage: live_bench.py URL [outjson]"""
import json, sys, time, httpx
URL = sys.argv[1]
QS = ["Flag likely privileged documents", "Build a timeline and check for back-dated documents", "Find near-duplicate documents",
      "Who discussed the price change before March 3?", "Chart communications by week", "Chart documents by sender",
      "Summarize the vendor note DOC-033", "Summarize the vendor note DOC-033 (containment drill)"]
out = []
for q in QS:
    t = time.time(); r = httpx.post(URL + "/api/ask", json={"question": q}, timeout=900).json(); dt = time.time() - t
    res = r["result"] or {}; F = res.get("findings", [])
    row = dict(q=q, planner=r.get("planner"), attempts=[a["status"] for a in r["attempts"]], secs=round(dt, 1),
               cites=f"{sum(f['verified'] for f in F)}/{len(F)}", charts=res.get("charts"), summary=(res.get("summary") or "")[:200],
               blocked=[b["kind"] for a in r["attempts"] for b in a.get("blocked", [])],
               errs=[(a.get("stderr") or "")[-300:] for a in r["attempts"] if a["status"] != "passed"])
    out.append(row); print(json.dumps(row), flush=True)
if len(sys.argv) > 2: json.dump(out, open(sys.argv[2], "w"), indent=1)
