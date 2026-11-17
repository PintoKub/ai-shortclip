#!/usr/bin/env python3
"""karaoke.py — ซับคาราโอเกะ: ไฮไลต์ทีละคำตามเสียงพูด (สไตล์ TikTok) โดยไม่เรียก LLM

อ่าน shots.json (`shots[i].words = [{t,s,e}, ...]` จาก tools/tts_natural.py) แล้วเขียนลง
composition/index.html แบบ deterministic:
  - เนื้อในของ <p id="subN"> กลายเป็น <span class="kw" data-ks data-ke>คำ</span> ทีละคำ
    (ข้อความรวมยังตรงกับบททุกตัวอักษร — verify_composition เทียบหลังตัดแท็ก)
  - <style id="karaoke-style"> และ <script id="karaoke-fx"> ที่เพิ่ม tween ลง timeline ของ composition
    คำที่กำลังพูด = เหลืองสว่าง + ยกขึ้น 6px (ไม่ขยาย: การขยายทำให้คำทับคำข้าง ๆ), คำที่พูดแล้ว = เหลืองค้าง, คำที่ยังไม่ถึง = ขาว
ทำซ้ำได้ (idempotent): ลบของเดิมแล้วเขียนใหม่ทุกครั้ง ใช้หลัง retime ทุกครั้งที่เวลาเปลี่ยน

ใช้: python3 tools/karaoke.py projects/<series>/<ep> [--off]
"""
from __future__ import annotations

import html as html_lib
import json
import re
import sys
from pathlib import Path

STYLE_ID = "karaoke-style"
SCRIPT_ID = "karaoke-fx"
CSS = (f'<style id="{STYLE_ID}">'
       ".sub .kp{white-space:nowrap}"
       ".sub .kw{display:inline-block;color:#fff;white-space:pre}"
       "</style>")
# Reads the word spans once at load; every tween is placed at an absolute time on the paused
# composition timeline, so any seek renders the same frame (no rAF, no clock).
JS = """<script id="%s">(function(){
var id=%s,tl=window.__timelines&&window.__timelines[id];if(!tl)return;
var HI="#ffd84d",NOW="#ffe970";
document.querySelectorAll(".sub .kw").forEach(function(el){
 var s=parseFloat(el.dataset.ks),e=parseFloat(el.dataset.ke);if(!(e>s))return;
 tl.set(el,{color:"#ffffff",y:0},0);
 tl.to(el,{color:NOW,y:-6,duration:Math.min(.08,(e-s)/2),ease:"power2.out"},s);
 tl.to(el,{color:HI,y:0,duration:.12,ease:"power1.out"},Math.max(s+.08,e-.02));
});})();</script>"""


def _strip_old(html: str) -> str:
    html = re.sub(rf'<style id="{STYLE_ID}">.*?</style>\n?', "", html, flags=re.S)
    html = re.sub(rf'<script id="{SCRIPT_ID}">.*?</script>\n?', "", html, flags=re.S)
    return html


def plain(inner: str) -> str:
    return html_lib.unescape(re.sub(r"<[^>]+>", "", inner))


def _span(w: dict) -> str:
    return (f'<span class="kw" data-ks="{float(w["s"]):.3f}" data-ke="{float(w["e"]):.3f}">'
            f'{html_lib.escape(w["t"], quote=False)}</span>')


def spans_for(text: str, words: list[dict]) -> str | None:
    """Wrap each word of `text` (in order) in a timed span; keep everything else verbatim.

    Words between two spaces sit in one nowrap `.kp` phrase, so a long line can only wrap
    at the script's own spaces, never inside a Thai phrase. Returns None if the words do
    not cover the text in order (then the caller keeps the plain subtitle).
    """
    out, phrase, cur = [], [], 0

    def flush():
        if phrase:
            out.append('<span class="kp">' + "".join(phrase) + "</span>")
            phrase.clear()

    for w in words:
        idx = text.find(w["t"], cur)
        if idx < 0:
            return None
        gap = text[cur:idx]
        if gap:
            if gap.strip():
                phrase.append(html_lib.escape(gap, quote=False))
            else:
                flush()
                out.append(gap)
        phrase.append(_span(w))
        cur = idx + len(w["t"])
    rest = text[cur:]
    if rest.strip():
        phrase.append(html_lib.escape(rest, quote=False))
    flush()
    if rest and not rest.strip():
        out.append(rest)
    return "".join(out)


def apply(html: str, shots: dict, enabled: bool = True) -> tuple[str, int]:
    html = _strip_old(html)
    count = 0
    for i, shot in enumerate(shots["shots"], 1):
        pat = re.compile(rf'(<(\w+)\b[^>]*\bid="sub{i}"[^>]*>)(.*?)(</\2>)', re.S)
        m = pat.search(html)
        if not m:
            continue
        text = shot["subtitle"]
        inner = html_lib.escape(text, quote=False)
        if enabled and shot.get("words"):
            wrapped = spans_for(text, shot["words"])
            if wrapped is not None:
                inner, count = wrapped, count + 1
        html = html[:m.start()] + m.group(1) + inner + m.group(4) + html[m.end():]
    if enabled and count:
        comp = re.search(r'data-composition-id="([^"]*)"', html)
        comp_id = json.dumps(comp.group(1) if comp else "")
        html = html.replace("</head>", CSS + "\n</head>", 1)
        html = html.replace("</body>", (JS % (SCRIPT_ID, comp_id)) + "\n</body>", 1)
    return html, count


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    ep = Path(sys.argv[1]).resolve()
    enabled = "--off" not in sys.argv
    index = ep / "composition/index.html"
    shots = json.loads((ep / "shots.json").read_text())
    before = index.read_text()
    html, count = apply(before, shots, enabled)
    if html != before:  # unchanged file keeps its mtime, so an up-to-date draft is not re-rendered
        index.write_text(html)
    total = len(shots["shots"])
    print(json.dumps({"karaoke_lines": count, "lines": total, "enabled": enabled}))
    if enabled and count < total - 1:  # sub1 may be the hidden title card, others must all have words
        print(f"warning: only {count}/{total} subtitles got word timing", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
