"""Prompts generated from structured inputs, never from hidden conversation state."""
from __future__ import annotations

import json
from pathlib import Path

from .validate import BEATS, end_card_start


OPTIONAL_SKILLS = ("hyperframes", "hyperframes-cli", "video-director", "huashu-motion", "rossark-motion-skill")


def _skill_list(root: Path) -> str:
    """Claude Code skills that are actually installed (`npx hyperframes skills update` installs the
    HyperFrames ones; the rest are optional extras). CLAUDE.md is always last."""
    base = Path.home() / ".claude" / "skills"
    items = [f"~/.claude/skills/{n}/SKILL.md" for n in OPTIONAL_SKILLS if (base / n / "SKILL.md").exists()]
    items.append(str(Path(root) / "CLAUDE.md"))
    return "\n".join(f"{i}. {p}" for i, p in enumerate(items, 1))


def _rule_block(title: str, rules) -> str:
    rules = [r for r in (rules or []) if r.strip()]
    if not rules:
        return ""
    return f"\n## {title} (ผู้ใช้กำหนด ต้องทำตามทุกข้อ ถ้าขัดกับข้ออื่นให้ข้อนี้ชนะ)\n" + \
        "\n".join(f"- {r}" for r in rules) + "\n"


def writer_prompt(root: Path, epdir: Path, series: str, number: int, hook: str, idea: str,
                  rules=(), target_seconds=None) -> str:
    previous = sorted(p for p in (root / "projects" / series).glob("ep*/script.json") if p.parent != epdir)
    ref = previous[-1] if previous else root / "examples/zombie-school/ep1v2/script.json"
    return f"""คุณคือ Writer ของ AI ShortClip งานนี้สร้างบทตอนใหม่เท่านั้น
อ่านและทำตาม {root / 'agents/writer.md'} ทุกข้อ
อ่านบทอ้างอิงโครงสร้าง {ref} (อย่าคัดลอกเนื้อเรื่อง)
เขียนผลลัพธ์เป็น JSON ที่ valid ลงไฟล์ {epdir / 'script.json'} เท่านั้น ห้ามสร้างไฟล์อื่น

series = {series!r}, episode = {number}, hook_type = {hook!r}
แนวคิดจากผู้ใช้: {idea or 'ต่อเนื่องจากตอนก่อน โดยเปิดคำถามใหม่และไม่เฉลยคำถามเดิมซ้ำ'}
ถ้าเป็น EP.2 ขึ้นไป ให้อ่าน script.json ของตอนก่อนในซีรีส์เดียวกันเพื่อความต่อเนื่อง
ใช้ second person 'คุณ', 18±3 บรรทัด, 5 beats: {', '.join(BEATS)}
title_options ต้องมี 3 ชื่อ, title ที่เลือกต้องมี EP.{number}
บรรทัดแรกคือเสียงอ่านชื่อเรื่องสำหรับ TITLE CARD 0–2.8 วิ (สั้นมาก)
บทพูดห้ามมีภาษาอังกฤษ/วงเล็บ/อีโมจิ, ตัวเลขเขียนเป็นคำไทย
เขียน JSON ให้ครบ schema ใน writer.md แล้วอ่านไฟล์กลับมาตรวจ JSON อีกครั้ง
ตอบสั้น ๆ ว่าเขียนไฟล์แล้ว ห้ามวาง JSON ซ้ำในคำตอบ
{f"ความยาวเป้าหมาย {target_seconds} วิ (ใส่ใน target_seconds และปรับจำนวนคำให้พอดี)" if target_seconds else ""}{_rule_block("กฎเนื้อเรื่อง", rules)}"""


def animator_brief(root: Path, epdir: Path, shots: dict, ref_ep: Path | None, rules=()) -> str:
    rows = []
    for i, s in enumerate(shots["shots"], 1):
        rows.append(f"| {i:02d} | {s['line']} | {s['start']:.2f}–{s['end']:.2f} | "
                    f"{s['subtitle']} | {s.get('on_screen') or ''} |")
    duration = shots["duration"]
    end_card = end_card_start(shots)
    comp_id = epdir.name.replace("-", "_")
    ref_text = (f"อ่าน {ref_ep / 'composition/index.html'} และ reuse rig/palette/scene functions ให้หน้าตาตัวละครเหมือนเดิม"
                if ref_ep else "สร้าง character sheet ใหม่และบันทึก ../character_sheet.json")
    return f"""# ANIMATOR BRIEF — {epdir.name} ({duration:.3f} วิ)

คุณคือ Animator สร้าง HyperFrames composition เดียว 1080x1920 30fps ยาว {duration:.3f} วิ
ทำงานในโฟลเดอร์นี้เท่านั้น เสียงถูก mux ทีหลัง ห้ามแตะ ../audio/

## อ่านก่อนเขียน (บังคับ)
{_skill_list(root)}

## กฎเหล็ก
- root data-composition-id=\"{comp_id}\" data-duration=\"{duration:.3f}\"
- ห้าม <audio>, Date.now(), Math.random() ที่ไม่ seed, rAF ขับภาพ
- ทุกเฟรม deterministic จาก t, gsap.timeline({{paused:true}}), window.__timelines[\"{comp_id}\"]
- Canvas เดียว 1080x1920, @font-face จาก assets/fonts/, ซับ >=44px หัวเรื่อง >=80px
- ซับ bottom:300px max-width:900px, ไม่บังตัวละคร, sub1 ซ่อนเพราะ TITLE CARD มีชื่ออยู่แล้ว
- ทุกช็อตมีภาพ >=2 ชั้น, พื้นที่ดำ <25%, หน้าตัวละครต้องตรงอารมณ์ฉาก
- ห้ามมี HTML ไฟล์อื่นใน composition/

## สัญญากับระบบ (ระบบตรวจด้วยเครื่อง ถ้าไม่ตรงงานจะถูกตีกลับ)
- `const W=1080,H=1920,DUR={duration:g};` ต้องเขียนรูปแบบนี้เป๊ะในบรรทัดเดียว
- `const SH=[...];` = เวลาเริ่มของทุกช็อตตามตาราง ({len(shots['shots'])} ค่า) ตามด้วยเวลาเริ่มการ์ดปิด {end_card:g} รวม {len(shots['shots']) + 1} ค่า
- ซับบรรทัดที่ N เป็น `<p id="subN" class="clip sub" data-start data-duration>` ข้อความตรงตารางทุกตัวอักษร ห้ามแก้คำ
- ใส่ข้อความซับเป็นข้อความล้วน ไม่ต้องทำไฮไลต์ทีละคำเอง: ระบบจะใส่ซับคาราโอเกะ (`<span class="kw">` ต่อคำ + `<script id="karaoke-fx">`) ให้หลังคุณส่งงาน ห้ามตั้ง `color` ของ `.sub` ด้วย `!important` และห้ามซ่อน `.sub span`
- ซับเป็น 2 บรรทัดได้ (ตัดบรรทัดที่ช่องว่างของบทเท่านั้น) วางตัวละครและหน้าให้อยู่เหนือกล่องซับ (กล่องซับกินพื้นที่ล่างสุด ~520px)
- ป้าย/ตัวนับที่ต้องขึ้นพร้อมบรรทัดใด ให้ใส่ `data-line="lXX"` พร้อม data-start/data-duration ของบรรทัดนั้น
- ทุก element ของการ์ดปิดใส่ attribute `data-endcard` พร้อม data-start="{end_card:g}" data-duration="{duration - end_card:g}"
- ห้ามเขียนเวลาเป็นตัวเลขตายตัวในโค้ดวาด ให้อ่านจาก SH/DUR เท่านั้น (ระบบอาจ retime ภายหลังโดยไม่เรียกคุณ)

## Series lock
{ref_text}
{_rule_block("กฎภาพ", rules)}

## Timeline (ใช้ค่านี้เป๊ะ)
| # | line | เวลา | ซับ | on_screen |
|---|---|---|---|---|
{chr(10).join(rows)}

- TITLE CARD ช็อตแรก: {shots['title']}
- การ์ดปิดหลังซับสุดท้ายถึง {duration:.3f}: \"EP.{int(shots['episode']) + 1} เร็ว ๆ นี้\" + \"ติดตามตอนต่อไป\" + \"กดติดตาม\" พื้นหลังไม่ดำ

## ส่งมอบ (ทำเองให้ครบ)
1. เขียน index.html
2. npx hyperframes lint ต้อง 0 error
3. npx hyperframes render -q draft
4. ถ้าไฟล์มี timestamp ให้ rename เป็น renders/draft.mp4 ความยาว {duration:.3f} ±0.1 วิ
5. npx hyperframes check และ snapshot อย่างน้อย 6 เวลา แล้วดูภาพจริง
6. รายงานจำนวนช็อต/ปัญหาที่เหลือ/ความยาวไฟล์ ห้ามแตะเวลาจากตาราง
"""


def polish_prompt(epdir: Path, reason: str) -> str:
    return f"""อ่าน BRIEF.md และ index.html ในโฟลเดอร์นี้ แล้วแก้เฉพาะปัญหาต่อไปนี้: {reason}
ห้ามแตะ DUR, const SH, data-start, data-duration และข้อความซับทุกบรรทัด
ห้ามเปลี่ยน rig/palette ตัวละคร หลังแก้ต้อง lint 0 error, render draft ใหม่ และตรวจ snapshot
เวลาเดิมต้องเหมือนเดิมทุกค่า รายงานสิ่งที่เปลี่ยนสั้น ๆ
"""
