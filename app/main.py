"""Prove-It eDiscovery: FastAPI orchestrator. Holds the inference key; never holds the Docker socket."""
import csv, io, json, os, pathlib, random, re, tempfile, threading, time, uuid, zipfile
from collections import defaultdict, deque
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from . import analytics, db, pipeline, sandbox, scoring
APP_DIR = pathlib.Path(__file__).parent; ROOT = db.ROOT
MAX_UPLOAD = int(os.getenv("MAX_UPLOAD_MB", 200)) * 1024 * 1024
RATE_N, RATE_WIN = int(os.getenv("UPLOAD_RATE_N", 6)), 600
DEMO_ZIP = pathlib.Path(os.getenv("DEMO_ZIP", ROOT / "demo" / "enron_demo.zip"))
DEMO_PROTOCOL = pathlib.Path(os.getenv("DEMO_PROTOCOL", ROOT / "demo" / "protocol.json"))
app = FastAPI(title="Prove-It eDiscovery", docs_url="/api/docs")
_hits = defaultdict(deque)
@app.on_event("startup")
def _start():
    pipeline.start(); threading.Thread(target=_nightly, daemon=True).start()
RESET_HOUR_UTC = 9   # 04:00 US Eastern (standard time)
def _nightly():
    """Nightly reset of the shared demo matter so one visitor's coding never becomes the next visitor's starting point."""
    last = None
    while True:
        now = time.gmtime(); day = time.strftime("%Y-%m-%d", now)
        if now.tm_hour == RESET_HOUR_UTC and last != day:
            last = day
            for m in db.q("SELECT id FROM matters WHERE is_demo=1"):
                try: demo_reset(m["id"], actor="nightly-reset")
                except Exception: pass
        time.sleep(300)
def _ip(req): return (req.headers.get("x-forwarded-for") or (req.client.host if req.client else "?")).split(",")[0].strip()
def _rate(req):
    dq = _hits[_ip(req)]; now = time.time()
    while dq and dq[0] < now - RATE_WIN: dq.popleft()
    if len(dq) >= RATE_N: raise HTTPException(429, f"Upload rate limit: {RATE_N} per 10 minutes")
    dq.append(now)
def _matter(mid):
    m = db.one("SELECT * FROM matters WHERE id=?", (mid,))
    if not m: raise HTTPException(404, "no such matter")
    m["protocol"] = json.loads(m["protocol"] or "null"); return m
def _prefix(name):
    p = re.sub(r"[^A-Z]", "", name.upper())[:3] or "DOC"; return p.ljust(3, "X")
def create_matter(name, protocol=None, demo=False, prefix=None):
    mid = db.x("INSERT INTO matters(name,prefix,protocol,created,is_demo) VALUES(?,?,?,?,?)", (name, prefix or _prefix(name), json.dumps(protocol) if protocol else None, time.time(), int(demo)))
    db.audit("matter.created", mid, name=name, demo=demo); return mid
@app.get("/")
def index(): return FileResponse(APP_DIR / "static" / "index.html")
@app.get("/api/health")
def health(): return {"ok": True, "sandbox": sandbox.mode(), "scorer": scoring.scorer_model(), "limits": sandbox.LIMITS}
@app.get("/api/matters")
def matters():
    return db.q("""SELECT m.id, m.name, m.prefix, m.is_demo, m.created, (SELECT COUNT(*) FROM docs d WHERE d.matter_id=m.id) docs
                   FROM matters m ORDER BY m.id DESC""")
def _not_demo(mid, msg):
    m = _matter(mid)
    if m["is_demo"]: raise HTTPException(403, msg)
    return m
def demo_reset(mid, actor="visitor"):
    """Return the shared demo matter to its pristine state: drop visitor coding and QC samples. Evidence, parse results,
    scores and the audit chain are untouched (the reset itself is audited)."""
    n = db.one("SELECT COUNT(*) n FROM coding WHERE doc_id IN (SELECT id FROM docs WHERE matter_id=?)", (mid,))["n"]
    db.x("DELETE FROM coding WHERE doc_id IN (SELECT id FROM docs WHERE matter_id=?)", (mid,))
    db.x("UPDATE docs SET qc_sample=0 WHERE matter_id=?", (mid,))
    db.audit("demo.reset", mid, actor=actor, coding_rows_cleared=n); return n
@app.post("/api/matters/{mid}/demo/reset")
def demo_reset_api(mid: int, req: Request):
    m = _matter(mid)
    if not m["is_demo"]: raise HTTPException(400, "only the demo matter can be reset")
    _rate(req); return {"ok": True, "cleared": demo_reset(mid)}
@app.post("/api/matters/{mid}/protocol")
async def set_protocol(mid: int, req: Request):
    p = await req.json(); _not_demo(mid, "The shared demo matter's protocol is locked (re-scoring costs money and would change what the next judge sees). Upload your own documents to try a custom protocol.")
    issues = [{"id": (i.get("id") or f"I{n+1}")[:12], "name": str(i.get("name", ""))[:120], "description": str(i.get("description", ""))[:1500]}
              for n, i in enumerate(p.get("issues", [])[:12]) if i.get("name")]
    if not issues: raise HTTPException(400, "protocol needs at least one issue")
    pv = p.get("privilege", {}); L = lambda v: [s.strip() for s in (v.split(",") if isinstance(v, str) else v or []) if s.strip()][:60]
    proto = {"matter": str(p.get("matter", ""))[:200], "issues": issues, "date_range": p.get("date_range") or {},
             "privilege": {"attorneys": L(pv.get("attorneys")), "domains": L(pv.get("domains")), "terms": L(pv.get("terms"))}}
    db.x("UPDATE matters SET protocol=? WHERE id=?", (json.dumps(proto), mid)); db.audit("protocol.saved", mid, actor="reviewer", protocol=proto)
    if p.get("rescore"): db.x("DELETE FROM scores WHERE doc_id IN (SELECT id FROM docs WHERE matter_id=?)", (mid,))
    if not db.one("SELECT id FROM jobs WHERE matter_id=? AND status IN ('queued','running')", (mid,)): pipeline.new_job(mid, "score")
    return {"ok": True, "protocol": proto}
async def _save_upload(f: UploadFile, total):
    tmp = tempfile.NamedTemporaryFile(delete=False, dir=ROOT / "uploads"); n = 0
    try:
        while ch := await f.read(1 << 20):
            n += len(ch); total[0] += len(ch)
            if total[0] > MAX_UPLOAD: raise HTTPException(413, f"Upload exceeds {MAX_UPLOAD // 2**20} MB cap")
            tmp.write(ch)
        tmp.close(); return pathlib.Path(tmp.name)
    except BaseException:
        tmp.close(); os.unlink(tmp.name); raise
@app.post("/api/upload")
async def upload(req: Request, files: list[UploadFile] = File(...), matter: str = Form("Untitled matter"), custodian: str = Form(""),
                 matter_id: int = Form(0)):
    _rate(req); (ROOT / "uploads").mkdir(parents=True, exist_ok=True)
    if len(files) > 500: raise HTTPException(400, "max 500 files per upload; zip them instead")
    total = [0]; saved = []
    try:
        for f in files: saved.append((os.path.basename(f.filename or "file")[:200], await _save_upload(f, total)))
    except BaseException:
        for _, p in saved: p.unlink(missing_ok=True)
        raise
    if matter_id: _not_demo(matter_id, "Files cannot be added to the shared demo matter; upload them as a new matter.")
    mid = matter_id if matter_id and db.one("SELECT id FROM matters WHERE id=?", (matter_id,)) else create_matter(matter[:120] or "Untitled matter")
    up = uuid.uuid4().hex[:12]; cust = re.sub(r"[^\w.\- ]", "", custodian)[:60]
    for name, p in saved:
        sha, n = pipeline.store_evidence_file(p); p.unlink(missing_ok=True)
        pipeline.add_doc(mid, sha, n, name, name, cust, upload_id=up)
    db.audit("upload", mid, actor="reviewer", files=len(saved), bytes=total[0], ip=_ip(req), upload_id=up)
    pipeline.new_job(mid, "ingest"); return {"matter_id": mid, "files": len(saved), "bytes": total[0]}
@app.post("/api/demo/load")
def demo_load(req: Request):
    ex = db.one("SELECT id FROM matters WHERE is_demo=1 ORDER BY id LIMIT 1")
    if ex: return {"matter_id": ex["id"], "existing": True}   # one shared, locked demo matter; never re-ingested by visitors
    _rate(req)
    if not DEMO_ZIP.is_file(): raise HTTPException(404, "demo set not built on this server (run scripts/build_demo.py)")
    proto = json.loads(DEMO_PROTOCOL.read_text())
    mid = create_matter(proto.get("matter", "Enron demo"), proto, demo=True, prefix="ENR")
    sha, n = pipeline.store_evidence_file(DEMO_ZIP)
    pipeline.add_doc(mid, sha, n, DEMO_ZIP.name, DEMO_ZIP.name, "", upload_id="demo")
    pipeline.new_job(mid, "ingest"); return {"matter_id": mid}
@app.get("/api/matters/{mid}")
def matter_summary(mid: int):
    m = _matter(mid)
    c = db.one(f"""SELECT COUNT(*) total, SUM(depth=0) uploaded, SUM(status='queued' OR status='parsing') pending, SUM(status='container') containers,
        SUM(status='needs_manual') manual, SUM(status='parsed') parsed, SUM(status='parsed' AND dup_of IS NOT NULL) dups,
        SUM({pipeline.reviewable_sql('')}) reviewable, COUNT(DISTINCT CASE WHEN {pipeline.reviewable_sql('')} THEN thread_id END) threads,
        COUNT(DISTINCT neardup_group) neardup_groups, SUM(size) bytes FROM docs WHERE matter_id=?""", (mid,))
    s = db.one("""SELECT COUNT(*) scored, SUM(s.status='error') errors, SUM(tokens_in) tin, SUM(tokens_out) tout, SUM(relevance>=50) responsive,
        SUM(priv>=50) priv, SUM(json_extract(prescreen,'$.combined')>=50) privlog, SUM(hot) hot, MIN(scored) t0, MAX(scored) t1, SUM(seconds) secs FROM scores s JOIN docs d ON d.id=s.doc_id WHERE d.matter_id=?""", (mid,))
    k = db.one("SELECT COUNT(*) coded, SUM(responsive) resp, SUM(privileged) priv FROM coding c JOIN docs d ON d.id=c.doc_id WHERE d.matter_id=?", (mid,))
    jobs = db.q("SELECT * FROM jobs WHERE matter_id=? ORDER BY id", (mid,))
    cust = db.q(f"SELECT custodian, COUNT(*) n FROM docs WHERE matter_id=? AND {pipeline.reviewable_sql('')} GROUP BY custodian ORDER BY n DESC", (mid,))
    types = db.q("SELECT COALESCE(ftype,'?') ftype, COUNT(*) n FROM docs WHERE matter_id=? GROUP BY ftype ORDER BY n DESC", (mid,))
    pin, pout = float(os.getenv("PRICE_IN_PER_M", "0.10")), float(os.getenv("PRICE_OUT_PER_M", "0.35"))
    s["cost_est_usd"] = round(((s["tin"] or 0) * pin + (s["tout"] or 0) * pout) / 1e6, 4); s["price_basis"] = f"${pin}/M in, ${pout}/M out (configurable estimate)"
    return {"matter": m, "counts": c, "scores": s, "coding": k, "jobs": jobs, "custodians": cust, "types": types}
def _doc_rows(mid, where="", args=(), order="COALESCE(s.relevance,-1) DESC, d.id", limit=5000, offset=0):
    return db.q(f"""SELECT d.id, d.control, d.filename, d.path, d.custodian, d.ftype, d.status, d.status_reason, d.date_iso, d.email_from, d.email_to,
        d.subject, d.thread_id, d.neardup_group, d.neardup_sim, d.dup_of, d.size, d.sha256, s.status sstatus, s.relevance, s.priv, s.priv_basis, s.hot, s.rationale,
        s.issues, s.prescreen, c.responsive c_resp, c.privileged c_priv, c.hot c_hot, c.note c_note, d.qc_sample, json_extract(d.meta,'$.ocr') ocr
        FROM docs d LEFT JOIN scores s ON s.doc_id=d.id LEFT JOIN coding c ON c.doc_id=d.id WHERE d.matter_id=? {where} ORDER BY {order} LIMIT ? OFFSET ?""",
        (mid, *args, limit, offset))
@app.get("/api/matters/{mid}/docs")
def docs(mid: int, issue: str = "", priv: str = "", hot: int = 0, view: str = "queue", q: str = "", custodian: str = "", coded: str = "", limit: int = 200, offset: int = 0):
    w, a = [], []; order = "COALESCE(s.relevance,-1) DESC, d.id"
    if view == "manual": w.append("d.status='needs_manual'"); order = "d.id"
    elif view == "all": order = "d.id"
    elif view == "dups": w.append("d.dup_of IS NOT NULL"); order = "d.id"
    else: w.append(pipeline.reviewable_sql())
    if issue:
        w.append("json_extract(s.issues, '$.' || ?) >= 50"); a.append(issue); order = f"json_extract(s.issues, '$.' || ?) DESC, d.id"
    if priv == "model": w.append("s.priv >= 50")
    if priv == "prescreen": w.append("json_extract(s.prescreen,'$.score') >= 50")
    if priv == "either": w.append("(s.priv >= 50 OR json_extract(s.prescreen,'$.score') >= 50)")
    if priv == "log":
        w.append(f"(c.privileged=1 OR (c.privileged IS NULL AND json_extract(s.prescreen,'$.combined') >= {analytics.PRIV_LOG_THRESHOLD}))")
        if not issue: order = "json_extract(s.prescreen,'$.combined') DESC, d.id"
    if hot: w.append("s.hot=1")
    if custodian: w.append("d.custodian=?"); a.append(custodian)
    if coded == "yes": w.append("c.doc_id IS NOT NULL")
    if coded == "no": w.append("c.doc_id IS NULL")
    if q: w.append("(d.text LIKE ? OR d.subject LIKE ?)"); a += [f"%{q}%"] * 2
    where = "".join(" AND " + x for x in w)
    args = tuple(a) + ((issue,) if issue else ())
    rows = _doc_rows(mid, where, args, order, min(limit, 1000), offset)
    total = db.one(f"SELECT COUNT(*) n FROM docs d LEFT JOIN scores s ON s.doc_id=d.id LEFT JOIN coding c ON c.doc_id=d.id WHERE d.matter_id=? {where}", (mid, *a))["n"]
    for r in rows: r["issues"] = json.loads(r["issues"] or "{}"); r["prescreen"] = json.loads(r["prescreen"] or "null")
    return {"total": total, "rows": rows}
@app.get("/api/docs/{did}")
def doc(did: int):
    d = db.one("SELECT * FROM docs WHERE id=?", (did,))
    if not d: raise HTTPException(404)
    s = db.one("SELECT * FROM scores WHERE doc_id=?", (did,)) or {}
    for k in ("issues", "quotes", "prescreen"): s[k] = json.loads(s.get(k) or "null")
    for k in ("meta", "warnings"): d[k] = json.loads(d.get(k) or "null")
    d["score"] = s; d["coding"] = db.one("SELECT * FROM coding WHERE doc_id=?", (did,))
    d["thread"] = db.q("SELECT id, control, date_iso, email_from, subject FROM docs WHERE thread_id=? AND matter_id=? ORDER BY date_iso", (d["thread_id"], d["matter_id"])) if d["thread_id"] else []
    d["neardups"] = db.q("SELECT id, control, neardup_sim, subject FROM docs WHERE neardup_group=? AND id!=?", (d["neardup_group"], did)) if d["neardup_group"] else []
    d["family"] = db.q("SELECT id, control, filename, status FROM docs WHERE parent_id=? OR id=?", (did, d["parent_id"] or -1))
    d["dups"] = db.q("SELECT id, control, path FROM docs WHERE dup_of=?", (did,))
    d["custody"] = db.q("SELECT ts, actor, event, detail FROM audit WHERE doc_id=? ORDER BY id", (did,))
    return d
@app.post("/api/docs/{did}/code")
async def code(did: int, req: Request):
    b = await req.json(); d = db.one("SELECT matter_id FROM docs WHERE id=?", (did,))
    if not d: raise HTTPException(404)
    B = lambda k: None if b.get(k) is None else int(bool(b.get(k)))
    db.x("INSERT OR REPLACE INTO coding(doc_id,responsive,privileged,hot,note,coder,ts) VALUES(?,?,?,?,?,?,?)",
         (did, B("responsive"), B("privileged"), B("hot"), str(b.get("note", ""))[:2000], "reviewer", time.time()))
    db.audit("coding.saved", d["matter_id"], did, actor="reviewer", responsive=B("responsive"), privileged=B("privileged"), hot=B("hot"), note=bool(b.get("note")))
    return {"ok": True}
@app.get("/api/matters/{mid}/qc")
def qc(mid: int, cutoff: int = 50):
    rows = db.q(f"""SELECT d.id, s.relevance r, s.priv p, json_extract(s.prescreen,'$.combined') pc, c.responsive cr, c.privileged cp, d.qc_sample
                    FROM docs d JOIN scores s ON s.doc_id=d.id AND s.status='scored' LEFT JOIN coding c ON c.doc_id=d.id WHERE d.matter_id=? AND {pipeline.reviewable_sql()}""", (mid,))
    coded = [r for r in rows if r["cr"] is not None]
    tp = sum(1 for r in coded if r["r"] >= cutoff and r["cr"] == 1); fp = sum(1 for r in coded if r["r"] >= cutoff and r["cr"] == 0)
    fn = sum(1 for r in coded if r["r"] < cutoff and r["cr"] == 1); tn = sum(1 for r in coded if r["r"] < cutoff and r["cr"] == 0)
    samp = [r for r in rows if r["qc_sample"]]; samp_coded = [r for r in samp if r["cr"] is not None]
    below = [r for r in rows if r["r"] < cutoff]; above = [r for r in rows if r["r"] >= cutoff]
    el = None
    if samp_coded:
        k = sum(r["cr"] == 1 for r in samp_coded); rate = k / len(samp_coded); est_missed = rate * len(below)
        found = sum(1 for r in above if r["cr"] == 1) if any(r["cr"] is not None for r in above) else len(above)
        el = {"sample_size": len(samp), "coded": len(samp_coded), "responsive_found": k, "elusion_rate": round(rate, 4),
              "discard_pile": len(below), "est_missed": round(est_missed, 1), "found_above_cutoff": found,
              "found_basis": "human-confirmed responsive above cutoff" if any(r["cr"] is not None for r in above) else "model-predicted (no human coding above cutoff yet)",
              "recall_est": round(found / (found + est_missed), 4) if (found + est_missed) else None}
    pagree = sum(1 for r in coded if r["cp"] is not None and ((r["pc"] or 0) >= analytics.PRIV_LOG_THRESHOLD) == bool(r["cp"]))
    return {"cutoff": cutoff, "scored": len(rows), "above_cutoff": len(above), "below_cutoff": len(below), "coded": len(coded),
            "agreement": {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "rate": round((tp + tn) / len(coded), 4) if coded else None,
                          "precision": round(tp / (tp + fp), 4) if tp + fp else None, "priv_agree": pagree}, "elusion": el,
            "sample_ids": [r["id"] for r in samp]}
@app.post("/api/matters/{mid}/qc/sample")
async def qc_sample(mid: int, req: Request):
    b = await req.json(); n = max(1, min(int(b.get("n", 30)), 500)); cutoff = int(b.get("cutoff", 50))
    pool = [r["id"] for r in db.q(f"SELECT d.id FROM docs d JOIN scores s ON s.doc_id=d.id WHERE d.matter_id=? AND s.status='scored' AND s.relevance<? AND {pipeline.reviewable_sql()}", (mid, cutoff))]
    seed = int(time.time()); pick = random.Random(seed).sample(pool, min(n, len(pool)))
    db.x("UPDATE docs SET qc_sample=0 WHERE matter_id=?", (mid,))
    for i in pick: db.x("UPDATE docs SET qc_sample=1 WHERE id=?", (i,))
    db.audit("qc.elusion_sample", mid, actor="reviewer", n=len(pick), pool=len(pool), cutoff=cutoff, seed=seed)
    return {"sampled": len(pick), "pool": len(pool), "seed": seed}
@app.post("/api/matters/{mid}/custody/verify")
def custody_verify(mid: int): _matter(mid); return {**pipeline.verify_hashes(mid), "audit_chain": db.verify_chain()}
@app.get("/api/matters/{mid}/custody")
def custody(mid: int):
    ing = db.one("SELECT COUNT(*) n FROM audit WHERE matter_id=? AND event='ingest.received'", (mid,))["n"]
    last = db.one("SELECT ts, actor, detail FROM audit WHERE matter_id=? AND event='custody.rehash' ORDER BY id DESC LIMIT 1", (mid,))
    sb = db.q("SELECT event, detail FROM audit WHERE matter_id=? AND event='parse.sandbox' ORDER BY id", (mid,))
    outcomes = defaultdict(int)
    for r in sb: outcomes[json.loads(r["detail"])["outcome"].split(";")[0].split(":")[0][:80]] += 1
    return {"hashed_at_ingest": ing, "last_rehash": last and {**json.loads(last["detail"]), "ts": last["ts"], "actor": last["actor"]},
            "sandbox_runs": len(sb), "sandbox_outcomes": outcomes, "audit_chain": db.verify_chain(), "limits": sandbox.LIMITS, "sandbox_mode": sandbox.mode()}
@app.get("/api/matters/{mid}/audit")
def audit_log(mid: int, limit: int = 300, event: str = ""):
    return db.q("SELECT * FROM audit WHERE matter_id=? AND event LIKE ? ORDER BY id DESC LIMIT ?", (mid, (event or "") + "%", min(limit, 5000)))
def _csv(name, header, rows):
    buf = io.StringIO(); w = csv.writer(buf); w.writerow(header); w.writerows(rows)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv", headers={"content-disposition": f'attachment; filename="{name}"'})
@app.get("/api/matters/{mid}/export/coding.csv")
def export_coding(mid: int):
    m = _matter(mid); ids = [i["id"] for i in (m["protocol"] or {}).get("issues", [])]
    rows = []
    for r in _doc_rows(mid, f"AND {pipeline.reviewable_sql()}", order="d.id", limit=10**6):
        iss = json.loads(r["issues"] or "{}"); YN = lambda v: "" if v is None else ("Y" if v else "N")
        rows.append([r["control"], r["custodian"], r["path"], r["sha256"], r["date_iso"], r["email_from"], r["subject"], r["relevance"],
                     *[iss.get(i) for i in ids], r["priv"], r["priv_basis"], (json.loads(r["prescreen"] or "{}") or {}).get("score"), "Y" if r["hot"] else "",
                     YN(r["c_resp"]), YN(r["c_priv"]), YN(r["c_hot"]), r["c_note"], r["rationale"]])
    db.audit("export.coding", mid, actor="reviewer", rows=len(rows))
    return _csv(f"{m['prefix']}_coding.csv", ["Control", "Custodian", "Path", "SHA256", "Date", "From", "Subject", "Model relevance",
                *[f"Model {i}" for i in ids], "Model privilege", "Model priv basis", "Prescreen priv", "Model hot", "Human responsive",
                "Human privileged", "Human hot", "Note", "Model rationale"], rows)
@app.get("/api/matters/{mid}/export/privlog.csv")
def export_privlog(mid: int):
    """Draft privilege log: metadata + basis only, never content. Includes human-coded privileged docs, else combined model+pre-screen score >= PRIV_LOG_THRESHOLD, marked DRAFT."""
    m = _matter(mid); rows = []
    for r in db.q("""SELECT d.*, s.priv, s.priv_basis, json_extract(s.prescreen,'$.combined') pc, s.prescreen pre, c.privileged cp FROM docs d LEFT JOIN scores s ON s.doc_id=d.id
                     LEFT JOIN coding c ON c.doc_id=d.id WHERE d.matter_id=? AND d.dup_of IS NULL AND (c.privileged=1 OR (c.privileged IS NULL AND pc>=?)) ORDER BY d.date_iso""", (mid, analytics.PRIV_LOG_THRESHOLD)):
        basis = {"attorney-client": "Attorney-Client Privilege", "work product": "Attorney Work Product", "both": "Attorney-Client Privilege; Work Product"}.get(r["priv_basis"] or "", "Attorney-Client Privilege (to be confirmed)")
        desc = {"eml": "Email", "msg": "Email", "pdf": "PDF document", "docx": "Word document", "xlsx": "Spreadsheet", "csv": "Spreadsheet"}.get(r["ftype"], "Document")
        rows.append([r["control"], (r["date_iso"] or "")[:10], r["email_from"] or r["custodian"], r["email_to"], r["email_cc"],
                     f"{desc} reflecting request for or rendition of legal advice" if "Client" in basis else f"{desc} prepared in anticipation of litigation",
                     basis, r["pc"], "; ".join(sorted({h["kind"] for h in (json.loads(r["pre"] or "{}") or {}).get("hits", []) if h.get("points", 0) > 0})),
                     "Human-coded" if r["cp"] == 1 else "DRAFT - model + pre-screen suggestion, needs attorney review"])
    db.audit("export.privlog", mid, actor="reviewer", rows=len(rows))
    return _csv(f"{m['prefix']}_privilege_log_DRAFT.csv", ["Control", "Date", "Author", "Recipients", "CC", "Description (no content disclosed)", "Privilege asserted", "Combined score", "Screen signals", "Status"], rows)
@app.get("/api/matters/{mid}/export/audit.csv")
def export_audit(mid: int):
    rows = [[r["id"], time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(r["ts"])), r["actor"], r["event"], r["doc_id"], r["detail"], r["prev"], r["hash"]]
            for r in db.q("SELECT * FROM audit WHERE matter_id=? ORDER BY id", (mid,))]
    return _csv(f"matter{mid}_audit.csv", ["Seq", "UTC", "Actor", "Event", "DocID", "Detail", "PrevHash", "Hash"], rows)
