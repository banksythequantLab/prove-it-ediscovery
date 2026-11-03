"""sandbox-runner: the ONLY component holding the Docker socket. Internal network only, no published port, no secrets.
The app may ask for exactly one thing: "parse evidence file <sha256> into scratch dir <job>". Image, command and every
isolation flag are fixed here and cannot be influenced by the request."""
import json, os, pathlib, re, shutil, subprocess, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
ROOT = pathlib.Path(os.getenv("PI_ROOT", "/var/lib/prove-it")).resolve()
EVID, JOBS = ROOT / "evidence", ROOT / "jobs"
IMAGE = "prove-it-parser:latest"; TIMEOUT = int(os.getenv("PARSE_TIMEOUT", 20)); OUT_CAP = 300_000_000
FLAGS = ["--rm", "--network", "none", "--read-only", "--tmpfs", "/tmp:rw,size=64m,noexec,nosuid",
         "--memory", "512m", "--memory-swap", "512m", "--cpus", "1", "--pids-limit", "64", "--ulimit", "nofile=256:256",
         "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", "10001:10001", "--env-file", "/dev/null"]
SHA, JOB, HINT = re.compile(r"^[0-9a-f]{64}$"), re.compile(r"^pi-job-[0-9a-f]{12}$"), re.compile(r"^[a-z0-9]{0,8}$")
def du(p): return sum(f.stat().st_size for f in p.rglob("*") if f.is_file() and not f.is_symlink())
def parse(sha, job, hint):
    if not (isinstance(sha, str) and SHA.match(sha)): raise ValueError("bad sha")
    if not (isinstance(job, str) and JOB.match(job)): raise ValueError("bad job")
    if not (isinstance(hint, str) and HINT.match(hint)): raise ValueError("bad hint")
    ev = EVID / sha; out = JOBS / job / "out"
    if not ev.is_file() or ev.is_symlink(): raise ValueError("no such evidence")
    if not out.is_dir() or out.is_symlink() or any(out.iterdir()): raise ValueError("scratch dir missing or not empty")
    cid = "pi-parse-" + uuid.uuid4().hex[:10]
    cmd = ["docker", "run", "--name", cid, *FLAGS, "-v", f"{ev}:/in/file:ro", "-v", f"{out}:/out:rw", IMAGE, hint]
    r = {"container": cid, "command": " ".join(cmd), "timed_out": False}
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT + 5, env={"PATH": "/usr/bin:/bin"})
        r.update(exit_code=p.returncode, stderr=p.stderr[-4000:])
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True); r.update(exit_code=-9, timed_out=True, stderr="killed: time limit")
    gone = subprocess.run(["docker", "ps", "-aq", "--filter", f"name=^{cid}$"], capture_output=True, text=True).stdout.strip() == ""
    size = du(out); r.update(out_bytes=size, destroyed=gone)
    if size > OUT_CAP:
        shutil.rmtree(out, ignore_errors=True); out.mkdir(); r.update(out_capped=True)
    return r
class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        b = json.dumps(obj).encode(); self.send_response(code); self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if self.path == "/health": return self._send(200, {"ok": True, "image": IMAGE, "flags": FLAGS, "timeout_s": TIMEOUT})
        self._send(404, {})
    def do_POST(self):
        if self.path != "/parse": return self._send(404, {})
        try:
            b = json.loads(self.rfile.read(min(int(self.headers.get("content-length", 0)), 2048)) or b"{}")
            self._send(200, parse(b.get("sha"), b.get("job"), b.get("hint", "")))
        except (ValueError, json.JSONDecodeError) as e: self._send(400, {"error": str(e)})
    def log_message(self, *a): pass
if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 9000), H).serve_forever()
