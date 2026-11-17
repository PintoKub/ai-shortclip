#!/usr/bin/env python3
"""retime.py — เขียนเวลาใน composition/index.html ใหม่ให้ตรงกับ shots.json (ไม่แตะโค้ดวาดภาพ)

สัญญาของ composition (ใช้ได้ทุกตอน ไม่ผูกกับเลขบรรทัดของตอนใด):
  - const W=1080,H=1920,DUR=<วินาที>
  - <div id="root" ... data-duration="<วินาที>">
  - const SH=[...];            เวลาเริ่มทุกช็อต + เวลาเริ่มการ์ดปิด (N+1 ค่า)
  - <p id="subN" data-start data-duration>   ซับบรรทัดที่ N
  - data-line="lXX"            element ใดก็ตามที่ต้องขึ้นพร้อมบรรทัด lXX (ป้าย/ตัวนับ)
  - data-endcard               element ของการ์ดปิด (เริ่มหลังซับสุดท้าย 0.4 วิ ถึงจบ)

ใช้: python3 tools/retime.py projects/<series>/<ep>
exit 1 ถ้าเขียนแล้วยังไม่ตรง (เช่น ข้อความซับถูกแก้ ซึ่ง retime ซ่อมไม่ได้)
"""
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shortclip.validate import end_card_start, expected_sh, verify_composition  # noqa: E402

# composition ที่ส่งแล้วก่อนมี data-line/data-endcard: ผูกด้วย id ตามเดิม
LEGACY = {
    "ep1v2": {"lines": {"chip1": "l03", "chip2": "l05", "chip3": "l09", "chip4": "l20"},
              "endcard": ["cd1", "e1", "e2", "e3", "e4"]},
}


def set_times(tag, start, dur):
    tag = re.sub(r'data-start="[^"]*"', f'data-start="{start:g}"', tag)
    return re.sub(r'data-duration="[^"]*"', f'data-duration="{dur:g}"', tag)


def retime(html, shots):
    rows = shots["shots"]
    dur = float(shots["duration"])
    end = end_card_start(shots)
    by_line = {s["line"]: s for s in rows}

    def span(shot):
        return float(shot["start"]), round(float(shot["end"]) - float(shot["start"]), 3)

    html = re.sub(r"(const W=\d+,H=\d+,DUR=)[\d.]+", lambda m: f"{m.group(1)}{dur:g}", html)
    html = re.sub(r'(<[^>]*\bid="root"[^>]*\bdata-duration=")[^"]*', lambda m: f"{m.group(1)}{dur:g}", html)

    def sh(m):
        count = len([x for x in m.group(1).split(",") if x.strip()])
        want = expected_sh(shots, count) or expected_sh(shots, len(rows) + 1)
        return "const SH=[" + ",".join(f"{v:g}" for v in want) + "];"
    html = re.sub(r"const SH=\[([^\]]*)\];", sh, html)

    for i, shot in enumerate(rows, 1):
        html = re.sub(rf'<[^>]*\bid="sub{i}"[^>]*>', lambda m, s=shot: set_times(m.group(0), *span(s)), html)

    def by_data_line(m):
        shot = by_line.get(re.search(r'data-line="([^"]*)"', m.group(0)).group(1))
        return set_times(m.group(0), *span(shot)) if shot else m.group(0)
    html = re.sub(r'<[^>]*\bdata-line="[^"]*"[^>]*>', by_data_line, html)
    html = re.sub(r'<[^>]*\sdata-endcard\b[^>]*>',
                  lambda m: set_times(m.group(0), end, round(dur - end, 3)), html)

    comp_id = re.search(r'data-composition-id="([^"]*)"', html)
    legacy = LEGACY.get(comp_id.group(1) if comp_id else "", {})
    for eid, lid in legacy.get("lines", {}).items():
        if lid in by_line:
            html = re.sub(rf'<[^>]*\bid="{eid}"[^>]*>',
                          lambda m, s=by_line[lid]: set_times(m.group(0), *span(s)), html)
    for eid in legacy.get("endcard", []):
        html = re.sub(rf'<[^>]*\bid="{eid}"[^>]*>',
                      lambda m: set_times(m.group(0), end, round(dur - end, 3)), html)
    return html


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    ep = Path(sys.argv[1]).resolve()
    html_path = ep / "composition/index.html"
    shots = json.loads((ep / "shots.json").read_text())
    (ep / "backup").mkdir(exist_ok=True)
    shutil.copy2(html_path, ep / "backup/index.pretime.html")
    html = retime(html_path.read_text(), shots)
    html_path.write_text(html)
    problems = verify_composition(html, shots)
    print(f"เขียน {html_path} (DUR={shots['duration']}, การ์ดปิด {end_card_start(shots)}–{shots['duration']})")
    for p in problems:
        print("  ! ยังไม่ตรง:", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
