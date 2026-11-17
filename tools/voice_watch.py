#!/usr/bin/env python3
"""voice_watch.py — รอให้เสียง edge-tts ที่เลือกกลับมาใช้ได้ แล้วเก็บไฟล์บีทไว้ก่อน

ใช้เมื่อเสียง (เช่น th-TH-PremwadeeNeural) ล่มเฉพาะตัว: ประโยคยาวกว่า 2-3 คำได้ NoAudioReceived
ทุกครั้ง แต่เสียงอื่นใช้ได้ (ไม่ใช่ throttle ทั้ง IP) — ถอย 45→180 วิไม่ช่วย ต้องรอเป็นชั่วโมง

ทุก --every วินาที: ลองพูดประโยคทดสอบ 1 ประโยค ถ้าได้เสียง → สร้าง audio/beats/<beat>.wav ที่ยังขาด
(ไฟล์เดียวกับที่ tools/tts_natural.py ใช้ซ้ำ) เมื่อครบทุกบีท:
  - ถ้ามี --resume-job N และมีไฟล์ db/.resume_ok_job<N> → รัน `orchestrator.py resume N`
  - ไม่งั้นจบเฉย ๆ (ให้คนสั่ง resume เอง)

ใช้:  python3 tools/voice_watch.py projects/<series>/<ep> [--every 900] [--hours 10] [--resume-job N]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import tts_natural as tn  # noqa: E402

PROBE = "เสียงทดสอบนี้ยาวพอที่จะรู้ว่าระบบพากย์ใช้งานได้ตามปกติแล้ว"


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def voice_ok(voice, tmp):
    out = tmp / "probe.mp3"
    out.unlink(missing_ok=True)
    r = subprocess.run([tn.EDGE, "--voice", voice, "--text", PROBE, "--write-media", str(out)],
                       capture_output=True, text=True, timeout=90)
    return r.returncode == 0 and out.exists() and out.stat().st_size > 8000


def missing_beats(ep, script):
    beats = {}
    for ln in script["lines"]:
        beats.setdefault(ln["beat"], []).append(ln)
    order = [b for b in tn.BEAT_ORDER if b in beats] + [b for b in beats if b not in tn.BEAT_ORDER]
    out = []
    for beat in order:
        bp = ep / "audio/beats" / f"{beat}.wav"
        if not (bp.exists() and bp.stat().st_size > 100000):
            out.append((beat, tn.PAUSE_MARK.join(x["text"] for x in beats[beat]), bp))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ep")
    ap.add_argument("--voice", default="th-TH-PremwadeeNeural")
    ap.add_argument("--every", type=int, default=900)
    ap.add_argument("--hours", type=float, default=10)
    ap.add_argument("--retry", type=int, default=240, help="wait after a probe passed but a beat failed")
    ap.add_argument("--resume-job", type=int)
    args = ap.parse_args()
    ep = Path(args.ep).resolve()
    script = json.loads((ep / "script.json").read_text())
    (ep / "audio/beats").mkdir(parents=True, exist_ok=True)
    tmp = ep / "audio/beats/.watch"
    tmp.mkdir(exist_ok=True)
    deadline = time.time() + args.hours * 3600
    while time.time() < deadline:
        todo = missing_beats(ep, script)
        if not todo:
            break
        if voice_ok(args.voice, tmp):
            log(f"{args.voice} OK — making {len(todo)} beat(s)")
            for beat, text, bp in todo:
                try:
                    tn.edge_say(text, str(bp), args.voice, tn.PROSODY.get(beat, "+0%"), retries=2)
                    tn.trim_edges(str(bp))
                    log(f"  [{beat}] {bp.stat().st_size} bytes, {tn.dur(str(bp)):.2f}s")
                except SystemExit as exc:
                    log(f"  [{beat}] failed: {str(exc)[-120:].strip()}")
                    failed = True
                    break
                time.sleep(12)
            else:
                failed = False
            if failed:
                log(f"voice flaky; next try in {args.retry}s")
                time.sleep(args.retry)
            continue
        log(f"{args.voice} still down; next probe in {args.every}s")
        time.sleep(args.every)
    todo = missing_beats(ep, script)
    if todo:
        log(f"gave up: still missing {[b for b, _, _ in todo]}")
        return 1
    log("all beats present")
    if args.resume_job:
        marker = ROOT / "db" / f".resume_ok_job{args.resume_job}"
        while not marker.exists() and time.time() < deadline:
            log(f"waiting for {marker.name} before resuming job {args.resume_job}")
            time.sleep(300)
        if marker.exists():
            log(f"resuming job {args.resume_job}")
            r = subprocess.run([sys.executable, str(ROOT / "orchestrator.py"), "resume", str(args.resume_job)],
                               cwd=ROOT)
            log(f"orchestrator exit {r.returncode}")
            return r.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
