"""Background job queue: ingest (parse every file in its own sandbox) -> analytics -> scoring. State lives in SQLite,
so a restart resumes where it stopped."""
import hashlib, shutil, json, os, pathlib, threading, time, traceback
from concurrent.futures import ThreadPoolExecutor
from . import db, sandbox, analytics, scoring
EVID = sandbox.EVID
PARSE_WORKERS = int(os.getenv("PARSE_WORKERS", 3)); SCORE_WORKERS = int(os.getenv("SCORE_WORKERS", 8)); MAX_DEPTH = 4
_wake = threading.Event(); _started = False
def store_evidence(data: bytes) -> str:
    sha = hashlib.sha256(data).hexdigest(); p = EVID / sha
    if not p.exists():
        EVID.mkdir(parents=True, exist_ok=True); tmp = EVID / f".tmp-{sha}-{os.getpid()}-{threading.get_ident()}"
        tmp.write_bytes(data); os.chmod(tmp, 0o444); os.replace(tmp, p)
    return sha
def store_evidence_file(src: pathlib.Path) -> tuple:
    h = hashlib.sha256(); n = 0
    with open(src, "rb") as f:
        for ch in iter(lambda: f.read(1 << 20), b""): h.update(ch); n += len(ch)
    sha = h.hexdigest(); p = EVID / sha
    if not p.exists():
        EVID.mkdir(parents=True, exist_ok=True); tmp = EVID / f".tmp-{sha}"; shutil.copyfile(src, tmp)
        os.chmod(tmp, 0o444); os.replace(tmp, p)
    return sha, n
def next_control(mid):
    m = db.one("SELECT prefix FROM matters WHERE id=?", (mid,)); n = db.one("SELECT COUNT(*) c FROM docs WHERE matter_id=?", (mid,))["c"]
    return f"{m['prefix']}-{n + 1:06d}"
def add_doc(mid, sha, size, filename, path, custodian, parent_id=None, depth=0, upload_id=None):
    with db._lock:
        did = db.x("INSERT INTO docs(matter_id,control,parent_id,depth,sha256,size,filename,path,custodian,status,ingested,upload_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                   (mid, next_control(mid), parent_id, depth, sha, size, filename, path, custodian, "queued", time.time(), upload_id))
    db.audit("ingest.received", mid, did, filename=filename, path=path, sha256=sha, bytes=size, custodian=custodian, parent=parent_id)
    return did
def hint_for(name):
    e = pathlib.Path(name).suffix.lower().lstrip(".")
    return e if e.isalnum() and len(e) <= 8 else ""
def new_job(mid, kind):
    jid = db.x("INSERT INTO jobs(matter_id,kind,status,created,updated) VALUES(?,?,?,?,?)", (mid, kind, "queued", time.time(), time.time()))
    db.audit(f"job.{kind}.queued", mid, job=jid); _wake.set(); return jid
def _upd(jid, **kw):
    kw["updated"] = time.time(); db.x(f"UPDATE jobs SET {','.join(k + '=?' for k in kw)} WHERE id=?", (*kw.values(), jid))
def reason_for(info, res):
    if info.get("timed_out"): return f"Parser exceeded the {sandbox.LIMITS['timeout_s']} s time limit; container killed"
    if info.get("out_capped"): return "Parser output exceeded the 300 MB scratch cap; output discarded"
    if info.get("exit_code") in (137, -9) or "MemoryError" in (info.get("stderr") or ""): return "Parser exceeded the 512 MB memory limit; container killed (OOM)"
    if res is None: return f"Parser crashed (exit {info.get('exit_code')}): {(info.get('stderr') or '').strip()[-200:]}"
    if res.get("status") == "capped": return f"Safety cap hit: {res.get('error')}"
    if res.get("status") == "error": return f"Could not parse: {res.get('error')}"
    return None
def parse_one(d):
    mid = d["matter_id"]; db.x("UPDATE docs SET status='parsing' WHERE id=?", (d["id"],))
    info, res, kids = sandbox.parse(d["sha256"], hint_for(d["filename"]))
    why = reason_for(info, res)
    db.audit("parse.sandbox", mid, d["id"], container=info.get("container"), exit_code=info.get("exit_code"), timed_out=info.get("timed_out"),
             destroyed=info.get("destroyed"), seconds=info.get("seconds"), outcome=why or "ok")
    if why and not (res and res.get("status") == "ok"):
        db.x("UPDATE docs SET status='needs_manual', status_reason=?, ftype=?, parse_seconds=?, container=? WHERE id=?",
             (why, (res or {}).get("type") or hint_for(d["filename"]) or "unknown", info.get("seconds"), info.get("container"), d["id"]))
        return False
    m = res.get("meta") or {}; t = res.get("type"); container = t in ("zip", "mbox")
    db.x("""UPDATE docs SET status=?, status_reason=?, ftype=?, is_container=?, meta=?, text=?, text_truncated=?, warnings=?, date_iso=?,
            email_from=?, email_to=?, email_cc=?, subject=?, parse_seconds=?, container=? WHERE id=?""",
         ("container" if container else "parsed", None, t, int(container), json.dumps(m), res.get("text") or "", int(bool(res.get("text_truncated"))),
          json.dumps(res.get("warnings") or []), analytics.to_iso(m.get("date")), m.get("from") or m.get("author") or "", m.get("to") or "",
          m.get("cc") or "", m.get("subject") or m.get("title") or "", info.get("seconds"), info.get("container"), d["id"]))
    for name, data in kids:
        if d["depth"] + 1 > MAX_DEPTH:
            cid = add_doc(mid, store_evidence(data), len(data), pathlib.Path(name).name, f"{d['path']}/{name}", d["custodian"], d["id"], d["depth"] + 1, d["upload_id"])
            db.x("UPDATE docs SET status='needs_manual', status_reason=? WHERE id=?", (f"Nested container depth > {MAX_DEPTH}; not expanded", cid)); continue
        cust = d["custodian"]
        parts = name.split("/")
        if d["parent_id"] is None and container and len(parts) > 1 and not d["custodian"]: cust = parts[0]
        add_doc(mid, store_evidence(data), len(data), parts[-1], f"{d['path']}/{name}", cust, d["id"], d["depth"] + 1, d["upload_id"])
    return True
def analytics_pass(mid):
    docs = db.q("SELECT id, text, subject, email_from, date_iso, meta, ftype, sha256 FROM docs WHERE matter_id=? AND status='parsed' ORDER BY id", (mid,))
    db.x("UPDATE docs SET dup_of=NULL, dup_reason=NULL, thread_id=NULL, neardup_group=NULL, neardup_sim=NULL WHERE matter_id=?", (mid,))
    seen_sha, seen_c = {}, {}; ndup = 0
    for d in docs:
        ch = analytics.content_hash(d) if d["ftype"] in ("eml", "msg") else None
        if d["sha256"] in seen_sha: db.x("UPDATE docs SET dup_of=?, dup_reason='exact duplicate (SHA-256)', content_hash=? WHERE id=?", (seen_sha[d["sha256"]], ch, d["id"])); ndup += 1; continue
        seen_sha[d["sha256"]] = d["id"]
        if ch and ch in seen_c: db.x("UPDATE docs SET dup_of=?, dup_reason='duplicate message (same sender, date, subject, body)', content_hash=? WHERE id=?", (seen_c[ch], ch, d["id"])); ndup += 1; continue
        if ch: seen_c[ch] = d["id"]; db.x("UPDATE docs SET content_hash=? WHERE id=?", (ch, d["id"]))
    uniq = [d for d in docs if d["sha256"] in seen_sha and seen_sha[d["sha256"]] == d["id"] and (not d["ftype"] in ("eml","msg") or seen_c.get(analytics.content_hash(d)) == d["id"])]
    # threading: In-Reply-To / References, else normalised subject
    byid, key2thread = {}, {}
    for d in uniq:
        m = json.loads(d["meta"] or "{}")
        if m.get("message_id"): byid[m["message_id"]] = d["id"]
    for d in uniq:
        if d["ftype"] not in ("eml", "msg"): continue
        m = json.loads(d["meta"] or "{}"); refs = (m.get("references", "") + " " + m.get("in_reply_to", "")).split()
        tid = next((byid[r] for r in refs if r in byid), None)
        k = analytics.norm_subject(d["subject"])
        if tid is None and len(k) > 3: tid = key2thread.setdefault(k, d["id"])
        elif len(k) > 3: key2thread.setdefault(k, tid)
        db.x("UPDATE docs SET thread_id=? WHERE id=?", (tid or d["id"], d["id"]))
    groups = analytics.near_dup_groups([(d["id"], d["text"]) for d in uniq])
    for i, (g, s) in groups.items(): db.x("UPDATE docs SET neardup_group=?, neardup_sim=? WHERE id=?", (g, s, i))
    db.audit("analytics.done", mid, unique=len(uniq), duplicates=ndup, near_dup_docs=len(groups),
             threads=len({t["thread_id"] for t in db.q("SELECT thread_id FROM docs WHERE matter_id=? AND thread_id IS NOT NULL", (mid,))}))
def run_ingest(job):
    mid = job["matter_id"]; _upd(job["id"], status="running", started=time.time(), message="parsing files in sandboxes")
    with ThreadPoolExecutor(PARSE_WORKERS) as ex:
        while True:
            batch = db.q("SELECT * FROM docs WHERE matter_id=? AND status='queued' ORDER BY id LIMIT 30", (mid,))
            if not batch: break
            for d in batch: db.x("UPDATE docs SET status='parsing' WHERE id=?", (d["id"],))
            list(ex.map(_safe_parse, batch))
            c = db.one("SELECT COUNT(*) n, SUM(status IN ('parsed','container','needs_manual')) done, SUM(status='needs_manual') bad FROM docs WHERE matter_id=?", (mid,))
            _upd(job["id"], total=c["n"], done=c["done"] or 0, failed=c["bad"] or 0)
    _upd(job["id"], message="deduplication, threading, near-duplicates"); analytics_pass(mid)
    after = verify_hashes(mid, actor="system (post-ingest)")
    _upd(job["id"], status="done", finished=time.time(), message=f"ingest complete; evidence re-hash: {after['matched']}/{after['checked']} match")
    m = db.one("SELECT protocol FROM matters WHERE id=?", (mid,))
    if m and m["protocol"]: new_job(mid, "score")
def _safe_parse(d):
    try: return parse_one(d)
    except Exception as e:
        db.x("UPDATE docs SET status='needs_manual', status_reason=? WHERE id=?", (f"Internal error: {e!r}"[:300], d["id"]))
        db.audit("parse.error", d["matter_id"], d["id"], error=repr(e)[:300])
def reviewable_sql(a="d."): return f"{a}status='parsed' AND {a}dup_of IS NULL AND {a}is_container=0"
def run_score(job):
    mid = job["matter_id"]; protocol = json.loads(db.one("SELECT protocol FROM matters WHERE id=?", (mid,))["protocol"] or "{}")
    scorer = scoring.get_scorer()
    todo = db.q(f"SELECT d.* FROM docs d LEFT JOIN scores s ON s.doc_id=d.id WHERE d.matter_id=? AND {reviewable_sql()} AND (s.status IS NULL OR s.status!='scored') ORDER BY d.id", (mid,))
    _upd(job["id"], status="running", started=time.time(), total=len(todo), done=0, message=f"scoring with {scorer.model}")
    db.audit("score.start", mid, model=scorer.model, docs=len(todo), sent="extracted text only (max %d chars/doc)" % scoring.DOC_CHARS)
    done = [0, 0]; lk = threading.Lock()
    def one(d):
        t0 = time.time(); pre = analytics.prescreen(d, protocol)
        try:
            r = scorer.score(d, protocol); st, err = "scored", None
        except Exception as e:
            r, st, err = {}, "error", str(e)[:300]
        db.x("""INSERT OR REPLACE INTO scores(doc_id,status,issues,relevance,priv,priv_basis,priv_reason,hot,rationale,quotes,prescreen,model,tokens_in,tokens_out,seconds,error,attempts,scored)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
             (d["id"], st, json.dumps(r.get("issues", {})), r.get("relevance"), r.get("priv"), r.get("priv_basis"), r.get("priv_reason"), int(bool(r.get("hot"))),
              r.get("rationale"), json.dumps(r.get("quotes", [])), json.dumps(pre), getattr(scorer, "model", "?"), r.get("tokens_in", 0), r.get("tokens_out", 0),
              round(time.time() - t0, 2), err, r.get("attempts"), time.time()))
        qs = r.get("quotes", [])
        db.audit("score.doc", mid, d["id"], status=st, relevance=r.get("relevance"), priv=r.get("priv"), quotes=len(qs),
                 quotes_verified=sum(q["verified"] for q in qs), error=err)
        with lk:
            done[0] += 1; done[1] += st != "scored"; _upd(job["id"], done=done[0], failed=done[1])
    with ThreadPoolExecutor(SCORE_WORKERS) as ex: list(ex.map(one, todo))
    _upd(job["id"], status="done", finished=time.time(), message=f"scored {done[0]-done[1]} of {done[0]}")
    db.audit("score.done", mid, scored=done[0] - done[1], failed=done[1])
def verify_hashes(mid, actor="reviewer"):
    rows = db.q("SELECT id, sha256 FROM docs WHERE matter_id=?", (mid,)); bad = []; cache = {}
    for r in rows:
        if r["sha256"] not in cache:
            h = hashlib.sha256(); p = EVID / r["sha256"]
            try:
                with open(p, "rb") as f:
                    for ch in iter(lambda: f.read(1 << 20), b""): h.update(ch)
                cache[r["sha256"]] = h.hexdigest()
            except FileNotFoundError: cache[r["sha256"]] = None
        if cache[r["sha256"]] != r["sha256"]: bad.append(r["id"])
    res = {"checked": len(rows), "matched": len(rows) - len(bad), "mismatched": bad[:50], "ts": time.time()}
    db.audit("custody.rehash", mid, actor=actor, **res); return res
def worker():
    while True:
        job = db.one("SELECT * FROM jobs WHERE status IN ('queued','running') ORDER BY id LIMIT 1")
        if not job: _wake.wait(5); _wake.clear(); continue
        try: (run_ingest if job["kind"] == "ingest" else run_score)(job)
        except Exception as e:
            traceback.print_exc(); _upd(job["id"], status="failed", message=repr(e)[:300]); db.audit("job.failed", job["matter_id"], job=job["id"], error=repr(e)[:300])
def start():
    global _started
    if _started: return
    _started = True; db.conn(); db.x("UPDATE docs SET status='queued' WHERE status='parsing'")
    threading.Thread(target=worker, daemon=True).start()
