#!/usr/bin/env python3
"""End-to-end check of the publish UI without real platform keys.

Starts tests/fake_platforms.py, a dashboard on a COPY of the DB with its own secrets dir, then drives
Chromium: connect YouTube (paste-back), TikTok + Instagram (callback), post EP to all three, check rows.
Usage: python3 tools/publish_selftest.py [--episode 3]
"""
import argparse
import asyncio
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from fake_platforms import FakeState, make_server  # noqa: E402

CHROME = os.environ.get("CHROME") or None   # None = Playwright's bundled Chromium


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def drive(base, pw, episode, out, state):
    try:
        return await _drive(base, pw, episode, out, state)
    except Exception as exc:  # keep a picture of where it stopped
        raise RuntimeError(f"UI step failed: {str(exc).splitlines()[0]} (see {out}/FAIL.png)") from exc


async def _drive(base, pw, episode, out, state):
    from playwright.async_api import async_playwright
    errs, log = [], []
    async with async_playwright() as p:
      try:
        b = await p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        pg = await b.new_page(viewport={"width": 1300, "height": 1100})
        pg.set_default_timeout(15000)
        _drive.page = pg
        pg.on("pageerror", lambda e: errs.append("JS " + str(e)))
        pg.on("response", lambda r: r.status >= 500 and errs.append(f"{r.status} {r.url}"))
        pg.on("dialog", lambda d: asyncio.ensure_future(d.accept()))
        await pg.goto(base + "/login")
        await pg.fill("input[name=password]", pw)
        await pg.click("button[type=submit]")
        await pg.wait_for_selector("#view .panel", timeout=20000)
        await pg.click(".tab[data-t=accounts]")
        await pg.wait_for_timeout(1500)
        await pg.screenshot(path=f"{out}/accounts-empty.png", full_page=True)
        # YouTube paste-back
        await pg.click("button:text-is('เชื่อมบัญชี') >> nth=0")
        await pg.wait_for_selector("#ap_yt_go", timeout=10000)
        auth = await pg.get_attribute("#ap_yt_go", "href")
        stt = auth.split("state=")[1].split("&")[0]
        await pg.fill("#ap_yt_url", f"http://localhost:1/?state={stt}&code=4/abc&scope=x")
        await pg.click("text=ยืนยัน")
        await pg.wait_for_timeout(2500)
        log.append("youtube connected: " + str(await pg.locator("text=ช่องทดสอบ").count()))
        # TikTok + IG: app creds, connect (redirect goes to the fake auth page -> we emulate the platform redirect)
        for plat, k, s_ in (("tiktok", "#ap_tt_k", "#ap_tt_s"), ("instagram", "#ap_ig_k", "#ap_ig_s")):
            await pg.fill(k, "123456" if plat == "instagram" else "ck-test")
            await pg.fill(s_, "secret-test")
            await pg.click(f"{k} ~ button")
            await pg.wait_for_timeout(1800)
            btns = pg.locator("button:text-is('เชื่อมบัญชี')")
            async with pg.expect_navigation():
                await btns.last.click()
            stt = pg.url.split("state=")[1].split("&")[0]
            await pg.goto(f"{base}/oauth/{plat}/callback?state={stt}&code=CODE")
            body = await pg.inner_text("body")
            log.append(f"{plat} callback: {body.strip()[:60]}")
            await pg.click("text=กลับไปหน้า dashboard")
            await pg.wait_for_timeout(1500)
            await pg.click(".tab[data-t=accounts]")
            await pg.wait_for_timeout(1500)
        await pg.screenshot(path=f"{out}/accounts-connected.png", full_page=True)
        # post screen
        await pg.click(".tab[data-t=produce]")
        await pg.wait_for_timeout(1500)
        slug = await pg.evaluate(f"(S.eps.find(e=>e.episode_id==={episode})||{{}}).series_id")
        slug = await pg.evaluate(f"(S.seriesList.find(s=>s.id==={slug})||{{}}).slug")
        await pg.evaluate(f"go('produce',{{series:'{slug}',ep:{episode},stage:'publish'}})")
        await pg.wait_for_timeout(2500)
        await pg.screenshot(path=f"{out}/post-youtube.png", full_page=True)
        await pg.click("text=โพสต์ไป YouTube Shorts")
        await pg.wait_for_timeout(5000)
        await pg.click("button.tab:has-text('TikTok')")
        await pg.wait_for_timeout(800)
        await pg.click("text=ส่งเข้า Inbox TikTok")
        await pg.wait_for_timeout(5000)
        await pg.click("button.tab:has-text('TikTok')")
        await pg.wait_for_timeout(600)
        await pg.check("input[name=ttm] >> nth=1")
        await pg.wait_for_timeout(2500)
        await pg.screenshot(path=f"{out}/post-tiktok-direct.png", full_page=True)
        log.append("tiktok direct shows nickname: " + str(await pg.locator("text=ทดสอบ").count()))
        await pg.evaluate("$('toast').textContent=''")
        log.append("buttons: " + str([await x.inner_text() for x in await pg.locator("#view button.primary").all()]))
        log.append("typing=" + str(await pg.evaluate("S.typing")) + " mode=" + str(await pg.evaluate("S.pub.ttMode")))
        log.append("matches: " + str(await pg.locator("text=โพสต์ไป TikTok").count()) + " " + str([await x.evaluate("e=>e.tagName+':'+e.textContent.slice(0,40)") for x in await pg.locator("text=โพสต์ไป TikTok").all()]))
        await pg.click("#view button.primary:text-is('โพสต์ไป TikTok')")  # no privacy chosen -> toast, nothing sent
        await pg.wait_for_timeout(800)
        log.append("toast: " + (await pg.text_content("#toast"))[:60])
        await pg.select_option("#pbPriv", "SELF_ONLY")
        await pg.check("#pbMusic")
        await pg.check("#pbAgain")
        await pg.click("#view button.primary:text-is('โพสต์ไป TikTok')")
        await pg.wait_for_timeout(1200)
        log.append("toast after direct: " + (await pg.text_content("#toast"))[:120])
        await pg.wait_for_timeout(5000)
        await pg.click("button.tab:has-text('Instagram')")
        await pg.wait_for_timeout(800)
        await pg.click("text=โพสต์ไป Instagram Reels")
        await pg.wait_for_timeout(9000)
        await pg.screenshot(path=f"{out}/post-history.png", full_page=True)
        log.append("history: " + (await pg.inner_text("#view")).split("ประวัติการโพสต์")[-1][:400].replace("\n", " | "))
        await b.close()
      except Exception:
        await _drive.page.screenshot(path=f"{out}/FAIL.png", full_page=True)
        log.append("view at failure: " + (await _drive.page.inner_text("#view"))[:600].replace("\n", " | "))
        print(json.dumps(log, ensure_ascii=False, indent=1))
        raise
    return errs, log


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--episode", type=int, default=3)
    a = ap.parse_args()
    work = Path(tempfile.mkdtemp(prefix="pubtest-", dir=os.environ.get("TMPDIR")))
    out = work / "shots"
    out.mkdir()
    db = work / "t.db"
    src = sqlite3.connect(ROOT / "db/shortclip.db")
    dst = sqlite3.connect(db)
    src.backup(dst)
    src.close()
    dst.close()
    state = FakeState()
    srv = make_server(state)
    port = free_port()
    pw = "selftest-" + os.urandom(4).hex()
    (work / "pass").write_text(pw)
    base = f"http://localhost:{port}"  # Chromium sends Secure cookies to localhost over http
    secrets_dir = work / "secrets"
    cfg = work / "publish.json"
    cfg.write_text(json.dumps({"public_base": base}))
    client = work / "client_secret.json"
    client.write_text(json.dumps({"installed": {"client_id": "cid", "client_secret": "cs"}}))
    env = {**os.environ, "SHORTCLIP_DB": str(db), "SHORTCLIP_DASH_PASS": str(work / "pass"),
           "SHORTCLIP_PUBLISH_FAKE": f"http://127.0.0.1:{srv.server_address[1]}",
           "SHORTCLIP_SECRETS": str(secrets_dir), "SHORTCLIP_PUBLISH_CONFIG": str(cfg)}
    real_secrets = sorted((ROOT / "secrets/publish").glob("*")) if (ROOT / "secrets/publish").exists() else []
    subprocess.run([sys.executable, str(ROOT / "orchestrator.py"), "--db", str(db), "--json", "publish", "set-app",
                    "youtube"], input=json.dumps({"client_secret_file": str(client)}),
                   env=env, cwd=ROOT, check=True, capture_output=True, text=True)
    state.fetch_video = lambda u: urllib.request.urlopen(u, timeout=30).status
    dash = subprocess.Popen([sys.executable, str(ROOT / "dashboard/server.py"), "--host", "127.0.0.1",
                             "--port", str(port)], env=env, cwd=ROOT,
                            stdout=open(work / "dash.log", "w"), stderr=subprocess.STDOUT)
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1)
                break
            except OSError:
                time.sleep(0.2)
        errs, log = asyncio.run(drive(base, pw, a.episode, out, state))
        time.sleep(3)
        con = sqlite3.connect(db)
        rows = con.execute("SELECT id,platform,status,url,error FROM posts ORDER BY id").fetchall()
        report = {"work": str(work), "posts": rows, "ui": log, "errors": errs,
                  "secret_files": sorted(p.name for p in secrets_dir.glob("*")),
                  "real_secrets_untouched": real_secrets == (sorted((ROOT / "secrets/publish").glob("*"))
                                                             if (ROOT / "secrets/publish").exists() else []),
                  "ig_fetched_status": state.ig_fetch_status,
                  "secrets_leaked_in_db": any("secret-test" in str(r) for r in con.execute("SELECT * FROM posts"))}
        print(json.dumps(report, ensure_ascii=False, indent=1, default=str))
        ok = (len(rows) == 4 and all(r[2] in ("published", "published_private", "inbox") for r in rows) and not errs
              and state.ig_fetch_status == 200 and not report["secrets_leaked_in_db"] and report["real_secrets_untouched"])
        print("PUBLISH SELFTEST", "OK" if ok else "FAILED")
        return 0 if ok else 1
    finally:
        dash.terminate()
        srv.shutdown()


if __name__ == "__main__":
    sys.exit(main())
