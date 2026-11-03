#!/usr/bin/env python3
"""Build the demo collection from the public Enron email corpus (CMU/FERC release) + clearly labelled stress-test files.
Usage: build_demo.py <maildir> <outdir>   -> outdir/enron_demo.zip, outdir/protocol.json"""
import io, json, mailbox, os, pathlib, random, re, sys, zipfile, zlib
SRC, OUT = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]); OUT.mkdir(parents=True, exist_ok=True)
CUST = {"lay-k": 150, "skilling-j": 140, "shackleton-s": 130, "kaminski-v": 110}
KW = re.compile(r"california|price cap|ferc|megawatt|grid|iso\b|raptor|ljm|fastow|hedg|spe\b|special purpose|retention|shred|destroy|andersen|document", re.I)
rng = random.Random(20011202)
def pick(c, n):
    files = sorted(p for p in (SRC / c).rglob("*") if p.is_file()); rng.shuffle(files)
    hits, rand = [], []
    for p in files:
        if len(hits) >= int(n * .45) and len(rand) >= n - int(n * .45): break
        try: b = p.read_bytes()
        except Exception: continue
        if len(b) > 200_000: continue
        if KW.search(b[:20000].decode("latin-1")) and len(hits) < int(n * .45): hits.append(p)
        elif len(rand) < n - int(n * .45): rand.append(p)
    return hits + rand
z = zipfile.ZipFile(OUT / "enron_demo.zip", "w", zipfile.ZIP_DEFLATED)
n = 0
# genuine, unmodified Enron emails with outside counsel (Vinson & Elkins, Andrews Kurth), always included so the
# privilege workflow has real attorney-client material to find
COUNSEL = ["shackleton-s/inbox/216.", "lay-k/discussion_threads/204.", "shackleton-s/notes_inbox/477."]
seen = set()
for c, k in CUST.items():
    for p in pick(c, k) + [SRC / x for x in COUNSEL if x.startswith(c + "/")]:
        if p in seen: continue
        seen.add(p)
        rel = p.relative_to(SRC / c); z.writestr(f"{c}/{'/'.join(rel.parts[:-1])}/{rel.parts[-1].rstrip('.')}.eml", p.read_bytes()); n += 1
# a few cross-folder exact duplicates (Enron users filed the same message in several folders)
for c in ("lay-k", "skilling-j"):
    for p in pick(c, 6)[:6]:
        z.writestr(f"{c}/all_documents_copy/{p.name.rstrip('.')}.eml", p.read_bytes()); n += 1
# an mbox export for kaminski-v
mb_path = OUT / "kaminski_export.mbox"; mb_path.unlink(missing_ok=True); mb = mailbox.mbox(mb_path)
for p in pick("kaminski-v", 45): mb.add(mailbox.mboxMessage(p.read_bytes()))
mb.flush(); mb.close(); z.write(mb_path, "kaminski-v/kaminski_export.mbox"); mb_path.unlink()
# ---- stress-test files added to the demo set (NOT part of the Enron corpus) ----
S = "zz_stress_tests_added_to_demo"
inner = io.BytesIO()
with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as iz:
    for i in range(4): iz.writestr(f"zeros_{i}.bin", b"\0" * (1 << 30))   # 4 GiB expands from ~4 MB
z.writestr(f"{S}/zip_bomb_4GiB.zip", inner.getvalue())
def pdf(objs):
    out = b"%PDF-1.4\n"; offs = []
    for i, o in enumerate(objs, 1): offs.append(len(out)); out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    x = len(out); out += f"xref\n0 {len(objs)+1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offs)
    return out + f"trailer\n<< /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF\n".encode()
def page_pdf(content: bytes, compress=True):
    data = zlib.compress(content, 9) if compress else content
    return pdf([b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
                f"<< /Length {len(data)}{' /Filter /FlateDecode' if compress else ''} >>\nstream\n".encode() + data + b"\nendstream",
                b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"])
z.writestr(f"{S}/pdf_flate_bomb.pdf", page_pdf(b"BT /F1 12 Tf 72 720 Td (" + b"A" * (1500 << 20) + b") Tj ET"))   # 1.5 GB stream
# 4,000 pages that all reference ONE small content stream of 150k empty text-show operators: ~60 KB on disk,
# hundreds of millions of operators to interpret, never produces text (so no text cap trips) -> must hit the time limit
NP = 4000; ops = zlib.compress(b"BT /F1 1 Tf " + b"1 0 0 1 9 9 Tm () Tj " * 150_000 + b"ET", 9)
kids = " ".join(f"{5 + i} 0 R" for i in range(NP)).encode()
objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [" + kids + b"] /Count " + str(NP).encode() + b" >>",
        f"<< /Length {len(ops)} /Filter /FlateDecode >>\nstream\n".encode() + ops + b"\nendstream", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
objs += [b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 3 0 R /Resources << /Font << /F1 4 0 R >> >> >>"] * NP
z.writestr(f"{S}/pdf_pathological_4000_pages.pdf", pdf(objs))
# a Word file whose document.xml inflates to ~400 MB of XML: the DOCX library has no size guard, so only the sandbox's
# 512 MB memory limit stops it
dx = io.BytesIO()
with zipfile.ZipFile(dx, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as dz:
    dz.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
    dz.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
    with dz.open("word/document.xml", "w", force_zip64=True) as w:
        w.write(b'<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>')
        chunk = b"<w:p><w:r><w:t>x</w:t></w:r></w:p>" * 30000
        for _ in range(400 * 2**20 // len(chunk)): w.write(chunk)
        w.write(b"</w:body></w:document>")
z.writestr(f"{S}/docx_memory_bomb.docx", dx.getvalue())
row = "2001-01-17,PG&E,SP15,{i},250.00,MWh,trader_{t}\n"
csv = io.StringIO(); csv.write("date,counterparty,hub,deal_id,price,unit,trader\n"); i = 0
while csv.tell() < 100 << 20: csv.write(row.format(i=i, t=i % 97)); i += 1
z.writestr(f"{S}/huge_trade_blotter_100MB.csv", csv.getvalue())
# a genuine Enron email (shackleton-s/notes_inbox/1449, unmodified text) rendered to an image-only "scanned" PDF:
# an ADDED test file (the corpus has no scans) that exercises sandboxed OCR. Needs Pillow + DejaVu fonts at build time.
from PIL import Image, ImageDraw, ImageFont, ImageFilter
src = (SRC / "shackleton-s/notes_inbox/1449.").read_text("latin-1").replace("\t", "    ")
font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 26)
lines = [l for raw in src.splitlines() for l in ([raw[i:i + 78] for i in range(0, len(raw), 78)] or [""])]
pages = []
for i in range(0, len(lines), 62):
    im = Image.new("L", (1700, 2200), 255); dr = ImageDraw.Draw(im)
    for j, l in enumerate(lines[i:i + 62]): dr.text((110, 110 + j * 32), l, fill=25, font=font)
    pages.append(im.rotate(0.4, fillcolor=255).filter(ImageFilter.GaussianBlur(0.6)).convert("RGB"))
scan = io.BytesIO(); pages[0].save(scan, "PDF", resolution=200, save_all=True, append_images=pages[1:])
z.writestr("zz_added_test_files/SCANNED_enron_email_shackleton-s_notes_inbox_1449.pdf", scan.getvalue())
z.close()
proto = {"matter": "In re Enron Corp. (demo) - first-pass review",
 "issues": [
  {"id": "CA", "name": "California energy-crisis trading strategies", "description": "Documents concerning Enron's Western power trading during the 2000-2001 California energy crisis, including strategies such as Death Star, Get Shorty, Ricochet, Fat Boy, load shifting, congestion/ancillary-services schemes, price caps, the California ISO/PX, and FERC inquiries into Western market manipulation."},
  {"id": "SPE", "name": "Raptor / LJM special-purpose entities", "description": "Documents concerning LJM1, LJM2, the Raptor vehicles, Chewco, Andrew Fastow's partnerships, hedges of merchant investments using Enron stock, related-party transactions, and their accounting or disclosure."},
  {"id": "RET", "name": "Document retention and destruction", "description": "Documents concerning document-retention policies, litigation holds, instructions to preserve, delete, or destroy documents or email, and Arthur Andersen's handling of Enron records."}],
 "date_range": {"start": "1999-01-01", "end": "2002-06-30"},
 "privilege": {"attorneys": ["Sara Shackleton", "Mark Haedicke", "Elizabeth Sager", "James Derrick", "Richard Sanders", "Jordan Mintz", "Mark Taylor"],
               "domains": ["velaw.com", "bracepatt.com", "akllp.com", "andrews-kurth.com", "skadden.com"],
               "terms": ["privileged", "attorney-client", "work product", "legal advice", "prepared at the request of counsel"]}}
(OUT / "protocol.json").write_text(json.dumps(proto, indent=1))
print(f"wrote {OUT/'enron_demo.zip'} ({(OUT/'enron_demo.zip').stat().st_size/2**20:.1f} MB, {n} emails + mbox + 4 stress files + 1 scanned PDF)")
