import csv, difflib, io, json, os, pathlib, shutil, threading, time, uuid
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
def _execute(run, drill):
    a_q = run["question"]; t0 = time.time(); steps = run["steps"]; attempts = run["attempts"]
    pl = planners.get_planner()
    code_pl = planners.ScriptedPlanner() if (drill or "containment drill" in a_q.lower()) else pl
    sb = sandbox.get_sandbox(); history, result = [], None
    run.update(planner=code_pl.mode, drill=code_pl is not pl, sandbox=sb.name)
    summary = f"{len(STATE['docs'])} docs; types: " + ", ".join(sorted({d['type'] for d in STATE['docs']}))
    log = lambda m: steps.append({"t": round(time.time()-t0, 2), "msg": m})
    log(f"Planner: {code_pl.mode}{' (containment drill: simulated hijacked model)' if code_pl is not pl else ''}; sandbox: {sb.name}")
    for n in range(1, MAX_ATTEMPTS + 1):
        att = {"n": n, "status": "writing", "plan": "", "code": "", "diff": "", "charts": []}; attempts.append(att)
        run["phase"] = f"attempt {n}: model writing code"
        try: plan, code = code_pl.write_code(a_q, history, summary)
        except Exception as e:
            log(f"Planner error: {e!r}"); att["status"] = "planner-error"; att["stderr"] = repr(e); break
        prev = attempts[-2]["code"] if n > 1 else None
        att.update(plan=plan, code=code, status="running",
                   diff="".join(difflib.unified_diff(prev.splitlines(True), code.splitlines(True), f"attempt_{n-1}.py", f"attempt_{n}.py")) if prev else "")
        log(f"Attempt {n}: plan - {plan}"); log(f"Attempt {n}: launching fresh {sb.name} sandbox")
        run["phase"] = f"attempt {n}: running in fresh sandbox"
        r = sb.run(code, STATE["corpus_dir"]); res = None
        for line in r["stdout"].splitlines():
            if line.startswith("@@PI_RESULT@@"):
                try: res = json.loads(line[len("@@PI_RESULT@@"):])
                except Exception: pass
        r["stdout"] = "\n".join(l for l in r["stdout"].splitlines() if not l.startswith("@@PI_RESULT@@"))
        ok = r["exit_code"] == 0 and res is not None
        bad = []
        if ok:  # citation gate: unverifiable quotes count as a failure and are fed back (only while retries remain)
            bad = [f for f in verify(res.get("findings") or []) if not f["verified"]]
            if bad and n < MAX_ATTEMPTS:
                ok = False; r["stderr"] = (r["stderr"] or "") + "CITATION CHECK FAILED - these findings' quotes are not verbatim spans of the cited document's text/subject:\n" + \
                    "\n".join(f"  {f.get('doc_id')}: {(f.get('quote') or '')[:120]!r}" for f in bad[:15]) + \
                    "\nCopy quotes exactly (one short line) from d['text'] or d['subject'], or drop findings that have no quote."
        att.update(**r, ok=ok, status="passed" if ok else "failed")
        log(f"Attempt {n}: exit {r['exit_code']} in {r['seconds']}s; {len(r['charts'])} chart(s); sandbox destroyed: {r['destroyed']}")
        CONTAINMENT.append({"run_id": run["id"], "attempt": n, "ts": time.strftime("%H:%M:%S"), "runner": r["runner"], "limits": r["limits"],
                            "exit_code": r["exit_code"], "blocked": r["blocked"], "code_attempts": r["code_attempts"], "destroyed": r["destroyed"]})
        if r["blocked"]: log(f"Attempt {n}: CONTAINMENT - blocked: " + "; ".join(f"{b['kind']} ({b['evidence']})" for b in r["blocked"]))
        if ok: result = res; result["charts"] = [c["name"] for c in r["charts"]]; break
        history.append({"code": code, "exit_code": r["exit_code"], "stderr": r["stderr"] or "no @@PI_RESULT@@ emitted (call pi.emit exactly once)"})
        log(f"Attempt {n}: failed; feeding stderr back to planner for retry")
        if n > 1 and history[-1]["code"] == history[-2]["code"]: log("Planner produced identical code; stopping retries"); break
    explanation = ""
    if result:
        run["phase"] = "verifying citations + explaining"
        result["findings"] = verify(result.get("findings", []))
        log(f"Citations verified: {sum(f['verified'] for f in result['findings'])}/{len(result['findings'])}")
        try: explanation = (planners.ScriptedPlanner() if code_pl is not pl else pl).explain(a_q, result)
        except Exception as e: explanation = f"(explanation failed: {e!r})"
    run.update(result=result, explanation=explanation, suspicious_docs=[d["id"] for d in STATE["docs"] if d.get("suspicious")],
               containment_flag=any(x.get("blocked") or x.get("code_attempts") for x in attempts), done=True, phase="done")
def _new_run(q):
    rid = uuid.uuid4().hex[:8]
    RUNS[rid] = {"id": rid, "question": q, "steps": [], "attempts": [], "result": None, "explanation": "", "done": False,
                 "phase": "queued", "suspicious_docs": [], "containment_flag": False}
    return RUNS[rid]
@app.post("/api/ask")
def ask(a: Ask):
    run = _new_run(a.question); _execute(run, a.drill); return run
@app.post("/api/ask_async")
def ask_async(a: Ask):
    run = _new_run(a.question)
    def go():
        try: _execute(run, a.drill)
        except Exception as e: run.update(done=True, phase=f"error: {e!r}")
    threading.Thread(target=go, daemon=True).start(); return {"id": run["id"]}
@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    if run_id not in RUNS: raise HTTPException(404)
    return RUNS[run_id]
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
