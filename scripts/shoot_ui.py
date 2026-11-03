"""Headless UI screenshots (dev helper): retry cards mid-run and final, plus chart."""
import sys
from playwright.sync_api import sync_playwright
URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8090"
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1500, "height": 1900})
    pg.goto(URL); pg.wait_for_selector(".doc")
    pg.click("text=Chart by week"); pg.click("#go")
    pg.wait_for_selector(".card.failed", timeout=30000); pg.screenshot(path="shots/ui_retry_in_progress.png")
    pg.wait_for_selector("h2:has-text('Answer')", timeout=60000); pg.wait_for_timeout(500)
    pg.screenshot(path="shots/ui_retry_cards_chart.png", full_page=True)
    pg.click("text=Timeline + back-dating"); pg.click("#go")
    pg.wait_for_selector("h2:has-text('Answer')", timeout=60000); pg.wait_for_timeout(500)
    pg.screenshot(path="shots/ui_timeline_selfheal.png", full_page=True)
    b.close()
print("ok")
