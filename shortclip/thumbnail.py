"""Clickbait thumbnails (covers) for an episode: frames → AI ideas → rendered PNG/JPG → chosen cover.

Everything lives in <epdir>/thumbs/:
  frames/fNN.jpg   one still per shot (middle of the shot), 1080×1920, from renders/final.mp4 (free)
  ideas.json       AI suggestions: [{frame, headline, sub, template, color, why}] (one small Claude call)
  cover-N.jpg      rendered covers (Chromium screenshot of an HTML template, Kanit font)
  cover-N.json     the spec that made it (so it can be edited and re-rendered)
  selected.json    {"file": "cover-N.jpg", "frame_ms": ...} — used by the post step
"""
from __future__ import annotations

import html
import json
import os
import sys
import re
import shutil
import subprocess
from pathlib import Path

from .external import FFMPEG, StepError, claude_json, parse_claude_log

TEMPLATES = {
    "top": "ตัวใหญ่ด้านบน + แถบมืดไล่สี",
    "bottom": "แถบข้อความด้านล่าง",
    "center": "ตัวใหญ่กลางจอ + กรอบ",
    "split": "ข้อความเฉียง + ลูกศรชี้",
}
COLORS = {"yellow": "#ffe14d", "white": "#ffffff", "red": "#ff3b3b", "cyan": "#3ff0ff", "green": "#7dff4d"}
CHROME = os.environ.get("CHROME") or ""   # empty = Playwright's own Chromium (playwright install chromium)
SYS_PY = sys.executable                   # needs the `playwright` package (requirements.txt)


def tdir(epdir: Path) -> Path:
    d = Path(epdir).resolve() / "thumbs"
    (d / "frames").mkdir(parents=True, exist_ok=True)
    return d


def _video(epdir: Path) -> Path:
    for p in (Path(epdir) / "renders/final.mp4", Path(epdir) / "composition/renders/draft.mp4"):
        if p.is_file():
            return p
    raise StepError("ยังไม่มีคลิป (final.mp4) ให้ดึงภาพ — ทำขั้นภาพให้เสร็จก่อน")


COMP_PY = r"""
import asyncio, json, sys
from playwright.async_api import async_playwright
async def main(idx, outdir, times, chrome):
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path=chrome or None, args=["--no-sandbox", "--allow-file-access-from-files"])
        pg = await b.new_page(viewport={"width": 1080, "height": 1920})
        await pg.goto("file://" + idx)
        await pg.wait_for_function("window.__timelines && Object.keys(window.__timelines).length", timeout=30000)
        await pg.evaluate("document.fonts.ready")
        # clean art for a cover: hide timed text layers (title card, subtitles, labels); keep bubbles/props
        await pg.add_style_tag(content=".clip{display:none!important}")
        for i, t in enumerate(json.loads(times), 1):
            await pg.evaluate("(t)=>{const tl=Object.values(window.__timelines)[0];tl.pause();tl.seek(t,false)}", t)
            await pg.wait_for_timeout(80)
            await pg.screenshot(path=f"{outdir}/f{i:02d}.jpg", type="jpeg", quality=90)
        await b.close()
asyncio.run(main(*sys.argv[1:5]))
"""


def frames(epdir: Path, force=False) -> list[dict]:
    """One still per shot (middle of the shot; shot 1 just after the title card).
    Preferred source: the composition itself with every timed text layer hidden (clean art, no burned-in
    subtitles/title). Fallback: ffmpeg grabs from the rendered video."""
    epdir = Path(epdir).resolve()
    shots = json.loads((epdir / "shots.json").read_text())["shots"]
    comp = epdir / "composition/index.html"
    out = tdir(epdir) / "frames"
    stamp = out / "_source.json"
    srcfile = comp if comp.is_file() else _video(epdir)
    src = {"src": str(srcfile), "mtime": srcfile.stat().st_mtime}
    if not force and stamp.exists() and json.loads(stamp.read_text()) == src and list_frames(epdir):
        return list_frames(epdir)
    for f in out.glob("f*.jpg"):
        f.unlink()
    times = [(float(s["start"]) + float(s["end"])) / 2 if i else float(s["end"]) + 0.4 for i, s in enumerate(shots)]
    clean = False
    if comp.is_file():
        r = subprocess.run([SYS_PY, "-c", COMP_PY, str(comp), str(out), json.dumps(times), CHROME],
                           capture_output=True, text=True, timeout=300)
        clean = r.returncode == 0 and len(list(out.glob("f*.jpg"))) == len(times)
    if not clean:
        video = _video(epdir)
        for i, t in enumerate(times, 1):
            r = subprocess.run([str(FFMPEG), "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
                                "-q:v", "3", str(out / f"f{i:02d}.jpg")], capture_output=True, text=True, timeout=60)
            if r.returncode != 0:
                raise StepError("ffmpeg frame failed: " + r.stderr[-300:])
    rows = [{"file": f"f{i:02d}.jpg", "t": round(t, 2), "shot": i, "text": s.get("subtitle", ""), "clean": clean}
            for i, (t, s) in enumerate(zip(times, shots), 1)]
    (out / "index.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    stamp.write_text(json.dumps(src))
    return rows


def list_frames(epdir: Path) -> list[dict]:
    p = tdir(epdir) / "frames/index.json"
    return json.loads(p.read_text()) if p.exists() else []


def clean_spec(spec: dict, epdir: Path) -> dict:
    s = dict(spec or {})
    names = {f["file"]: f for f in list_frames(epdir)}
    frame = str(s.get("frame") or "")
    if frame not in names:
        raise StepError("เลือกภาพพื้นหลังจากเฟรมของคลิป")
    headline = re.sub(r"\s+", " ", str(s.get("headline") or "")).strip()
    if not headline:
        raise StepError("ใส่ข้อความหลักบนปก")
    if len(headline) > 40:
        raise StepError("ข้อความหลักยาวเกิน 40 ตัวอักษร — ปกที่คนกดคือคำสั้น ๆ")
    sub = re.sub(r"\s+", " ", str(s.get("sub") or "")).strip()[:60]
    template = s.get("template") if s.get("template") in TEMPLATES else "top"
    color = s.get("color") if s.get("color") in COLORS else "yellow"
    zoom = min(1.6, max(1.0, float(s.get("zoom") or 1.0)))
    fy = s.get("focus_y")
    focus_y = min(100, max(0, int(float(50 if fy is None else fy))))
    return {"frame": frame, "frame_t": names[frame]["t"], "headline": headline, "sub": sub, "template": template,
            "color": color, "zoom": round(zoom, 2), "focus_y": focus_y, "badge": str(s.get("badge") or "")[:16],
            "arrow": bool(s.get("arrow")), "emoji": str(s.get("emoji") or "")[:4]}


def _html(spec: dict, bg: Path) -> str:
    c = COLORS[spec["color"]]
    e = html.escape
    head = e(spec["headline"])
    sub = e(spec["sub"])
    badge = f'<div class="badge">{e(spec["badge"])}</div>' if spec["badge"] else ""
    emoji = f'<div class="emoji">{e(spec["emoji"])}</div>' if spec["emoji"] else ""
    arrow = ('<svg class="arrow" viewBox="0 0 200 200"><path d="M20 30 Q120 40 150 150" stroke="#ff2a2a" '
             'stroke-width="22" fill="none" stroke-linecap="round"/><path d="M95 140 L160 175 L172 100 Z" '
             'fill="#ff2a2a"/></svg>') if spec["arrow"] else ""
    # platform UI covers the bottom ~22% (caption/buttons): "bottom" stays above it
    pos = {"top": "top:150px", "bottom": "bottom:560px", "center": "top:50%;transform:translateY(-50%)",
           "split": "top:220px;transform:rotate(-6deg)"}[spec["template"]]
    shade = {"top": "linear-gradient(180deg,rgba(0,0,0,.78) 0%,rgba(0,0,0,0) 48%)",
             "bottom": "linear-gradient(0deg,rgba(0,0,0,.82) 0%,rgba(0,0,0,0) 50%)",
             "center": "radial-gradient(ellipse at center,rgba(0,0,0,.55) 0%,rgba(0,0,0,.15) 70%)",
             "split": "linear-gradient(160deg,rgba(0,0,0,.75) 0%,rgba(0,0,0,0) 55%)"}[spec["template"]]
    size = 150 if len(spec["headline"]) <= 10 else 124 if len(spec["headline"]) <= 18 else 100
    frame_box = "border:14px solid %s;border-radius:28px;padding:18px 26px;background:rgba(0,0,0,.35);" % c \
        if spec["template"] == "center" else ""
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{width:1080px;height:1920px;overflow:hidden;background:#000}}
.bg{{position:absolute;inset:0;background:url('{bg.as_uri()}') center {spec['focus_y']}%/cover no-repeat;
  transform:scale({spec['zoom']});transform-origin:center {spec['focus_y']}%;filter:saturate(1.35) contrast(1.12)}}
.shade{{position:absolute;inset:0;background:{shade}}}
.txt{{position:absolute;left:60px;right:60px;{pos};text-align:center}}
.box{{display:inline-block;{frame_box}}}
h1{{font:700 {size}px/1.12 'Kanit',sans-serif;color:{c};-webkit-text-stroke:10px #000;paint-order:stroke fill;
  text-shadow:0 10px 0 #000,0 14px 30px rgba(0,0,0,.8);letter-spacing:1px}}
h2{{margin-top:22px;display:inline-block;font:700 72px/1.22 'Kanit',sans-serif;color:#fff;background:#e0201b;
  padding:8px 28px;border-radius:16px;box-shadow:0 8px 0 #000}}
.badge{{position:absolute;right:40px;top:40px;font:700 58px/1 'Kanit',sans-serif;color:#000;background:{c};
  padding:16px 26px;border-radius:18px;border:6px solid #000;transform:rotate(4deg)}}
.emoji{{position:absolute;left:40px;top:{'1180px' if spec['template'] != 'bottom' else '330px'};font-size:190px;line-height:1;
  font-family:'Noto Color Emoji';filter:drop-shadow(0 10px 10px rgba(0,0,0,.6))}}
.arrow{{position:absolute;right:120px;top:760px;width:300px;height:300px;filter:drop-shadow(0 8px 0 #000)}}
</style></head><body><div class="bg"></div><div class="shade"></div>{arrow}{emoji}{badge}
<div class="txt"><div class="box"><h1>{head}</h1></div>{f'<br><h2>{sub}</h2>' if sub else ''}</div></body></html>"""


SHOT_PY = r"""
import sys, asyncio
from playwright.async_api import async_playwright
async def main(src, out, chrome):
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path=chrome or None, args=["--no-sandbox", "--allow-file-access-from-files"])
        pg = await b.new_page(viewport={"width": 1080, "height": 1920})
        await pg.goto("file://" + src)
        await pg.evaluate("document.fonts.ready")
        await pg.wait_for_timeout(150)
        await pg.screenshot(path=out, type="jpeg", quality=90)
        await b.close()
asyncio.run(main(*sys.argv[1:4]))
"""


def render(epdir: Path, spec: dict) -> dict:
    """Render one cover from a spec; returns {file, spec}. Free (local Chromium)."""
    epdir = Path(epdir)
    spec = clean_spec(spec, epdir)
    d = tdir(epdir)
    n = 1 + max([int(m.group(1)) for p in d.glob("cover-*.jpg") if (m := re.match(r"cover-(\d+)\.jpg", p.name))] or [0])
    page = d / f"cover-{n}.html"
    page.write_text(_html(spec, d / "frames" / spec["frame"]))
    out = d / f"cover-{n}.jpg"
    r = subprocess.run([SYS_PY, "-c", SHOT_PY, str(page), str(out), CHROME], capture_output=True, text=True, timeout=120)
    page.unlink(missing_ok=True)
    if r.returncode != 0 or not out.is_file():
        raise StepError("render ปกไม่สำเร็จ: " + (r.stderr or r.stdout)[-400:])
    if out.stat().st_size > 1_900_000:  # YouTube custom thumbnail limit is 2 MB
        subprocess.run([str(FFMPEG), "-v", "error", "-y", "-i", str(out), "-q:v", "5", str(out) + ".tmp.jpg"], timeout=60)
        os.replace(str(out) + ".tmp.jpg", out)
    (d / f"cover-{n}.json").write_text(json.dumps(spec, ensure_ascii=False, indent=1))
    return {"file": out.name, "spec": spec}


def covers(epdir: Path) -> list[dict]:
    d = tdir(epdir)
    out = []
    for p in sorted(d.glob("cover-*.jpg"), key=lambda p: int(re.findall(r"\d+", p.name)[0])):
        sp = p.with_suffix(".json")
        out.append({"file": p.name, "spec": json.loads(sp.read_text()) if sp.exists() else {}})
    return out


def select(epdir: Path, name: str) -> dict:
    d = tdir(epdir)
    if not re.fullmatch(r"cover-\d+\.jpg", name or "") or not (d / name).is_file():
        raise StepError("ไม่พบปกนี้")
    spec = json.loads((d / name).with_suffix(".json").read_text()) if (d / name).with_suffix(".json").exists() else {}
    data = {"file": name, "frame_ms": int(float(spec.get("frame_t") or 1.2) * 1000)}
    (d / "selected.json").write_text(json.dumps(data))
    return data


def selected(epdir: Path) -> dict | None:
    p = tdir(epdir) / "selected.json"
    if not p.exists():
        return None
    data = json.loads(p.read_text())
    return data if (p.parent / data.get("file", "")).is_file() else None


def delete(epdir: Path, name: str) -> None:
    d = tdir(epdir)
    if not re.fullmatch(r"cover-\d+\.jpg", name or ""):
        raise StepError("ชื่อไฟล์ไม่ถูกต้อง")
    (d / name).unlink(missing_ok=True)
    (d / name).with_suffix(".json").unlink(missing_ok=True)
    sel = d / "selected.json"
    if sel.exists() and json.loads(sel.read_text()).get("file") == name:
        sel.unlink()


IDEAS_PROMPT = """คุณคือคนทำปกคลิปสั้น (YouTube Shorts / TikTok / Reels) ภาษาไทยที่เก่งเรื่องทำให้คนหยุดนิ้วแล้วกด
อ่าน {script} (บทของตอนนี้) และดูภาพใน {frames_dir}/ (f01.jpg … ใช้ Read เปิดดูรูปจริง อย่างน้อย 8 รูป)
ข้อมูลเฟรม: {frames_json}

เขียนไฟล์ {out} เป็น JSON เท่านั้น: {{"ideas": [ ... {count} ข้อ ... ]}} แต่ละข้อ:
{{"frame": "fNN.jpg", "headline": "ข้อความหลัก ≤ 18 ตัวอักษร", "sub": "บรรทัดรอง ≤ 30 ตัว หรือ \\"\\"",
  "template": "top|bottom|center|split", "color": "yellow|white|red|cyan|green", "emoji": "อีโมจิ 1 ตัว หรือ \\"\\"",
  "arrow": true/false, "zoom": 1.0–1.4, "focus_y": 0–100 (ตำแหน่งแนวตั้งของจุดสนใจในภาพ), "why": "เหตุผลสั้น ๆ"}}

กฎ:
- ดึงความอยากรู้: คำถามค้าง ตัวเลข เดิมพัน ความขัดแย้ง หรือประโยคที่ตัวละครพูดแล้วช็อก — แต่ต้องเป็นเรื่องที่เกิดในตอนนี้จริง
  ห้ามสัญญาสิ่งที่ไม่มีในคลิป (ปกหลอกทำให้คนดูออกเร็วและแพลตฟอร์มลดการมองเห็น)
- ห้ามเฉลยตอนจบ/จุดหักมุม
- เลือกเฟรมที่เห็นหน้า/อารมณ์ตัวละครชัด สว่าง ไม่ใช่การ์ดชื่อเรื่องหรือการ์ดปิด เว้นแต่ไม่มีทางเลือก
- แต่ละข้อใช้มุมต่างกัน (คำถาม / ตัวเลข / คำพูดช็อก / เดิมพัน / ความลับ)
- ข้อความภาษาไทยพูดง่าย ไม่ใช้คำอังกฤษยาว สะกดถูกต้อง (ตรวจทุกคำ)
- "template":"bottom" วางข้อความกลางล่าง ใช้เมื่อหน้าตัวละครอยู่ครึ่งบน; "top" ใช้เมื่อตัวละครอยู่ครึ่งล่าง (ข้อความต้องไม่ทับหน้า)
{extra}"""


def ideas(epdir: Path, model: str, log_path: Path, count=5, extra="") -> dict:
    """One small Claude call that LOOKS at the frames and writes ideas.json. Returns {ideas, cost}."""
    epdir = Path(epdir)
    rows = frames(epdir)
    d = tdir(epdir)
    out = d / "ideas.json"
    out.unlink(missing_ok=True)
    compact = [{"file": r["file"], "t": r["t"], "บรรทัด": r["text"]} for r in rows]
    prompt = IDEAS_PROMPT.format(script=epdir / "script.json", frames_dir=d / "frames", out=out,
                                 frames_json=json.dumps(compact, ensure_ascii=False), count=max(2, min(6, int(count))),
                                 extra=("คำขอเพิ่มจากผู้ใช้: " + extra.strip()[:600]) if extra.strip() else "")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    result = claude_json(prompt, model=model, cwd=d, log_path=log_path, max_turns=25, timeout=900,
                         tools="Read,Write")
    if not out.exists():
        raise StepError("AI ไม่ได้เขียน ideas.json")
    raw = json.loads(out.read_text())
    good = []
    for it in (raw.get("ideas") if isinstance(raw, dict) else raw) or []:
        try:
            spec = clean_spec(it, epdir)
            spec["why"] = str(it.get("why") or "")[:200]
            good.append(spec)
        except (StepError, ValueError, TypeError):
            continue
    if not good:
        raise StepError("ไอเดียจาก AI ใช้ไม่ได้สักข้อ")
    out.write_text(json.dumps({"ideas": good, "model": model}, ensure_ascii=False, indent=1))
    return {"ideas": good, "cost_usd": float(result.get("total_cost_usd") or 0), "model": model}


def state(epdir: Path) -> dict:
    d = tdir(epdir)
    ideas_p = d / "ideas.json"
    return {"frames": list_frames(epdir), "ideas": json.loads(ideas_p.read_text()).get("ideas", []) if ideas_p.exists() else [],
            "covers": covers(epdir), "selected": selected(epdir), "templates": TEMPLATES, "colors": list(COLORS),
            "busy": (d / "ideas.running").exists()}
