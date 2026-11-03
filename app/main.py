import csv, io, json, os, pathlib, shutil, time, uuid
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel
from . import ingest, sandbox, planner as planners
ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = pathlib.Path(os.getenv("DATA_DIR", ROOT / "data")); DATA.mkdir(exist_ok=True)
app = FastAPI(title="Prove-It eDiscovery")
STATE = {"corpus_dir": None, "docs": [], "name": None}
RUNS, CONTAINMENT = {}, []
MAX_ATTEMPTS = 3
def load_corpus(src, name):
    out = DATA / "normalized" / uuid.uuid4().hex[:8]
    STATE.update(docs=ingest.build(src, out), corpus_dir=out, name=name)
load_corpus(ROOT / "demo_corpus", "Acme Corp v. Brightline Logistics (synthetic demo)")
@app.get("/api/status")
def status():
    sb = sandbox.get_sandbox(); pl = planners.get_planner()
    return {"planner": pl.mode, "model": getattr(pl, "model", None), "sandbox": sb.name, "limits": sandbox.LIMITS,
            "corpus": STATE["name"], "doc_count": len(STATE["docs"])}
@app.get("/api/docs")
def docs():
    return [{k: d.get(k) for k in ("id","filename","type","date","from","subject","suspicious")} for d in STATE["docs"]]
@app.get("/api/doc/{doc_id}")
def doc(doc_id: str):
    for d in STATE["docs"]:
        if d["id"] == doc_id: return d
    raise HTTPException(404)
@app.post("/api/corpus/demo")
def demo(): load_corpus(ROOT / "demo_corpus", "Acme Corp v. Brightline Logistics (synthetic demo)"); return status()
@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(...)):
    dst = DATA / "uploads" / uuid.uuid4().hex[:8]; dst.mkdir(parents=True)
    for f in files:
        name = pathlib.Path(f.filename).name
        if name: (dst / name).write_bytes(await f.read())
    load_corpus(dst, f"Uploaded set ({len(files)} files)"); return status()
class Ask(BaseModel):
    question: str
    drill: bool = False
def verify(findings):
    byid = {d["id"]: d for d in STATE["docs"]}
    for f in findings:
        d = byid.get(f.get("doc_id")); q = (f.get("quote") or "").strip()
        f["verified"] = bool(d and q and " ".join(q.split()).lower() in " ".join((d.get("subject","")+" "+d["text"]).split()).lower())
    return findings
@app.post("/api/ask")
def ask(a: Ask):
    run_id = uuid.uuid4().hex[:8]; steps = []; t0 = time.time()
    pl = planners.get_planner()
    code_pl = planners.ScriptedPlanner() if (a.drill or "containment drill" in a.question.lower()) else pl
    sb = sandbox.get_sandbox(); history, attempts, result = [], [], None
    summary = f"{len(STATE['docs'])} docs; types: " + ", ".join(sorted({d['type'] for d in STATE['docs']}))
    log = lambda m: steps.append({"t": round(time.time()-t0, 2), "msg": m})
    log(f"Planner: {code_pl.mode}{' (containment drill: simulated hijacked model)' if code_pl is not pl else ''}; sandbox: {sb.name}")
    for n in range(1, MAX_ATTEMPTS + 1):
        try: plan, code = code_pl.write_code(a.question, history, summary)
        except Exception as e:
            log(f"Planner error: {e!r}"); break
        log(f"Attempt {n}: plan - {plan}"); log(f"Attempt {n}: launching fresh {sb.name} sandbox")
        r = sb.run(code, STATE["corpus_dir"])
        res = None
        for line in r["stdout"].splitlines():
            if line.startswith("@@PI_RESULT@@"): res = json.loads(line[len("@@PI_RESULT@@"):])
        r["stdout"] = "\n".join(l for l in r["stdout"].splitlines() if not l.startswith("@@PI_RESULT@@"))
        att = {"n": n, "plan": plan, "code": code, **r, "ok": r["exit_code"] == 0 and res is not None}
        attempts.append(att)
        log(f"Attempt {n}: exit {r['exit_code']} in {r['seconds']}s; sandbox destroyed: {r['destroyed']}")
        CONTAINMENT.append({"run_id": run_id, "attempt": n, "ts": time.strftime("%H:%M:%S"), "runner": r["runner"], "limits": r["limits"],
                            "exit_code": r["exit_code"], "blocked": r["blocked"], "code_attempts": r["code_attempts"], "destroyed": r["destroyed"]})
        if r["blocked"]: log(f"Attempt {n}: CONTAINMENT - blocked: " + "; ".join(f"{b['kind']} ({b['evidence']})" for b in r["blocked"]))
        if att["ok"]: result = res; break
        history.append({"code": code, "exit_code": r["exit_code"], "stderr": r["stderr"] or "no @@PI_RESULT@@ emitted"})
        log(f"Attempt {n}: failed; feeding stderr back to planner for retry")
        if n > 1 and history[-1]["code"] == history[-2]["code"]: log("Planner produced identical code; stopping retries"); break
    explanation = ""
    if result:
        result["findings"] = verify(result.get("findings", []))
        log(f"Citations verified: {sum(f['verified'] for f in result['findings'])}/{len(result['findings'])}")
        try: explanation = (planners.ScriptedPlanner() if code_pl is not pl else pl).explain(a.question, result)
        except Exception as e: explanation = f"(explanation failed: {e!r})"
    injected = [d["id"] for d in STATE["docs"] if d.get("suspicious")]
    run = {"id": run_id, "question": a.question, "planner": code_pl.mode, "drill": code_pl is not pl, "sandbox": sb.name,
           "steps": steps, "attempts": attempts, "result": result, "explanation": explanation, "suspicious_docs": injected,
           "containment_flag": any(x["blocked"] or x["code_attempts"] for x in attempts)}
    RUNS[run_id] = run; return run
@app.get("/api/containment")
def containment(): return CONTAINMENT[-200:]
@app.get("/api/runs/{run_id}/csv")
def run_csv(run_id: str):
    r = RUNS.get(run_id)
    if not r or not r["result"] or not r["result"].get("table"): raise HTTPException(404)
    t = r["result"]["table"]; b = io.StringIO(); w = csv.writer(b); w.writerow(t["columns"]); w.writerows(t["rows"])
    return PlainTextResponse(b.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={r['result'].get('csv_name') or 'results.csv'}"})
@app.get("/")
def index(): return FileResponse(ROOT / "app" / "static" / "index.html")
