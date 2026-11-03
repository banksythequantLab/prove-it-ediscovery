import os, sys, pathlib, tempfile
ROOT = tempfile.mkdtemp(prefix="pi-test-")
os.environ.update(PI_ROOT=ROOT, SCORER="fake", PARSER_PYTHON=sys.executable, UPLOAD_RATE_N="100")
os.environ.pop("SANDBOX_RUNNER_URL", None)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
