#!/usr/bin/env python3
"""make_srt.py — สร้าง .srt จาก shots.json (ใช้กับ YouTube/TikTok หรือตรวจซับ)

ใช้: python3 tools/make_srt.py projects/<series>/<ep>
"""
import json
import os
import sys


def ts(t, sep=","):
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    if ms == 1000:
        ms, s = 0, s + 1
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def main():
    ep = os.path.abspath(sys.argv[1])
    data = json.load(open(os.path.join(ep, "shots.json")))
    out = os.path.join(ep, "renders", f"{os.path.basename(ep)}.srt")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    rows = []
    shots = data["shots"]
    for i, s in enumerate(shots, 1):
        end = s["end"]
        if i < len(shots):
            end = min(end, shots[i]["start"] - 0.02)   # กันซับทับกัน
        rows.append(f"{i}\n{ts(s['start'])} --> {ts(max(end, s['start'] + 0.3))}\n{s['subtitle']}\n")
    open(out, "w").write("\n".join(rows))
    print(out, len(rows), "บรรทัด")


if __name__ == "__main__":
    sys.exit(main())
