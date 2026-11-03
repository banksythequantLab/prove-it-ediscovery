"""Helper library available INSIDE the sandbox. Read-only corpus; results go to stdout."""
import json, os
_HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.join(_HERE, "corpus")
def load_docs():
    with open(os.path.join(CORPUS, "docs.jsonl")) as f:
        return [json.loads(l) for l in f if l.strip()]
def emit(summary="", findings=None, table=None, csv_name=None):
    """findings: [{doc_id, quote, note}]  table: {columns:[...], rows:[[...]]}"""
    print("@@PI_RESULT@@" + json.dumps({"summary": summary, "findings": findings or [],
          "table": table, "csv_name": csv_name}, default=str))
OUT = os.path.join(_HERE, "out")
def save_chart(fig, name):
    """Save a matplotlib figure as PNG into the sandbox output dir; host returns it to the UI."""
    import re as _re
    name = _re.sub(r"[^\w.-]", "_", name)
    if not name.endswith(".png"): name += ".png"
    fig.savefig(os.path.join(OUT, name), dpi=110, bbox_inches="tight")
    return name
