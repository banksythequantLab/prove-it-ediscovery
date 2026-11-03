import io, json, time, zipfile
from fastapi.testclient import TestClient
from app import main, db, analytics, scoring
EML = lambda i, subj, body, frm="kenneth.lay@enron.com": (f"Message-ID: <{i}@x>\nDate: Mon, 14 May 2001 16:39:00 -0700\nFrom: {frm}\nTo: jeff.skilling@enron.com\n"
                                 f"Subject: {subj}\n\n{body}\n").encode()
PROTO = {"matter": "T", "issues": [{"id": "CA", "name": "California trading", "description": "california power price manipulation"}],
         "privilege": {"attorneys": ["Sara Shackleton"], "domains": ["velaw.com"], "terms": ["privileged"]}}
def test_quote_verification():
    d = {"text": "We will get that language\nto you by Friday.", "subject": "ISDA"}
    q = scoring.verify_quotes([{"for": "CA", "text": "get that language to you"}, {"for": "CA", "text": "invented sentence"}], d)
    assert q[0]["verified"] and d["text"][q[0]["start"]:q[0]["end"]] == "get that language\nto you"
    assert not q[1]["verified"] and q[1]["start"] is None
def test_prescreen():
    p = analytics.prescreen({"email_from": "sara.shackleton@enron.com", "text": "This is privileged legal advice on the swap."}, PROTO); assert p["score"] >= 50, p
    assert analytics.prescreen({"email_from": "sara.shackleton@enron.com", "text": "Lunch at noon? This e-mail may contain confidential or privileged information."}, PROTO)["score"] < 50
    assert analytics.prescreen({"text": "lunch?"}, PROTO)["score"] == 0
def test_near_dup_and_subject():
    a = " ".join(f"word{i}" for i in range(200))
    g = analytics.near_dup_groups([(1, a), (2, a + " extra words here"), (3, "totally different text " * 30)])
    assert g[1][0] == g[2][0] and 3 not in g
    assert analytics.norm_subject("RE: Fw: RE: Price caps") == "price caps"
def test_audit_chain_detects_tamper():
    db.audit("t.one", None, x=1); db.audit("t.two", None, x=2); assert db.verify_chain()["ok"]
    last = db.one("SELECT id FROM audit ORDER BY id DESC LIMIT 1")["id"]
    db.x("UPDATE audit SET detail='{}' WHERE id=?", (last,)); assert not db.verify_chain()["ok"]
    db.x("DELETE FROM audit")
def _wait(c, mid, kinds=2, t=120):
    end = time.time() + t
    while time.time() < end:
        s = c.get(f"/api/matters/{mid}").json()
        if len(s["jobs"]) >= kinds and all(j["status"] in ("done", "failed") for j in s["jobs"]): return s
        time.sleep(0.5)
    raise AssertionError("timeout")
def test_end_to_end_upload():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("lay-k/inbox/1.eml", EML(1, "California price caps", "Our California power trading strategy and price caps."))
        z.writestr("lay-k/inbox/1_copy.eml", EML(1, "California price caps", "Our California power trading strategy and price caps."))
        z.writestr("lay-k/inbox/2.eml", EML(2, "Lunch", "Lunch on friday?"))
        z.writestr("shackleton-s/legal/3.eml", EML(3, "ISDA", "This is privileged legal advice.", "sara.shackleton@enron.com"))
        z.writestr("junk/broken.pdf", b"%PDF-1.4 this is not really a pdf")
        z.writestr("junk/notes.txt", b"plain notes about california")
        inner = io.BytesIO(); zi = zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED); zi.writestr("z.bin", b"\0" * (30 << 20)); zi.close()
        z.writestr("junk/bomb.zip", inner.getvalue())
    with TestClient(main.app) as c:
        r = c.post("/api/upload", files=[("files", ("set.zip", buf.getvalue()))], data={"matter": "Test matter"}); assert r.status_code == 200
        mid = r.json()["matter_id"]
        assert c.post(f"/api/matters/{mid}/protocol", json=PROTO).status_code == 200
        s = _wait(c, mid)
        assert s["counts"]["dups"] == 1, s["counts"]
        man = c.get(f"/api/matters/{mid}/docs?view=manual").json()["rows"]
        reasons = {m["filename"]: m["status_reason"] for m in man}
        assert "zip-bomb" in reasons["bomb.zip"] and "broken.pdf" in reasons, reasons
        q = c.get(f"/api/matters/{mid}/docs").json()["rows"]
        assert q[0]["subject"] == "California price caps" and q[0]["custodian"] == "lay-k"
        priv = c.get(f"/api/matters/{mid}/docs?priv=prescreen").json()["rows"]; assert [p["subject"] for p in priv] == ["ISDA"], priv
        assert c.post(f"/api/docs/{q[0]['id']}/code", json={"responsive": 1}).status_code == 200
        cv = c.post(f"/api/matters/{mid}/custody/verify").json(); assert cv["matched"] == cv["checked"] and cv["audit_chain"]["ok"]
        assert c.post(f"/api/docs/{priv[0]['id']}/code", json={"privileged": 1}).status_code == 200
        pl = c.get(f"/api/matters/{mid}/export/privlog.csv").text; rows = pl.strip().splitlines()
        assert len(rows) == 2 and "Human-coded" in rows[1] and "ISDA" not in pl and "privileged legal advice" not in pl, pl
        assert "California price caps" in c.get(f"/api/matters/{mid}/export/coding.csv").text
        assert "coding.saved" in c.get(f"/api/matters/{mid}/export/audit.csv").text
        assert c.post(f"/api/matters/{mid}/qc/sample", json={"n": 5, "cutoff": 50}).json()["sampled"] >= 1
def test_upload_cap(monkeypatch):
    monkeypatch.setattr(main, "MAX_UPLOAD", 1000)
    with TestClient(main.app) as c:
        assert c.post("/api/upload", files=[("files", ("big.txt", b"x" * 5000))]).status_code == 413
def test_demo_matter_locked_and_resettable():
    from fastapi.testclient import TestClient
    from app import main
    with TestClient(main.app) as c:
        mid = main.create_matter("Demo", PROTO, demo=True, prefix="DEM")
        assert c.post(f"/api/matters/{mid}/protocol", json=PROTO).status_code == 403
        r = c.post("/api/upload", files={"files": ("a.txt", b"hi")}, data={"matter_id": str(mid)}); assert r.status_code == 403
        assert c.post("/api/demo/load").json() == {"matter_id": mid, "existing": True}
        assert c.post(f"/api/matters/{mid}/demo/reset").json()["ok"]
        assert db.one("SELECT event FROM audit ORDER BY id DESC LIMIT 1")["event"] == "demo.reset"
