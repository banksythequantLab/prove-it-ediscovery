"""Sandbox runners. DockerSandbox is the production path (Vultr VM). LocalNamespaceSandbox is a
fallback for hosts without Docker: Linux user+net+mount+pid namespaces, rlimits, empty env."""
import os, pathlib, re, shutil, subprocess, tempfile, time, uuid
HERE = pathlib.Path(__file__).resolve().parent.parent
PI = HERE / "sandbox" / "pi.py"
LIMITS = {"timeout_s": int(os.getenv("SANDBOX_TIMEOUT", 20)), "memory_mb": int(os.getenv("SANDBOX_MEMORY_MB", 512)),
          "cpus": float(os.getenv("SANDBOX_CPUS", 1)), "pids": 64, "network": "none", "corpus": "read-only", "env": "empty (no secrets)"}
BLOCK_SIGNS = [("network", r"Network is unreachable|Temporary failure in name resolution|Name or service not known|nodename nor servname|Errno -[23]|Errno 101"),
               ("filesystem-write", r"Read-only file system|Errno 30"),
               ("permission", r"Permission denied|Operation not permitted"),
               ("memory", r"MemoryError|Cannot allocate memory"),]
CODE_SIGNS = [("network attempt", r"\burllib|\brequests\b|\bsocket\b|http\.client|httpx"),
              ("env/secret read", r"os\.environ|getenv|/proc/self/environ"),
              ("file deletion/write to corpus", r"os\.remove|unlink|shutil\.rmtree|open\([^)]*corpus[^)]*['\"][wa]"),
              ("subprocess spawn", r"\bsubprocess\b|os\.system|os\.popen")]
def docker_available():
    if os.getenv("SANDBOX_MODE") == "local": return False
    try: return subprocess.run(["docker","info"],capture_output=True,timeout=8).returncode == 0
    except Exception: return False
class Run(dict): pass
def _prepare(code, corpus_dir):
    work = pathlib.Path(tempfile.mkdtemp(prefix="pi-sbx-"))
    shutil.copytree(corpus_dir, work/"corpus"); shutil.copy(PI, work/"pi.py"); (work/"main.py").write_text(code)
    for p in [work/"corpus", *(work/"corpus").rglob("*"), work/"pi.py", work/"main.py"]:
        os.chmod(p, 0o555 if p.is_dir() else 0o444)
    os.chmod(work, 0o777)
    return work
def _analyze(code, out, err, rc, timed_out):
    blocked = [{"kind": k, "evidence": re.search(rx, out+err).group(0)} for k, rx in BLOCK_SIGNS if re.search(rx, out+err)]
    if timed_out: blocked.append({"kind": "timeout", "evidence": f"killed after {LIMITS['timeout_s']}s"})
    attempts = [{"kind": k, "evidence": re.search(rx, code).group(0)} for k, rx in CODE_SIGNS if re.search(rx, code)]
    return blocked, attempts
class DockerSandbox:
    name = "docker"
    image = os.getenv("SANDBOX_IMAGE", "prove-it-sandbox:latest")
    def run(self, code, corpus_dir):
        work = _prepare(code, corpus_dir); cid = "pi-" + uuid.uuid4().hex[:10]
        cmd = ["docker","run","--rm","--name",cid,"--network","none","--read-only","--tmpfs","/tmp:rw,size=64m,noexec",
               "--memory",f"{LIMITS['memory_mb']}m","--memory-swap",f"{LIMITS['memory_mb']}m","--cpus",str(LIMITS['cpus']),
               "--pids-limit",str(LIMITS['pids']),"--cap-drop","ALL","--security-opt","no-new-privileges","--user","10001:10001",
               "--env-file","/dev/null","-v",f"{work}/corpus:/work/corpus:ro","-v",f"{work}/pi.py:/work/pi.py:ro",
               "-v",f"{work}/main.py:/work/main.py:ro",self.image]
        return _exec(cmd, code, work, {"runner": "docker", "container": cid, "command": " ".join(cmd)},
                     on_timeout=lambda: subprocess.run(["docker","rm","-f",cid],capture_output=True))
class LocalNamespaceSandbox:
    name = "local-namespace"
    def run(self, code, corpus_dir):
        work = _prepare(code, corpus_dir); mem = LIMITS["memory_mb"]*1024*1024
        inner = (f"mount --bind {work} {work} && mount -o remount,ro,bind {work} {work} && "
                 "for d in /home /workspace /root /etc/ssh; do [ -d $d ] && mount -t tmpfs -o ro,size=1k none $d 2>/dev/null; done; "
                 f"cd {work} && exec prlimit --as={mem} --cpu={LIMITS['timeout_s']} --fsize=10485760 --nofile=64 /usr/bin/python3 -E -s main.py")
        cmd = ["env","-i","PATH=/usr/bin:/bin","HOME=/tmp","LANG=C.UTF-8","unshare","-r","-n","-m","-p","--fork","--mount-proc","sh","-c",inner]
        return _exec(cmd, code, work, {"runner": "local-namespace", "container": None, "command": "env -i … unshare -rnmp --fork … prlimit … python3 -E -s main.py", "limit_overrides": {"pids": "not enforced (local)", "cpus": "cpu-time rlimit only"}})
def _exec(cmd, code, work, meta, on_timeout=None):
    t0 = time.time(); timed_out = False
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=LIMITS["timeout_s"]+5, env={"PATH": "/usr/bin:/bin"})
        out, err, rc = p.stdout, p.stderr, p.returncode
    except subprocess.TimeoutExpired as e:
        timed_out = True; rc = -9; out = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or ""); err = "TIMEOUT"
        if on_timeout: on_timeout()
    finally:
        subprocess.run(["chmod","-R","u+w",str(work)]); shutil.rmtree(work, ignore_errors=True)
    if rc in (-9, 137, -24, 152) and time.time()-t0 >= LIMITS['timeout_s']-1: timed_out = True
    blocked, attempts = _analyze(code, out, err, rc, timed_out)
    return Run(meta, exit_code=rc, stdout=out[-20000:], stderr=err[-20000:], seconds=round(time.time()-t0,2), limits={**LIMITS, **meta.pop("limit_overrides", {})},
               blocked=blocked, code_attempts=attempts, destroyed=not pathlib.Path(work).exists())
def get_sandbox():
    return DockerSandbox() if docker_available() else LocalNamespaceSandbox()
