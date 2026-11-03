"""How the app gets a file parsed. Production: ask the isolated sandbox-runner (the only holder of the Docker socket).
Dev/tests without Docker: a local namespace sandbox (unshare -rn + prlimit + timeout), clearly labelled as such."""
import json, os, pathlib, shutil, subprocess, time, uuid, urllib.request
from .db import ROOT
EVID, JOBS = ROOT / "evidence", ROOT / "jobs"
RUNNER = os.getenv("SANDBOX_RUNNER_URL", "")
LIMITS = {"network": "none", "evidence": "read-only bind mount", "scratch": "per-job /out only", "memory_mb": 512, "cpus": 1,
          "pids": 64, "timeout_s": int(os.getenv("PARSE_TIMEOUT", 20)), "user": "uid 10001 (non-root)", "capabilities": "none (cap-drop ALL)",
          "env": "empty (no secrets)", "lifetime": "destroyed after each file (--rm)"}
PARSER = pathlib.Path(__file__).resolve().parent.parent / "parser" / "parse.py"
def mode():
    if RUNNER:
        try:
            with urllib.request.urlopen(RUNNER.rstrip("/") + "/health", timeout=3) as r: return "docker-runner" if r.status == 200 else "unavailable"
        except Exception: return "unavailable"
    return "local-namespace (dev only)"
def parse(sha, hint):
    """Returns (runner_info, result_dict_or_None, children[list of (name, bytes)])."""
    job = "pi-job-" + uuid.uuid4().hex[:12]; out = JOBS / job / "out"; out.mkdir(parents=True); os.chmod(out, 0o777)
    t0 = time.time()
    try:
        info = _runner(sha, job, hint) if RUNNER else _local(sha, out, hint)
        info["seconds"] = round(time.time() - t0, 2)
        res = None; rp = out / "result.json"
        if rp.is_file() and rp.stat().st_size < 20_000_000:
            try: res = json.loads(rp.read_text())
            except Exception: res = None
        kids = []
        if res and not info.get("out_capped"):
            for c in res.get("children", []):
                p = (out / c["path"]).resolve()
                if p.parent == (out / "children").resolve() and p.is_file() and not p.is_symlink(): kids.append((c["name"], p.read_bytes()))
        return info, res, kids
    finally:
        shutil.rmtree(JOBS / job, ignore_errors=True)
def _runner(sha, job, hint):
    req = urllib.request.Request(RUNNER.rstrip("/") + "/parse", data=json.dumps({"sha": sha, "job": job, "hint": hint}).encode(),
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=LIMITS["timeout_s"] + 40) as r: return json.load(r)
def _local(sha, out, hint):
    mem = 512 * 1024 * 1024; ev = EVID / sha
    cmd = ["env", "-i", "PATH=/usr/bin:/bin", f"PI_IN={ev}", f"PI_OUT={out}", "unshare", "-rn", "prlimit", f"--as={mem * 2}",
           f"--cpu={LIMITS['timeout_s']}", "--nofile=256", os.getenv("PARSER_PYTHON", shutil.which("python3")), str(PARSER), hint]
    info = {"container": None, "runner": "local-namespace", "timed_out": False, "destroyed": True}
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=LIMITS["timeout_s"])
        info.update(exit_code=p.returncode, stderr=p.stderr[-4000:])
    except subprocess.TimeoutExpired: info.update(exit_code=-9, timed_out=True, stderr="killed: time limit")
    return info
