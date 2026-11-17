#!/usr/bin/env python3
"""finalize.py — ประกอบ draft.mp4 + audio/mix.wav เป็น final.mp4 แล้วรัน QA gate

ใช้:  python3 tools/finalize.py projects/<series>/<ep>
ผลลัพธ์: renders/final.mp4, qa.json (ผ่าน/ไม่ผ่าน + เหตุผล)
"""
import json
import os
import re
import subprocess
import sys

import shutil

FFMPEG = os.environ.get("FFMPEG") or shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = os.environ.get("FFPROBE") or shutil.which("ffprobe") or "ffprobe"
TARGET_W, TARGET_H, TARGET_FPS = 1080, 1920, 30
MIN_DUR, MAX_DUR = 50.0, 70.0


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def probe(path):
    r = run([FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path])
    if r.returncode != 0:
        return None
    d = json.loads(r.stdout)
    v = next((s for s in d["streams"] if s["codec_type"] == "video"), {})
    a = next((s for s in d["streams"] if s["codec_type"] == "audio"), {})
    num, den = (v.get("r_frame_rate", "0/1").split("/") + ["1"])[:2]
    fps = float(num) / float(den or 1)
    return {
        "duration": float(d["format"].get("duration") or 0),
        "width": v.get("width"), "height": v.get("height"), "fps": round(fps, 3),
        "vcodec": v.get("codec_name"), "acodec": a.get("codec_name"),
        "has_audio": bool(a), "size": int(d["format"].get("size") or 0),
    }


def measure(path):
    """Integrated loudness + true peak with ffmpeg's loudnorm analysis (no extra tools)."""
    r = run([FFMPEG, "-hide_banner", "-nostats", "-i", path, "-af",
             "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json", "-vn", "-f", "null", "-"])
    txt = r.stderr[r.stderr.rfind("{"):r.stderr.rfind("}") + 1]
    try:
        d = json.loads(txt)
        lufs, peak = float(d["input_i"]), float(d["input_tp"])
    except (ValueError, KeyError):
        return {"raw": (r.stderr or "")[-300:], "lufs": None, "true_peak": None}
    return {"raw": f"{lufs:.1f} LUFS, true peak {peak:.1f} dBTP", "lufs": lufs, "true_peak": peak}


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    ep = os.path.abspath(sys.argv[1])
    draft = next((p for p in (
        os.path.join(ep, "renders", "draft.mp4"),
        os.path.join(ep, "composition", "renders", "draft.mp4"),
    ) if os.path.exists(p)), os.path.join(ep, "renders", "draft.mp4"))
    mix = os.path.join(ep, "audio", "mix.wav")
    out = os.path.join(ep, "renders", "final.mp4")
    os.makedirs(os.path.dirname(out), exist_ok=True)

    checks, fails, warns = [], [], []

    dp = probe(draft)
    ap = probe(mix)
    if not dp:
        print(f"FAIL: ไม่พบ draft ที่ใช้งานได้: {draft}")
        return 1

    checks.append({"check": "draft exists", "ok": True, "detail": f"{dp['size']/1e6:.1f} MB"})
    if (dp["width"], dp["height"]) != (TARGET_W, TARGET_H):
        fails.append(f"ขนาดภาพ {dp['width']}x{dp['height']} ไม่ใช่ {TARGET_W}x{TARGET_H}")
    if abs(dp["fps"] - TARGET_FPS) > 0.05:
        fails.append(f"fps {dp['fps']} ไม่ใช่ {TARGET_FPS}")
    if dp["duration"] < MIN_DUR or dp["duration"] > MAX_DUR:
        fails.append(f"ความยาว {dp['duration']:.1f}s ไม่ได้อยู่ในช่วง {MIN_DUR}-{MAX_DUR}s")
    checks.append({"check": "video spec", "ok": not fails, "detail": f"{dp['width']}x{dp['height']} {dp['fps']}fps {dp['duration']:.2f}s"})

    if not ap:
        fails.append("ไม่พบ audio/mix.wav")
    else:
        dur_delta = abs(ap["duration"] - dp["duration"])
        if dur_delta > 1.0:
            warns.append(f"เสียงกับภาพยาวต่างกัน {dur_delta:.2f}s (จะตัดตามภาพ)")

    # mux
    cmd = [FFMPEG, "-y", "-loglevel", "error", "-i", draft]
    if ap:
        cmd += ["-i", mix]
    cmd += ["-c:v", "copy"]
    if ap:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-af", "afade=t=out:st=%.2f:d=0.5" % max(0, dp["duration"] - 0.6), "-shortest"]
    cmd += ["-movflags", "+faststart", out]
    r = run(cmd)
    if r.returncode != 0:
        print("FAIL: ffmpeg mux error:\n", r.stderr[-800:])
        return 1

    fp = probe(out)
    checks.append({"check": "mux", "ok": bool(fp and fp["has_audio"]), "detail": f"{fp['duration']:.2f}s {fp['vcodec']}/{fp['acodec']}" if fp else "no output"})
    if not fp or not fp["has_audio"]:
        fails.append("ไฟล์ final ไม่มีเสียง")

    aud = measure(out) if fp else {}
    if aud.get("lufs") is not None:
        if not (-17.0 <= aud["lufs"] <= -12.0):
            warns.append(f"LUFS {aud['lufs']} อยู่นอกช่วง -17..-12")
        if aud.get("true_peak") is not None and aud["true_peak"] > -0.5:
            fails.append(f"true peak {aud['true_peak']} dBTP เสี่ยง clipping")

    qa = {
        "episode": ep,
        "final": out,
        "video": fp,
        "audio": aud,
        "checks": checks,
        "fails": fails,
        "warns": warns,
        "passed": not fails,
    }
    json.dump(qa, open(os.path.join(ep, "qa.json"), "w"), ensure_ascii=False, indent=1)

    print(json.dumps({k: qa[k] for k in ("passed", "fails", "warns")}, ensure_ascii=False, indent=1))
    print("final:", out, f"({fp['duration']:.2f}s, {fp['size']/1e6:.1f} MB)" if fp else "")
    print("audio:", aud.get("raw", "")[:200])
    return 0 if qa["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
