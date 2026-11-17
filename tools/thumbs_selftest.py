#!/usr/bin/env python3
"""thumbs_selftest.py — click the cover ("ปก") stage in Chromium against a COPY of the DB and episode.

No paid call: the AI-ideas button is only checked for presence (ideas.json is copied from the real
episode if it exists). Renders a cover through the form, selects it, then opens the post stage and
checks the chosen cover is shown. The real episode dir is never written (copy under projects/_selftest-*).
Use: python3 tools/thumbs_selftest.py --job 8
"""
import argparse, asyncio, json, os, secrets, shutil, socket, sqlite3, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHROME = os.environ.get("CHROME") or None   # None = Playwright's bundled Chromium


async def drive(base, pw, eid, epdir, out):
    from playwright.async_api import async_playwright
    log, errs = [], []
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        pg = await (await b.new_context(viewport={"width": 1300, "height": 1100})).new_page()
        pg.on("pageerror", lambda e: errs.append("JS " + str(e)))
        pg.on("response", lambda r: r.status >= 400 and errs.append(f"{r.status} {r.url}"))
        pg.on("dialog", lambda d: asyncio.ensure_future(d.accept()))
        await pg.goto(base + "/login")
        await pg.fill("input[name=password]", pw)
        await pg.click("button[type=submit]")
        await pg.wait_for_selector("#view .panel")
        await pg.evaluate(f"go('produce',{{series:'genius-at-jj',ep:{eid},stage:'thumb'}})")
        await pg.wait_for_selector("text=ทำปก", timeout=20000)
        log.append(("stage tab", await pg.locator(".stp:has-text('ปก')").count()))
        log.append(("frames shown", await pg.locator("img[src*='frames%2Ff']").count()))
        log.append(("ai button", await pg.locator("button:has-text('ให้ AI คิดปก')").count()))
        log.append(("idea cards", await pg.locator("button:has-text('ใช้ไอเดียนี้')").count()))
        await pg.fill("#thHead", "ทดสอบปกจากเว็บ")
        before = len(list((epdir / "thumbs").glob("cover-*.jpg")))
        await pg.click("button:has-text('สร้างปกนี้')")
        await pg.wait_for_selector("button:has-text('ใช้ปกนี้')", timeout=30000)
        after = sorted((epdir / "thumbs").glob("cover-*.jpg"), key=lambda p: int(p.stem.split("-")[1]))
        log.append(("cover rendered", len(after) == before + 1))
        await pg.screenshot(path=str(out / "1-thumb-stage.png"), full_page=True)
        await pg.locator("button:has-text('ใช้ปกนี้')").first.click()
        await pg.wait_for_selector("text=เลือกปกแล้ว", timeout=15000)
        sel = json.loads((epdir / "thumbs/selected.json").read_text())
        log.append(("selected newest", sel["file"] == after[-1].name))
        await pg.click("button:has-text('ไปขั้นโพสต์')")
        await pg.wait_for_selector("text=ปกที่จะส่ง", timeout=15000)
        await pg.screenshot(path=str(out / "2-publish.png"), full_page=True)
        log.append(("post shows cover", True))
        await b.close()
    return log, errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", type=int, required=True)
    a = ap.parse_args()
    work = Path(tempfile.mkdtemp(prefix="thumbtest-", dir=os.environ.get("TMPDIR")))
    out = work / "shots"; out.mkdir()
    db = work / "shortclip.db"
    sqlite3.connect(ROOT / "db/shortclip.db").backup(sqlite3.connect(db))
    con = sqlite3.connect(db); con.row_factory = sqlite3.Row
    job = dict(con.execute("SELECT * FROM jobs WHERE id=?", (a.job,)).fetchone())
    params = json.loads(job["params_json"])
    src = Path(params["epdir"])
    epdir = ROOT / "projects" / src.parent.name / f"_selftest-{work.name}"
    shutil.copytree(src, epdir, ignore=shutil.ignore_patterns("node_modules", "logs", "backup", "renders"))
    (epdir / "renders").mkdir(exist_ok=True)
    shutil.copy2(src / "renders/final.mp4", epdir / "renders/final.mp4")
    for f in (epdir / "thumbs").glob("cover-*"):
        f.unlink()
    (epdir / "thumbs/selected.json").unlink(missing_ok=True)
    params["epdir"] = str(epdir)
    con.execute("UPDATE jobs SET params_json=?,status='done' WHERE id=?", (json.dumps(params, ensure_ascii=False), a.job))
    con.commit()
    eid = job["episode_id"]
    real_before = sorted(p.name for p in (src / "thumbs").glob("*"))
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    pw = secrets.token_urlsafe(12); (work / "pass").write_text(pw)
    env = {**os.environ, "SHORTCLIP_DB": str(db), "SHORTCLIP_DASH_PASS": str(work / "pass"), "SHORTCLIP_DASH_NOLAUNCH": "1",
           "SHORTCLIP_SECRETS": str(work / "secrets"), "SHORTCLIP_PLANS": str(work / "plans")}
    dash = subprocess.Popen([sys.executable, str(ROOT / "dashboard/server.py"), "--port", str(port)], env=env,
                            stdout=open(work / "dash.log", "w"), stderr=subprocess.STDOUT)
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1); break
            except OSError:
                time.sleep(0.2)
        log, errs = asyncio.run(drive(f"http://localhost:{port}", pw, eid, epdir, out))
    finally:
        dash.terminate()
        shutil.rmtree(epdir, ignore_errors=True)
    real_ok = sorted(p.name for p in (src / "thumbs").glob("*")) == real_before
    log.append(("real episode untouched", real_ok))
    for k, v in log:
        print(f"{k}: {v}")
    print("errors:", errs, "\nscreenshots:", out)
    d = dict(log)
    ok = not errs and real_ok and d.get("cover rendered") and d.get("selected newest") and d.get("stage tab") and d.get("frames shown", 0) >= 15
    print("THUMBS SELFTEST", "OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
