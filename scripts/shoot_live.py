"""Headless screenshot of one live-model run: shoot_live.py [question] [out.png]"""
import sys
from playwright.sync_api import sync_playwright
q = sys.argv[1] if len(sys.argv) > 1 else "Build a timeline and check for back-dated documents"
out = sys.argv[2] if len(sys.argv) > 2 else "shots/ui_live_run.png"
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1500, "height": 1900})
    pg.goto("http://localhost:8090/?auto=1&q=" + q.replace(" ", "%20"))
    pg.wait_for_selector("h2:has-text('Answer')", timeout=240000); pg.wait_for_timeout(800)
    pg.screenshot(path=out, full_page=True); b.close()
print(out)
