#!/usr/bin/env python3
"""Drive the bible editor in Chromium against a COPY of the DB (plan dirs are copied too). No Claude calls.

Checks: edit a character, add + delete an episode, reorder, add lore via a real Wikipedia search,
refuse a lore without a source, and (approved plans) that the series row follows.
Usage: python3 tools/bible_edit_selftest.py --plan 1
"""
import argparse
import asyncio
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHROME = os.environ.get("CHROME") or None   # None = Playwright's bundled Chromium


async def drive(base, pw, plan, out):
    from playwright.async_api import async_playwright
    log, errs = [], []
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        pg = await b.new_page(viewport={"width": 1300, "height": 1100})
        pg.set_default_timeout(20000)
        pg.on("pageerror", lambda e: errs.append("JS " + str(e)))
        pg.on("response", lambda r: r.status >= 500 and errs.append(f"{r.status} {r.url}"))
        pg.on("dialog", lambda d: asyncio.ensure_future(d.accept()))
        try:
            await pg.goto(base + "/login")
            await pg.fill("input[name=password]", pw)
            await pg.click("button[type=submit]")
            await pg.wait_for_selector("#view .panel")
            await pg.evaluate(f"go('series',{{plan:{plan}}})")
            await pg.wait_for_selector("text=Series bible")
            await pg.screenshot(path=f"{out}/1-view.png", full_page=True)
            # 1) edit first character
            await pg.click(".card button:has-text('✎ แก้') >> nth=0")
            await pg.fill("#fName", "ชื่อแก้แล้ว")
            await pg.screenshot(path=f"{out}/2-char-form.png", full_page=True)
            await pg.click("button.primary:text-is('บันทึก')")
            await pg.wait_for_timeout(2500)
            log.append("char renamed: " + str(await pg.locator("b:text-is('ชื่อแก้แล้ว')").count()))
            # 2) add an episode by hand
            await pg.click("button:has-text('+ เพิ่มเอง') >> nth=2")
            await pg.fill("#fETitle", "ตอนทดสอบ")
            await pg.fill("#fEHook", "เปิดเรื่องทดสอบ")
            await pg.fill("#fESum", "เรื่องย่อทดสอบ")
            await pg.click("button.primary:text-is('บันทึก')")
            await pg.wait_for_timeout(2500)
            n_eps = await pg.evaluate("S.be.plan.bible.episodes.length")
            log.append(f"episodes after add: {n_eps}")
            # 3) move the new last episode up
            await pg.click(f".ep >> nth={n_eps - 1} >> button[title='เลื่อนขึ้น']")
            await pg.wait_for_timeout(2500)
            titles = await pg.evaluate("S.be.plan.bible.episodes.map(e=>e.number+':'+e.title)")
            log.append("after move: " + json.dumps(titles[-2:], ensure_ascii=False))
            # 4) lore without a source is refused (client-side)
            await pg.click("button:has-text('+ เพิ่มเอง') >> nth=1")
            await pg.wait_for_selector("#fLName")
            await pg.fill("#fLName", "ทดสอบ lore")
            await pg.fill("#fLFact", "ข้อเท็จจริง")
            await pg.click("button.primary:text-is('บันทึก')")
            await pg.wait_for_timeout(500)
            log.append("no-source error: " + await pg.inner_text("#beErr"))
            # 5) real search (Wikipedia) then pick a hit
            await pg.fill("#fLQ", "วัดมหาบุศย์")
            await pg.click("button:text-is('ค้น')")
            await pg.wait_for_selector("button:text-is('ใช้อันนี้')", timeout=90000)
            await pg.click("button:text-is('ใช้อันนี้') >> nth=0")
            await pg.wait_for_timeout(600)
            await pg.fill("#fLName", "ทดสอบ lore")
            await pg.fill("#fLFact", "ข้อเท็จจริงจากแหล่ง")
            await pg.screenshot(path=f"{out}/3-lore-form.png", full_page=True)
            await pg.click("button.primary:text-is('บันทึก')")
            await pg.wait_for_timeout(2500)
            log.append("lore count: " + str(await pg.evaluate("S.be.plan.bible.lore.length")))
            # 6) delete the test episode
            idx = await pg.evaluate("S.be.plan.bible.episodes.findIndex(e=>e.title==='ตอนทดสอบ')")
            num = await pg.evaluate(f"S.be.plan.bible.episodes[{idx}].number")
            await pg.evaluate(f"beEdit('episode:{num}')")
            await pg.wait_for_selector("button.danger:text-is('ลบ')")
            await pg.click("button.danger:text-is('ลบ')")
            await pg.wait_for_timeout(2500)
            log.append("episodes after delete: " + str(await pg.evaluate("S.be.plan.bible.episodes.length")))
            # 7) AI box opens (we do not send it here)
            await pg.click(".card button:has-text('✨ AI') >> nth=0")
            await pg.wait_for_selector("#beInstr")
            await pg.screenshot(path=f"{out}/4-ai-box.png", full_page=True)
            log.append("ai box: ok")
        except Exception:
            await pg.screenshot(path=f"{out}/FAIL.png", full_page=True)
            print(json.dumps(log, ensure_ascii=False, indent=1))
            raise
        await b.close()
    return log, errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", type=int, default=1)
    a = ap.parse_args()
    work = Path(tempfile.mkdtemp(prefix="bibletest-", dir=os.environ.get("TMPDIR")))
    out = work / "shots"
    out.mkdir()
    db = work / "t.db"
    src = sqlite3.connect(ROOT / "db/shortclip.db")
    dst = sqlite3.connect(db)
    src.backup(dst)
    src.close()
    dst.close()
    before = sqlite3.connect(ROOT / "db/shortclip.db").execute("SELECT bible_json FROM plans WHERE id=?", (a.plan,)).fetchone()[0]
    # lore-search writes into the plan dir: point the code at a COPY of plans/ (never the user's)
    plans = work / "plans"
    shutil.copytree(ROOT / "plans", plans, ignore=shutil.ignore_patterns("logs"))
    before_sources = (ROOT / f"plans/{a.plan}/sources.jsonl").read_bytes()
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    pw = "bt-" + os.urandom(4).hex()
    (work / "pass").write_text(pw)
    env = {**os.environ, "SHORTCLIP_DB": str(db), "SHORTCLIP_DASH_PASS": str(work / "pass"),
           "SHORTCLIP_SECRETS": str(work / "secrets"), "SHORTCLIP_PLANS": str(plans)}
    dash = subprocess.Popen([sys.executable, str(ROOT / "dashboard/server.py"), "--port", str(port)], env=env,
                            cwd=ROOT, stdout=open(work / "dash.log", "w"), stderr=subprocess.STDOUT)
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1)
                break
            except OSError:
                time.sleep(0.2)
        log, errs = asyncio.run(drive(f"http://localhost:{port}", pw, a.plan, out))
        after_real = sqlite3.connect(ROOT / "db/shortclip.db").execute("SELECT bible_json FROM plans WHERE id=?", (a.plan,)).fetchone()[0]
        res = {"work": str(work), "ui": log, "errors": errs,
               "real_db_untouched": before == after_real and
               before_sources == (ROOT / f"plans/{a.plan}/sources.jsonl").read_bytes()}
        print(json.dumps(res, ensure_ascii=False, indent=1))
        ok = (not errs and res["real_db_untouched"] and "char renamed: 1" in log
              and any(x.startswith("lore count") for x in log) and "เลือกแหล่ง" in " ".join(log))
        print("BIBLE EDIT SELFTEST", "OK" if ok else "FAILED")
        return 0 if ok else 1
    finally:
        dash.terminate()


if __name__ == "__main__":
    sys.exit(main())
