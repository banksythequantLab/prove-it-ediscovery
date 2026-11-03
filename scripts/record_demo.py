import html, json
from playwright.sync_api import sync_playwright
URL="https://207-246-93-241.sslip.io"
LOOP=open("/tmp/loop_out.txt").read()
CAP_JS="""t=>{let d=document.getElementById('__cap');if(!d){d=document.createElement('div');d.id='__cap';
d.style.cssText='position:fixed;left:50%;bottom:28px;transform:translateX(-50%);max-width:1100px;background:rgba(0,0,0,.85);color:#fff;font:600 26px/1.35 system-ui,sans-serif;padding:14px 26px;border-radius:12px;z-index:99999;text-align:center;border:2px solid #f5b942';document.body.appendChild(d)}
d.textContent=t;d.style.display=t?'block':'none'}"""
def card(pg, body):
    pg.set_content(f"<html><body style='margin:0;background:#0d1117;color:#e6edf3;font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh'>{body}</body></html>")
def cap(pg,t): pg.evaluate(CAP_JS,t)
with sync_playwright() as p:
    b=p.chromium.launch(); ctx=b.new_context(viewport={"width":1280,"height":800},record_video_dir="/tmp/vid",record_video_size={"width":1280,"height":800})
    pg=ctx.new_page(); W=pg.wait_for_timeout
    card(pg,"<div style='text-align:center'><div style='font-size:60px;font-weight:800'>Prove-It <span style='color:#f5b942'>eDiscovery</span></div><div style='font-size:32px;margin-top:20px;color:#9fb0c3'>Sandboxed document review on Vultr</div><div style='font-size:22px;margin-top:40px;color:#6b7b8c'>Vultr Agent Rush · Challenge 1: Blast Radius Zero</div></div>"); W(3500)
    pg.goto(URL); pg.wait_for_selector(".doc"); cap(pg,"Live on a Vultr VM. 41 synthetic case documents loaded; the LLM is glm-5.3-flash on Vultr Serverless Inference."); W(5000)
    pg.click("text=Load demo"); W(1500)
    pg.click("text=Privilege log"); cap(pg,"Ask: 'Flag likely privileged documents'. The model writes Python code, and a throwaway Docker sandbox runs it."); W(1500); pg.click("#go")
    pg.wait_for_selector("h2:has-text('Answer')",timeout=180000); W(800)
    cap(pg,"Every finding cites a document ID and an exact quote. The host checks each quote against the source (✅ = verified)."); 
    pg.locator("text=Cited findings").first.scroll_into_view_if_needed(); pg.mouse.wheel(0,250); W(6000)
    if pg.locator(".card.failed").count():
        cap(pg,"Self-healing: a failed attempt's error is sent back to the model, which fixes its code and retries."); pg.locator(".card.failed").first.scroll_into_view_if_needed(); W(4500)
    else:
        cap(pg,"Each attempt card shows the plan, the generated code, the container run, and destroyed=true."); pg.locator(".card").first.scroll_into_view_if_needed(); W(4500)
    pg.evaluate("window.scrollTo(0,0)"); cap(pg,"Now the booby trap: DOC-033 hides a prompt injection telling the agent to steal secrets, phone home, and delete the corpus."); W(5000)
    pg.click("text=Containment drill"); W(1200); pg.click("#go")
    cap(pg,"Drill: we simulate a model that OBEYED the injection. Its hostile code runs in the sandbox...")
    pg.wait_for_selector("h2:has-text('Answer')",timeout=180000); W(500)
    pg.locator("text=CONTAINMENT EVENT").first.scroll_into_view_if_needed()
    pg.evaluate("()=>{const e=[...document.querySelectorAll('*')].find(x=>x.children.length<12&&x.textContent.includes('CONTAINMENT EVENT')&&x.offsetHeight>40);if(e){e.style.outline='4px solid #ffcc00';e.style.outlineOffset='4px'}}")
    cap(pg,"CONTAINED. The exfil request hit 'Network is unreachable', writes hit a read-only filesystem, and there were no secrets to steal."); W(7500)
    cap(pg,"The sandbox had no network, a read-only corpus, 512 MB of memory, 64 processes, no Linux capabilities, and an empty environment."); W(5000)
    pg.evaluate("()=>{const c=document.getElementById('cl');c.scrollIntoView({block:'start'});c.style.outline='4px solid #ffcc00'}")
    cap(pg,"Containment Log: every run's limits and blocked actions are recorded, and every container is destroyed afterwards (destroyed=true)."); W(6500)
    cap(pg,"")
    term="<pre style='font:22px/1.5 ui-monospace,monospace;background:#000;color:#7ee787;padding:30px 40px;border-radius:12px;border:1px solid #333;max-width:1150px;white-space:pre-wrap'>"+html.escape("# real run on the Vultr VM: sandbox code = `while True: pass`\n"+LOOP)+"</pre>"
    card(pg,"<div><div style='font-size:28px;margin-bottom:18px;color:#f5b942;text-align:center'>Real VM output: an infinite loop gets killed</div>"+term+"</div>"); cap(pg,"Infinite loop: killed at the 20 s cap, the container is removed, and nothing is left behind."); W(8000)
    card(pg,"<div style='text-align:center;max-width:1100px'><div style='font-size:40px;font-weight:800;margin-bottom:28px'>Architecture</div><div style='font-size:25px;line-height:1.7;color:#c9d6e3'>Vultr VM orchestrator (FastAPI + Caddy HTTPS)<br>→ <b>Vultr Serverless Inference</b> · glm-5.3-flash<br>→ isolated <b>sandbox-runner</b> (only holder of the Docker socket, fixed flags)<br>→ Docker sandbox: network none · read-only · 512 MB · 1 CPU · pids 64 · uid 10001 · cap-drop ALL · no secrets · destroyed after each run</div><div style='font-size:28px;margin-top:40px;color:#f5b942'>github.com/banksythequantLab/prove-it-ediscovery</div><div style='font-size:22px;margin-top:12px;color:#9fb0c3'>Live: 207-246-93-241.sslip.io</div></div>"); W(7000)
    v=pg.video.path(); ctx.close(); b.close(); print(v)
