"""Prove-It file parser. Runs INSIDE a throwaway sandbox container (network none, read-only root, evidence mounted
read-only at /in/file, writable scratch at /out only, 512 MB, 1 CPU, 64 pids, 20 s, uid 10001, no capabilities, empty env).
Usage: parse.py <type-hint>. Writes /out/result.json and, for containers (zip/mbox), extracted members under /out/children/.
It never writes anywhere else and never needs the network. Caps below make hostile-but-realistic files fail safely."""
import email, email.policy, email.utils, io, json, mailbox, os, re, sys, zipfile
IN, OUT = os.environ.get("PI_IN", "/in/file"), os.environ.get("PI_OUT", "/out")
TEXT_CAP = 1_000_000            # chars of extracted text kept per document
CHILD_TOTAL_CAP = 150_000_000   # bytes we will decompress out of one container file
CHILD_COUNT_CAP = 5000
RATIO_CAP = 200                 # per-member compression ratio above which we refuse (zip-bomb signature)
res = {"status": "ok", "type": None, "meta": {}, "text": "", "text_truncated": False, "children": [], "warnings": []}
OCR_PAGE_CAP = 20               # OCR at most the first 20 pages of an image-only PDF
OCR_BUDGET_S = 14               # stop OCR before the 20 s sandbox kill so partial text survives
T0 = __import__("time").monotonic()
class Cap(Exception): pass
def warn(m): res["warnings"].append(m)
def setText(t):
    t = t or ""
    if len(t) > TEXT_CAP: res["text_truncated"] = True; warn(f"text capped at {TEXT_CAP:,} chars (original {len(t):,})"); t = t[:TEXT_CAP]
    res["text"] = t
def sniff(hint):
    with open(IN, "rb") as f: head = f.read(2048)
    if head.startswith(b"PK\x03\x04"):
        try:
            names = zipfile.ZipFile(IN).namelist()
            if "word/document.xml" in names: return "docx"
            if "xl/workbook.xml" in names: return "xlsx"
        except Exception: pass
        return "zip"
    if head.startswith(b"%PDF"): return "pdf"
    if head.startswith(b"\x89PNG\r\n") or head.startswith(b"\xff\xd8\xff") or head[:4] in (b"II*\x00", b"MM\x00*"): return "image"
    if head.startswith(b"\xd0\xcf\x11\xe0"): return "msg"
    if head.startswith(b"From ") and hint in ("mbox", ""): return "mbox"
    if hint in ("eml", "msg", "mbox", "pdf", "docx", "xlsx", "csv", "txt", "html", "htm", "zip"): return {"htm": "html"}.get(hint, hint)
    if re.match(rb"(?is)\s*(<!doctype html|<html)", head): return "html"
    if re.search(rb"(?m)^(Message-ID|From|Subject|Date):", head): return "eml"
    return "txt"
def addr(v): return str(v) if v else ""
def email_meta(m):
    return {"from": addr(m.get("From")), "to": addr(m.get("To")), "cc": addr(m.get("Cc")), "date": addr(m.get("Date")),
            "subject": addr(m.get("Subject")), "message_id": addr(m.get("Message-ID")).strip(),
            "in_reply_to": addr(m.get("In-Reply-To")).strip(), "references": addr(m.get("References")).strip(),
            "x_from": addr(m.get("X-From")), "x_to": addr(m.get("X-To")), "x_folder": addr(m.get("X-Folder"))}
def email_body(m):
    parts = []
    for p in (m.walk() if m.is_multipart() else [m]):
        ct = p.get_content_type()
        if p.get_content_maintype() == "multipart": continue
        if p.get_filename():
            data = p.get_payload(decode=True) or b""; write_child(p.get_filename(), data); continue
        if ct in ("text/plain", "text/html"):
            raw = p.get_payload(decode=True) or b""
            txt = raw.decode(p.get_content_charset() or "utf-8", "replace")
            parts.append(html_to_text(txt) if ct == "text/html" else txt)
    return "\n".join(parts)
def html_to_text(s):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(s, "html.parser")
    for t in soup(["script", "style"]): t.decompose()
    return re.sub(r"\n{3,}", "\n\n", soup.get_text("\n"))
_written = 0
def write_child(name, data):
    global _written
    if len(res["children"]) >= CHILD_COUNT_CAP: raise Cap(f"more than {CHILD_COUNT_CAP} members")
    _written += len(data)
    if _written > CHILD_TOTAL_CAP: raise Cap(f"decompressed output exceeded {CHILD_TOTAL_CAP//1_000_000} MB")
    os.makedirs(f"{OUT}/children", exist_ok=True)
    i = len(res["children"]); p = f"{OUT}/children/{i:05d}"
    with open(p, "wb") as f: f.write(data)
    clean = "/".join(x for x in str(name).replace("\\", "/").split("/") if x not in ("", ".", ".."))[:300]
    res["children"].append({"name": clean or f"member-{i}", "path": f"children/{i:05d}", "size": len(data)})
def p_eml():
    m = email.message_from_binary_file(open(IN, "rb"), policy=email.policy.compat32)
    res["meta"] = email_meta(m); setText(email_body(m))
def p_mbox():
    n = 0
    for m in mailbox.mbox(IN, create=False):
        write_child(f"message-{n:05d}.eml", m.as_bytes()); n += 1
    setText(f"[mailbox container: {n} messages extracted]")
def p_msg():
    import extract_msg
    m = extract_msg.Message(IN)
    res["meta"] = {"from": m.sender or "", "to": m.to or "", "cc": m.cc or "", "date": str(m.date or ""), "subject": m.subject or "",
                   "message_id": getattr(m, "messageId", "") or ""}
    setText(m.body or "")
    for a in m.attachments:
        d = getattr(a, "data", None)
        if isinstance(d, bytes): write_child(a.longFilename or a.shortFilename or "attachment", d)
def p_zip():
    z = zipfile.ZipFile(IN); members = [i for i in z.infolist() if not i.is_dir()]
    declared = sum(i.file_size for i in members)
    for i in members:
        if i.compress_size and i.file_size / max(i.compress_size, 1) > RATIO_CAP and i.file_size > 10_000_000:
            raise Cap(f"member '{i.filename}' expands {i.file_size/ max(i.compress_size,1):,.0f}x ({i.file_size/1e9:.1f} GB declared): zip-bomb signature")
    if declared > CHILD_TOTAL_CAP: raise Cap(f"archive declares {declared/1e9:.1f} GB uncompressed (cap {CHILD_TOTAL_CAP//1_000_000} MB)")
    for i in members:
        with z.open(i) as src:   # stream with a hard byte counter: declared sizes can lie
            buf = io.BytesIO()
            while True:
                chunk = src.read(1 << 20)
                if not chunk: break
                buf.write(chunk)
                if _written + buf.tell() > CHILD_TOTAL_CAP: raise Cap(f"decompressed output exceeded {CHILD_TOTAL_CAP//1_000_000} MB (sizes in header were false)")
            write_child(i.filename, buf.getvalue())
    setText(f"[archive container: {len(members)} files extracted]")
def p_pdf():
    from pypdf import PdfReader
    r = PdfReader(IN); md = r.metadata or {}
    res["meta"] = {"title": str(md.get("/Title") or ""), "author": str(md.get("/Author") or ""), "date": str(md.get("/CreationDate") or ""), "pages": len(r.pages)}
    out = []; total = 0
    for pg in r.pages:
        t = pg.extract_text() or ""; out.append(t); total += len(t)
        if total > TEXT_CAP: break
    if total < 40 * max(1, min(len(r.pages), OCR_PAGE_CAP)) * 0.5 and total < 200:   # essentially no text layer: scanned
        n = min(len(r.pages), OCR_PAGE_CAP); txt = ocr_pdf(n)
        if len(r.pages) > OCR_PAGE_CAP: warn(f"OCR limited to first {OCR_PAGE_CAP} of {len(r.pages)} pages")
        if txt.strip(): out = [txt]
    setText("\n\n".join(out))
def ocr(path):
    import subprocess
    left = OCR_BUDGET_S - (__import__("time").monotonic() - T0)
    if left <= 1: raise TimeoutError
    p = subprocess.run(["tesseract", path, "stdout", "-l", "eng", "--psm", "3"], capture_output=True, timeout=left)
    return p.stdout.decode("utf-8", "replace")
def ocr_pdf(n):
    import subprocess
    out, done = [], 0
    for i in range(1, n + 1):
        try:
            left = OCR_BUDGET_S - (__import__("time").monotonic() - T0)
            if left <= 1: raise TimeoutError
            subprocess.run(["pdftoppm", "-r", "200", "-gray", "-png", "-singlefile", "-f", str(i), "-l", str(i), IN, "/tmp/pg"], capture_output=True, timeout=left, check=True)
            out.append(ocr("/tmp/pg.png")); done += 1
        except (TimeoutError, subprocess.TimeoutExpired):
            warn(f"OCR time budget ({OCR_BUDGET_S}s) reached after {done} page(s)"); break
        except subprocess.CalledProcessError as e:
            warn(f"page {i} could not be rendered for OCR"); continue
        finally:
            if os.path.exists("/tmp/pg.png"): os.unlink("/tmp/pg.png")
    res["meta"].update(ocr=True, ocr_pages=done, ocr_engine="tesseract")
    return "\n\n".join(out)
def p_image():
    res["meta"] = {"ocr": True, "ocr_engine": "tesseract", "ocr_pages": 1}
    try: setText(ocr(IN))
    except Exception as e: raise Cap(f"OCR failed or exceeded time budget: {type(e).__name__}")
def p_docx():
    import docx
    d = docx.Document(IN); cp = d.core_properties
    res["meta"] = {"title": cp.title or "", "author": cp.author or "", "date": str(cp.created or "")}
    setText("\n".join(p.text for p in d.paragraphs))
def p_xlsx():
    import openpyxl
    wb = openpyxl.load_workbook(IN, read_only=True, data_only=True); out = []; n = 0; size = 0
    for ws in wb.worksheets:
        out.append(f"## sheet {ws.title}")
        for row in ws.iter_rows(values_only=True):
            line = "\t".join("" if v is None else str(v) for v in row); out.append(line); n += 1; size += len(line) + 1
            if size > TEXT_CAP: warn(f"stopped after {n:,} rows (text cap)"); break
    setText("\n".join(out))
def p_csv():
    out = []; size = 0; rows = 0
    with open(IN, "r", errors="replace", newline="") as f:
        for line in f:
            rows += 1
            if size < TEXT_CAP: out.append(line); size += len(line)
    if size >= TEXT_CAP: warn(f"CSV has {rows:,} lines; text capped at {TEXT_CAP:,} chars")
    res["meta"] = {"rows": rows}; setText("".join(out))
def p_txt(): setText(open(IN, "rb").read(TEXT_CAP * 4).decode("utf-8", "replace"))
def p_html(): setText(html_to_text(open(IN, "rb").read(TEXT_CAP * 4).decode("utf-8", "replace")))
def main():
    hint = re.sub(r"[^a-z0-9]", "", (sys.argv[1] if len(sys.argv) > 1 else "").lower())[:8]
    t = sniff(hint); res["type"] = t
    try: {"eml": p_eml, "mbox": p_mbox, "msg": p_msg, "zip": p_zip, "pdf": p_pdf, "docx": p_docx, "xlsx": p_xlsx,
          "csv": p_csv, "txt": p_txt, "html": p_html, "image": p_image}[t]()
    except Cap as e: res.update(status="capped", error=str(e))
    except MemoryError: res.update(status="error", error="MemoryError inside parser")
    except Exception as e: res.update(status="error", error=f"{type(e).__name__}: {str(e)[:300]}")
    with open(f"{OUT}/result.json", "w") as f: json.dump(res, f)
if __name__ == "__main__": main()
