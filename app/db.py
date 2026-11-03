"""SQLite persistence (on a Docker volume) + hash-chained audit log."""
import hashlib, json, os, pathlib, sqlite3, threading, time
ROOT = pathlib.Path(os.getenv("PI_ROOT", "/var/lib/prove-it"))
DB_PATH = pathlib.Path(os.getenv("PI_DB", ROOT / "db" / "proveit.db"))
_lock = threading.RLock(); _conn = None
SCHEMA = """
CREATE TABLE IF NOT EXISTS matters(id INTEGER PRIMARY KEY, name TEXT, prefix TEXT, protocol TEXT, created REAL, is_demo INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS docs(id INTEGER PRIMARY KEY, matter_id INTEGER, control TEXT, parent_id INTEGER, depth INTEGER DEFAULT 0,
  sha256 TEXT, size INTEGER, filename TEXT, path TEXT, custodian TEXT, ftype TEXT, is_container INTEGER DEFAULT 0,
  status TEXT DEFAULT 'queued', status_reason TEXT, warnings TEXT, parse_seconds REAL, container TEXT,
  meta TEXT, text TEXT, text_truncated INTEGER DEFAULT 0, date_iso TEXT, email_from TEXT, email_to TEXT, email_cc TEXT, subject TEXT,
  dup_of INTEGER, dup_reason TEXT, content_hash TEXT, thread_id INTEGER, neardup_group INTEGER, neardup_sim REAL,
  qc_sample INTEGER DEFAULT 0, ingested REAL, upload_id TEXT);
CREATE INDEX IF NOT EXISTS docs_m ON docs(matter_id, status);
CREATE TABLE IF NOT EXISTS scores(doc_id INTEGER PRIMARY KEY, status TEXT, issues TEXT, relevance INTEGER, priv INTEGER, priv_basis TEXT,
  priv_reason TEXT, hot INTEGER, rationale TEXT, quotes TEXT, prescreen TEXT, model TEXT, tokens_in INTEGER, tokens_out INTEGER,
  seconds REAL, error TEXT, attempts INTEGER, scored REAL);
CREATE TABLE IF NOT EXISTS coding(doc_id INTEGER PRIMARY KEY, responsive INTEGER, privileged INTEGER, hot INTEGER, note TEXT, coder TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY, matter_id INTEGER, kind TEXT, status TEXT, total INTEGER DEFAULT 0, done INTEGER DEFAULT 0,
  failed INTEGER DEFAULT 0, message TEXT, created REAL, updated REAL, started REAL, finished REAL);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, ts REAL, matter_id INTEGER, actor TEXT, event TEXT, doc_id INTEGER, detail TEXT, prev TEXT, hash TEXT);
"""
def conn():
    global _conn
    with _lock:
        if _conn is None:
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
            _conn.row_factory = sqlite3.Row; _conn.execute("PRAGMA journal_mode=WAL"); _conn.executescript(SCHEMA)
        return _conn
def q(sql, args=()):
    with _lock: return [dict(r) for r in conn().execute(sql, args).fetchall()]
def one(sql, args=()):
    r = q(sql, args); return r[0] if r else None
def x(sql, args=()):
    with _lock: c = conn().execute(sql, args); return c.lastrowid
def audit(event, matter_id=None, doc_id=None, actor="system", **detail):
    with _lock:
        prev = (one("SELECT hash FROM audit ORDER BY id DESC LIMIT 1") or {}).get("hash") or "GENESIS"
        ts = time.time(); d = json.dumps(detail, sort_keys=True, default=str)
        h = hashlib.sha256(f"{prev}|{ts:.6f}|{matter_id}|{actor}|{event}|{doc_id}|{d}".encode()).hexdigest()
        x("INSERT INTO audit(ts,matter_id,actor,event,doc_id,detail,prev,hash) VALUES(?,?,?,?,?,?,?,?)", (ts, matter_id, actor, event, doc_id, d, prev, h))
def verify_chain():
    prev = "GENESIS"; n = 0
    for r in q("SELECT * FROM audit ORDER BY id"):
        h = hashlib.sha256(f"{prev}|{r['ts']:.6f}|{r['matter_id']}|{r['actor']}|{r['event']}|{r['doc_id']}|{r['detail']}".encode()).hexdigest()
        if r["prev"] != prev or r["hash"] != h: return {"ok": False, "entries": n, "broken_at": r["id"]}
        prev = h; n += 1
    return {"ok": True, "entries": n, "head": prev}
