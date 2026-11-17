#!/usr/bin/env python3
"""edit_stages_selftest.py — click the new "edit any stage" UI against a COPY of the DB and an episode.

Starts its own dashboard (temp port, temp password) on a DB copy whose job params point to a copied
episode dir, then in Chromium: opens a cancelled episode, edits one script line by hand and saves,
checks that the orchestrator re-queued only the voice of that beat (picture kept), then asks for a
picture fix and a re-voice of one beat. Nothing paid runs: --no-run is what the server calls and the
detached `resume` is disabled via SHORTCLIP_DASH_NOLAUNCH. The real DB and project are never touched.

Use: python3 tools/edit_stages_selftest.py --job 8
"""
import argparse
import asyncio
import json
import os
import secrets
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


def sql(db, q, a=()):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in con.execute(q, a)]
        con.commit()
        return rows
    finally:
        con.close()


async def drive(base, pw, eid, job, epdir, out, dbpath):
    from playwright.async_api import async_playwright
    log, errs = [], []
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        ctx = await b.new_context(viewport={"width": 1300, "height": 1100})
        pg = await ctx.new_page()
        pg.on("pageerror", lambda e: errs.append("JS " + str(e)))
        pg.on("response", lambda r: r.status >= 400 and "/api/job/" not in r.url and errs.append(f"{r.status} {r.url}"))
        pg.on("dialog", lambda d: asyncio.ensure_future(d.accept()))
        await pg.goto(base + "/login")
        await pg.fill("input[name=password]", pw)
        await pg.click("button[type=submit]")
        await pg.wait_for_selector("#view .panel")
        await pg.evaluate(f"go('produce',{{series:'genius-at-jj',ep:{eid},stage:'script'}})")
        await pg.wait_for_selector("text=แก้บท", timeout=15000)
        body = await pg.inner_text("#view")
        log.append(("cancelled banner", "ตอนนี้ถูกยกเลิกไว้" in body))
        await pg.screenshot(path=str(out / "1-script-stage.png"), full_page=True)

        # picture: needs a reason, then sends it
        await pg.evaluate("S.stage='visual';S.key='';refresh(true)")
        await pg.wait_for_selector("#rvReason", timeout=10000)
        await pg.fill("#rvReason", "ช็อต 5 มืดไป ให้สว่างขึ้น")
        await pg.screenshot(path=str(out / "3-visual-stage.png"), full_page=True)
        log.append(("visual buttons", await pg.locator("button:has-text('วาดใหม่ทั้งหมด')").count()))
        await pg.click("button:has-text('ให้ AI แก้ตามนี้')")
        await pg.wait_for_timeout(2500)
        anim = sql(dbpath, "SELECT input_json FROM steps WHERE job_id=? AND role='animator'", (job,))[0]["input_json"]
        log.append(("picture fix queued with reason", "ช็อต 5 มืดไป" in (anim or "")))
        # voice stage UI present
        await pg.evaluate("S.stage='voice';S.key='';refresh(true)")
        await pg.wait_for_selector(".rvBeat", timeout=10000)
        log.append(("voice beat boxes", await pg.locator(".rvBeat").count()))
        await pg.screenshot(path=str(out / "4-voice-stage.png"), full_page=True)
        # hand edit: change one twist line
        await pg.evaluate("S.stage='script';S.key='';refresh(true)")
        await pg.wait_for_selector("button:text-is('✎ แก้บทเอง')", timeout=10000)
        await pg.click("button:text-is('✎ แก้บทเอง')")
        await pg.wait_for_selector("text=บันทึกบท", timeout=10000)
        sc = json.loads((epdir / "script.json").read_text())
        idx = next(i for i, l in enumerate(sc["lines"]) if l["beat"] == "twist")
        area = pg.locator("#scriptEditor textarea").nth(idx)
        await area.fill("แต่ลายเซ็นบนใบเช่านั้นเขียนไว้ตั้งแต่สามปีก่อน")
        await pg.screenshot(path=str(out / "2-editor.png"), full_page=True)
        await pg.click("button:text-is('บันทึกบท')")
        await pg.wait_for_timeout(2500)
        toast = await pg.inner_text("#toast")
        log.append(("save toast", toast))
        new = json.loads((epdir / "script.json").read_text())
        log.append(("script saved", new["lines"][idx]["text"] == "แต่ลายเซ็นบนใบเช่านั้นเขียนไว้ตั้งแต่สามปีก่อน"))
        log.append(("twist beat moved to backup", (epdir / "backup/beats-redo/twist.wav").exists()
                    and not (epdir / "audio/beats/twist.wav").exists()))
        log.append(("hook beat kept", (epdir / "audio/beats/hook.wav").exists()))

        await b.close()
    return log, errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", type=int, required=True)
    a = ap.parse_args()
    work = Path(tempfile.mkdtemp(prefix="edittest-", dir=os.environ.get("TMPDIR")))
    out = work / "shots"
    out.mkdir()
    db = work / "shortclip.db"
    sqlite3.connect(ROOT / "db/shortclip.db").backup(sqlite3.connect(db))
    job = sql(db, "SELECT * FROM jobs WHERE id=?", (a.job,))[0]
    params = json.loads(job["params_json"])
    src = Path(params["epdir"])
    # under ROOT/projects so the dashboard may serve it; a unique throwaway name, removed at the end
    epdir = ROOT / "projects" / src.parent.name / f"_selftest-{work.name}"
    shutil.copytree(src, epdir, ignore=shutil.ignore_patterns("node_modules", "renders"))
    (epdir / "renders").mkdir(exist_ok=True)
    params["epdir"] = str(epdir)
    sql(db, "UPDATE jobs SET params_json=? WHERE id=?", (json.dumps(params, ensure_ascii=False), a.job))
    # The real job failed at the director; pretend the voice+picture exist so the edit path is exercised.
    sql(db, "UPDATE steps SET status='done',attempt=MAX(attempt,1) WHERE job_id=? AND role IN ('sound','director','animator')", (a.job,))
    (epdir / "composition").mkdir(exist_ok=True)
    (epdir / "composition/index.html").write_text("<html/>")
    eid = job["episode_id"]
    real_before = (src / "script.json").read_text()

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    pw = secrets.token_urlsafe(12)
    (work / "pass").write_text(pw)
    env = {**os.environ, "SHORTCLIP_DB": str(db), "SHORTCLIP_DASH_PASS": str(work / "pass"),
           "SHORTCLIP_DASH_NOLAUNCH": "1", "SHORTCLIP_SECRETS": str(work / "secrets"),
           "SHORTCLIP_PLANS": str(work / "plans")}
    dash = subprocess.Popen([sys.executable, str(ROOT / "dashboard/server.py"), "--port", str(port)], env=env,
                            stdout=open(work / "dash.log", "w"), stderr=subprocess.STDOUT)
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1)
                break
            except OSError:
                time.sleep(0.2)
        log, errs = asyncio.run(drive(f"http://localhost:{port}", pw, eid, a.job, epdir, out, db))
    finally:
        dash.terminate()
        shutil.rmtree(epdir, ignore_errors=True)
    steps = {r["role"]: r["status"] for r in sql(db, "SELECT role,status FROM steps WHERE job_id=?", (a.job,))}
    jstat = sql(db, "SELECT status FROM jobs WHERE id=?", (a.job,))[0]["status"]
    log.append(("steps after edit", steps))
    log.append(("job status", jstat))
    real_ok = (src / "script.json").read_text() == real_before
    log.append(("real episode untouched", real_ok))
    for k, v in log:
        print(f"{k}: {v}")
    print("errors:", errs)
    print("screenshots:", out)
    ok = (not errs and real_ok and steps.get("writer") == "done" and steps.get("sound") == "queued"
          and dict(log).get("script saved") and dict(log).get("twist beat moved to backup")
          and dict(log).get("hook beat kept") and dict(log).get("cancelled banner")
          and dict(log).get("picture fix queued with reason") and dict(log).get("voice beat boxes") == 5)
    print("EDIT STAGES SELFTEST", "OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
