"""Deterministic analytics: date normalisation, exact/near duplicate detection, email threading, privilege pre-screen."""
import email.utils, hashlib, re
from datetime import timezone
def to_iso(s):
    if not s: return None
    s = str(s).strip()
    try:
        d = email.utils.parsedate_to_datetime(s)
        if d: return (d.astimezone(timezone.utc) if d.tzinfo else d).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception: pass
    m = re.match(r"D:(\d{4})(\d{2})(\d{2})", s) or re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None
def norm_subject(s):
    s = (s or "").strip()
    while True:
        n = re.sub(r"^\s*(re|fw|fwd|aw|wg)\s*(\[\d+\])?\s*:\s*", "", s, flags=re.I)
        if n == s: break
        s = n
    return re.sub(r"\s+", " ", s).strip().lower()
def body_core(text):
    """Body without quoted history, for content dedupe."""
    t = re.split(r"\n-+\s*Original Message\s*-+|\n-+\s*Forwarded by|\n>", text or "", maxsplit=1)[0]
    return re.sub(r"\s+", " ", t).strip().lower()
def content_hash(d):
    k = "|".join([(d.get("email_from") or "").lower(), d.get("date_iso") or "", norm_subject(d.get("subject")), body_core(d.get("text"))])
    return hashlib.sha256(k.encode()).hexdigest()
def shingles(text, k=5):
    w = re.findall(r"[a-z0-9]+", (text or "")[:20000].lower())
    return {" ".join(w[i:i + k]) for i in range(max(0, len(w) - k + 1))}
def near_dup_groups(docs, thr=0.8):
    """docs: [(id, text)] -> {id: (group_id, best_sim)} for docs that have at least one near-duplicate. Pairwise Jaccard
    with a cheap length prefilter (fine for thousands of docs; use MinHash/LSH beyond that)."""
    S = [(i, shingles(t)) for i, t in docs]; S = [(i, s) for i, s in S if len(s) >= 8]
    parent = {i: i for i, _ in S}; best = {}
    def f(a):
        while parent[a] != a: parent[a] = parent[parent[a]]; a = parent[a]
        return a
    S.sort(key=lambda z: len(z[1]))
    for a in range(len(S)):
        ia, sa = S[a]
        for b in range(a + 1, len(S)):
            ib, sb = S[b]
            if len(sa) < thr * len(sb): break
            inter = len(sa & sb)
            if inter == 0: continue
            j = inter / (len(sa) + len(sb) - inter)
            if j >= thr:
                parent[f(ib)] = f(ia); best[ia] = max(best.get(ia, 0), j); best[ib] = max(best.get(ib, 0), j)
    return {i: (f(i), round(best[i], 3)) for i in best}
def prescreen(doc, protocol):
    """Cheap deterministic privilege screen from the protocol's signals. Returns {score, hits:[{kind, term}]}"""
    P = protocol.get("privilege", {}) if protocol else {}
    hay_hdr = " ".join([doc.get("email_from") or "", doc.get("email_to") or "", doc.get("email_cc") or ""]).lower()
    hay = (hay_hdr + " " + (doc.get("subject") or "") + " " + (doc.get("text") or "")[:200000]).lower()
    hits = []
    for name in P.get("attorneys", []):
        n = name.strip().lower()
        if not n: continue
        last = n.split()[-1]; first = n.split()[0]
        pat = re.escape(n) + "|" + re.escape(last) + r",?\s+" + re.escape(first) + "|" + re.escape(first[0]) + r"\w*\.?\s*" + re.escape(last) + "@|" + re.escape(first) + r"\." + re.escape(last) + "@"
        if re.search(pat, hay_hdr): hits.append({"kind": "attorney (header)", "term": name})
        elif re.search(pat, hay): hits.append({"kind": "attorney (body)", "term": name})
    for dom in P.get("domains", []):
        d = dom.strip().lower().lstrip("@")
        if d and "@" + d in hay: hits.append({"kind": "law-firm domain", "term": d})
    for term in P.get("terms", []):
        t = term.strip().lower()
        if t and t in hay: hits.append({"kind": "privilege term", "term": term})
    kinds = {h["kind"].split(" ")[0] for h in hits}
    score = min(100, 35 * len(kinds) + 10 * max(0, len(hits) - len(kinds)))
    if any(h["kind"] == "attorney (header)" for h in hits): score = max(score, 50)
    return {"score": score, "hits": hits}
