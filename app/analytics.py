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
FIRM_NAMES = {"velaw.com": "vinson & elkins", "andrews-kurth.com": "andrews kurth", "akllp.com": "andrews kurth", "bracepatt.com": "bracewell",
              "skadden.com": "skadden", "sullcrom.com": "sullivan & cromwell", "bakerbotts.com": "baker botts"}
BOILER = re.compile(r"[^.\n]*(may (contain|be) (confidential|privileged)|intended (only|solely)|not the intended recipient|property of enron|"
                    r"confidentiality notice|if you have received this (e-?mail|message) in error)[^.]*[.\n]", re.I)
LEGEND = re.compile(r"privileged\s*(and|&)\s*confidential|attorney[- ]client\s+(privilege|privileged|communication)|attorney\s+work\s+product|"
                    r"privileged\s+(legal\s+advice|communication)|this\s+(e-?mail|message|memo|document)\s+is\s+privileged|prepared\s+at\s+the\s+(request|direction)\s+of\s+counsel", re.I)
ADVICE = re.compile(r"legal advice|advice of counsel|legal opinion|legal review|legal (issue|risk|analysis)|your advice|our counsel|"
                    r"(please|pls) (advise|review)|(comments|mark-?up|redline|blackline)s? (on|to) (the|our|your)?\s*(draft|agreement)|"
                    r"\bdraft (agreement|memo|opinion|letter)|enforceab|indemnif|liabilit|regulatory (issue|risk)", re.I)
LITIG = re.compile(r"litigation|lawsuit|subpoena|deposition|in anticipation of|class action|plaintiff|defendant|settlement negotiat|"
                   r"(sec|ferc|doj) (inquiry|investigation)|work product", re.I)
LAW_FIRMS = {"cwt.com", "mayerbrown.com", "rlf.com", "prickett.com", "modrall.com", "sullcrom.com", "bakerbotts.com", "kslaw.com", "lw.com",
             "weil.com", "milbank.com", "cravath.com", "stblaw.com", "davispolk.com", "gibsondunn.com", "haynesboone.com", "fulbright.com",
             "akingump.com", "lockeliddell.com", "jenkens.com", "chadbourne.com", "kirkland.com", "sidley.com", "jonesday.com", "whitecase.com",
             "cliffordchance.com", "linklaters.com", "freshfields.com", "allenovery.com", "velaw.com", "bracepatt.com", "akllp.com",
             "andrews-kurth.com", "skadden.com", "ogilvyrenault.com", "blakes.com", "stikeman.com", "mckennalong.com", "pillsburylaw.com"}
def _is_firm(d): return d in LAW_FIRMS or re.search(r"(^|[.-])(law|legal|llp)[\w-]*\.", d) is not None
def _emails(s): return re.findall(r"[\w.'+-]+@([\w-]+(?:\.[\w-]+)+)", (s or "").lower())
def prescreen(doc, protocol):
    """Deterministic privilege screen. Privilege needs a LEGAL-ADVICE signal, not just a lawyer's name: an attorney's routine
    business mail is not privileged by default. Signals: outside-counsel domain/firm, in-house counsel + advice language,
    an explicit privilege legend that is not a boilerplate footer, litigation/work-product language. Third parties outside
    the privilege circle on the header lower the score (possible waiver). Returns {score, hits:[{kind, term}]}."""
    P = protocol.get("privilege", {}) if protocol else {}
    hdr = " ".join([doc.get("email_from") or "", doc.get("email_to") or "", doc.get("email_cc") or ""]).lower()
    raw = (doc.get("subject") or "") + "\n" + (doc.get("text") or "")[:200000]
    body = BOILER.sub(" ", raw); low = body.lower()
    hits, score = [], 0
    def hit(kind, term, pts):
        nonlocal score; hits.append({"kind": kind, "term": term, "points": pts}); score += pts
    doms = [d.strip().lower().lstrip("@") for d in P.get("domains", []) if d.strip()]
    hdr_doms = set(_emails(hdr)); firm_hdr = [d for d in doms if d in hdr_doms]
    for d in firm_hdr: hit("outside counsel (header)", d, 45)
    other_firms = sorted(d for d in hdr_doms if d not in doms and _is_firm(d))
    if other_firms: hit("other law firm (header)", ", ".join(other_firms[:3]), 20 if not firm_hdr else 5)
    if not firm_hdr:
        for d in doms:
            nm = FIRM_NAMES.get(d)
            if "@" + d in low or (nm and nm in low): hit("outside counsel (body)", nm or d, 20); break
    att_hdr = []
    for name in P.get("attorneys", []):
        n = name.strip().lower()
        if not n: continue
        first, last = n.split()[0], n.split()[-1]
        pat = re.escape(n) + "|" + re.escape(last) + r",?\s+" + re.escape(first) + "|" + re.escape(first) + r"\." + re.escape(last) + "@"
        if re.search(pat, hdr): att_hdr.append(name)
    adv = sorted({m.group(0).lower() for m in ADVICE.finditer(low)})[:4]
    terms = [t for t in P.get("terms", []) if t.strip() and t.strip().lower() in low]
    if att_hdr:
        hit("in-house counsel (header)", ", ".join(att_hdr), 15)
        if adv or terms: hit("counsel + legal-advice language", ", ".join(adv or terms), 25)
    elif (firm_hdr or other_firms) and adv: hit("legal-advice language", ", ".join(adv), 15)
    leg = LEGEND.search(body)
    if leg: hit("explicit privilege legend (not footer)", leg.group(0), 30)
    lit = sorted({m.group(0).lower() for m in LITIG.finditer(low)})[:4]
    if lit: hit("litigation / work-product language", ", ".join(lit), 20)
    third = sorted(d for d in hdr_doms if d not in doms and not _is_firm(d) and not d.endswith("enron.com"))
    if third and score: hit("non-lawyer third party on header (possible waiver)", ", ".join(third[:3]), -15)
    if not (firm_hdr or other_firms or att_hdr or leg or lit): score = min(score, 20)   # no counsel and no legal-advice signal
    return {"score": max(0, min(100, score)), "hits": hits}
PRIV_LOG_THRESHOLD = 50
def priv_combined(model_priv, pre_score):
    """Blend the model's privilege score with the deterministic screen. Neither alone puts a doc on the log easily:
    a strong model call needs some deterministic signal, and a keyword hit needs the model to agree somewhat."""
    m, p = model_priv or 0, pre_score or 0
    return round(0.6 * m + 0.4 * p)
