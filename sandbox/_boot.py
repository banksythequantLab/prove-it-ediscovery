# Sandbox bootstrap: make bundled libs importable, headless matplotlib, then run main.py.
import os, sys, runpy
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "_site")); sys.path.insert(0, HERE)
os.environ["MPLBACKEND"] = "Agg"; os.environ["MPLCONFIGDIR"] = os.path.join(HERE, "out", ".mpl")
runpy.run_path(os.path.join(HERE, "main.py"), run_name="__main__")
