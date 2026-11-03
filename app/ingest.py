import email, email.utils, json, pathlib, re
from email import policy
INJECTION_PATTERNS = [r"ignore (all )?previous instructions", r"system override", r"os\.environ", r"api[_ ]?key",
                      r"exfil", r"https?://\S+/collect", r"do not mention this"]
def _text_pdf(p):
    from pypdf import PdfReader
    return "\n".join((pg.extract_text() or "") for pg in PdfReader(str(p)).pages)
def _text_docx(p):
    import docx
    return "\n".join(par.text for par in docx.Document(str(p)).paragraphs)
def parse(path: pathlib.Path):
    ext = path.suffix.lower(); d = {"id": path.stem, "filename": path.name, "type": ext.lstrip("."),
        "date": None, "from": "", "to": "", "cc": "", "subject": "", "text": ""}
    if ext == ".eml":
        m = email.message_from_bytes(path.read_bytes(), policy=policy.default)
        d.update({k: str(m.get(h, "")) for k, h in [("from","From"),("to","To"),("cc","Cc"),("subject","Subject")]})
        try: d["date"] = email.utils.parsedate_to_datetime(m["Date"]).isoformat()
        except Exception: d["date"] = str(m.get("Date", ""))
        body = m.get_body(preferencelist=("plain",)); d["text"] = body.get_content() if body else ""
        d["raw_date_header"] = str(m.get("Date", ""))
    elif ext == ".pdf": d["text"] = _text_pdf(path)
    elif ext == ".docx": d["text"] = _text_docx(path)
    else: d["text"] = path.read_text(errors="replace")
    if not d["date"]:
        mm = re.search(r"Date:\s*([^\n]+)", d["text"]); d["date"] = mm.group(1).strip() if mm else None
    d["suspicious"] = [p for p in INJECTION_PATTERNS if re.search(p, d["text"], re.I)]
    return d
def build(src_dir: pathlib.Path, out_dir: pathlib.Path):
    out_dir.mkdir(parents=True, exist_ok=True); docs = []
    for p in sorted(src_dir.iterdir()):
        if p.is_file() and p.suffix.lower() in {".eml",".txt",".pdf",".docx",".csv",".md"}:
            try: docs.append(parse(p))
            except Exception as e: docs.append({"id": p.stem, "filename": p.name, "type": "error", "text": "", "error": str(e), "suspicious": []})
    (out_dir/"docs.jsonl").write_text("\n".join(json.dumps(x) for x in docs) + "\n")
    return docs
