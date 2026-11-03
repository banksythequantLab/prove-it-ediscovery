"""Sandbox runner: the ONLY component with the Docker socket. Internal network only (no published port).
The app may send just a job directory name; every docker flag below is fixed here and cannot be overridden."""
import json, os, pathlib, re, subprocess, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
ROOT = pathlib.Path(os.getenv("SBX_ROOT", "/var/lib/prove-it/sbx")).resolve()
IMAGE = "prove-it-sandbox:latest"; TIMEOUT = 20
FLAGS = ["--rm", "--network", "none", "--read-only", "--tmpfs", "/tmp:rw,size=64m,noexec",
         "--memory", "512m", "--memory-swap", "512m", "--cpus", "1", "--pids-limit", "64",
         "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", "10001:10001", "--env-file", "/dev/null"]
JOB = re.compile(r"^pi-sbx-[A-Za-z0-9_]{1,40}$")
def run(job):
    if not isinstance(job, str) or not JOB.match(job): raise ValueError("bad job name")
    w = (ROOT / job).resolve()
    if w.parent != ROOT or not w.is_dir(): raise ValueError("job dir missing")
    cid = "pi-" + uuid.uuid4().hex[:10]
    mounts = []
    for src, dst, mode in [("corpus", "/work/corpus", "ro"), ("pi.py", "/work/pi.py", "ro"), ("main.py", "/work/main.py", "ro"),
                           ("_boot.py", "/work/_boot.py", "ro"), ("out", "/work/out", "rw")]:
        p = w / src
        if p.is_symlink() or not p.exists(): raise ValueError(f"bad mount {src}")
        mounts += ["-v", f"{p}:{dst}:{mode}"]
    cmd = ["docker", "run", "--name", cid, *FLAGS, *mounts, IMAGE]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT + 5, env={"PATH": "/usr/bin:/bin"})
        return {"exit_code": p.returncode, "stdout": p.stdout[-20000:], "stderr": p.stderr[-20000:], "timed_out": False, "container": cid, "command": " ".join(cmd)}
    except subprocess.TimeoutExpired as e:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True)
        out = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        return {"exit_code": -9, "stdout": out[-20000:], "stderr": "TIMEOUT", "timed_out": True, "container": cid, "command": " ".join(cmd)}
class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        b = json.dumps(obj).encode(); self.send_response(code); self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        self._send(200, {"ok": True, "flags": FLAGS, "image": IMAGE, "timeout_s": TIMEOUT}) if self.path == "/health" else self._send(404, {})
    def do_POST(self):
        if self.path != "/run": return self._send(404, {})
        try:
            body = json.loads(self.rfile.read(min(int(self.headers.get("content-length", 0)), 4096)) or b"{}")
            self._send(200, run(body.get("job")))
        except ValueError as e: self._send(400, {"error": str(e)})
    def log_message(self, *a): pass
if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 9000), H).serve_forever()
