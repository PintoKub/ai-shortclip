#!/usr/bin/env python3
"""e2e_claude.py — ทดสอบทั้งสาย "ของจริง" รวม Claude animator ในโฟลเดอร์ scratch (ไม่แตะ DB/โปรเจกต์จริง)

ต่างจาก selfcheck.py ตรงที่ animator เรียก Claude จริง (เสีย quota ~$1.3–3) เพื่อพิสูจน์ brief/สัญญา/repair
ใช้บทที่มีอยู่ (fixture) และเสียง edge-tts ที่เลือกได้ (เช่น Niwat ตอน Premwadee ล่ม)

ใช้:  (ANTHROPIC_API_KEY ใน .env หรือ claude login)
      python3 tools/e2e_claude.py --script examples/zombie-school/ep1v2/script.json --voice th-TH-NiwatNeural
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from shortclip.pipeline import Pipeline  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", required=True)
    ap.add_argument("--voice", default="th-TH-NiwatNeural")
    ap.add_argument("--work", default=str(Path(os.environ.get("TMPDIR", "/tmp")) / "shortclip-e2e"))
    ap.add_argument("--ref", default="examples/zombie-school/ep1v2", help="episode whose composition is the series lock")
    args = ap.parse_args()
    t0 = time.time()
    work = Path(args.work)
    script = json.loads((ROOT / args.script).read_text())
    series, number = script["series"], int(script["episode"])
    shutil.rmtree(work, ignore_errors=True)
    for d in ("db", "config", "agents"):
        (work / d).mkdir(parents=True)
    for f in ("db/schema.sql", "config/models.json", "CLAUDE.md", "agents/writer.md"):
        shutil.copy2(ROOT / f, work / f)
    ref = ROOT / args.ref
    refdst = work / "projects" / series / ref.name
    (refdst / "composition").mkdir(parents=True)
    for name in ("index.html", "hyperframes.json", "package.json", "meta.json"):
        if (ref / "composition" / name).exists():
            shutil.copy2(ref / "composition" / name, refdst / "composition" / name)
    shutil.copytree(ref / "composition/assets", refdst / "composition/assets")
    ep = work / "projects" / series / f"ep{number}"
    ep.mkdir(parents=True)
    # Pre-make beats with the chosen voice; the orchestrator's sound step then reuses them.
    shutil.copy2(ROOT / args.script, ep / "script.json")
    print(f"[{time.time() - t0:5.0f}s] TTS {args.voice}", flush=True)
    r = subprocess.run(["python3", str(ROOT / "tools/tts_natural.py"), str(ep), "--voice", args.voice],
                       capture_output=True, text=True)
    print(r.stdout[-600:], r.stderr[-600:], flush=True)
    if r.returncode:
        return 1
    (ep / "script.json").unlink()
    pipe = Pipeline(work, work / "db/e2e.db")
    try:
        state = pipe.create(series, number, fixture=str(ROOT / args.script), requested_by="e2e")
        jid = state["job"]["id"]
        print(f"[{time.time() - t0:5.0f}s] gate1; approving", flush=True)
        state = pipe.advance(jid, "approve")
        print(f"[{time.time() - t0:5.0f}s] {state['job']['status']} {state['job']['gate']}", flush=True)
    except Exception as exc:
        print("E2E FAILED:", exc, flush=True)
        state = pipe.status(1)
    finally:
        for s in pipe.status(1)["steps"]:
            print(f"  {s['role']:9s} {s['status']:8s} attempt={s['attempt']} ${s['cost_usd'] or 0:.4f} "
                  f"{(s['error'] or '')[-300:]}", flush=True)
        pipe.close()
    ok = state["job"].get("gate") == "gate2_final"
    if ok:
        qa = json.loads((ep / "qa.json").read_text())
        print(json.dumps({"duration": qa["video"]["duration"], "lufs": qa["audio"]["lufs"],
                          "true_peak": qa["audio"]["true_peak"], "warns": qa["warns"],
                          "seconds": round(time.time() - t0)}, ensure_ascii=False))
    print("E2E OK" if ok else "E2E NOT OK", "— work dir:", work, flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
