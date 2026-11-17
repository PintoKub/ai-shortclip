# กฎของ repo นี้ (อ่านก่อนทำงานทุกครั้ง)

1. อ่าน `README.md` ก่อนแก้โค้ด/สร้างตอนใหม่
2. งานภาพ (composition/index.html) ให้ Claude Code ทำผ่าน `claude -p` ตาม BRIEF เท่านั้น — orchestrator ห้ามเรียก LLM เอง
3. ห้ามแตะ `DUR`, `const SH`, `data-start`, `data-duration` ด้วยมือ — ใช้ `tools/retime.py` จาก `shots.json`
4. เปลี่ยนเวลาหรือบทเมื่อมี composition แล้ว = ต้อง retime + render ใหม่ (1 pass แพง ~$1.3)
5. ห้ามสร้างเสียงใหม่เพียงเพื่อเปลี่ยนช่องไฟ — ใช้ `audio/beats/*.wav` ซ้ำ (edge-tts throttle 45→180 วิ)
6. `python3 tools/finalize.py` ต้องได้ 50–70 วิ 1080×1920 30fps และ QA ผ่านก่อนส่ง
7. ค่าใช้จ่าย: ทุก pass ของ Claude Code บันทึก `total_cost_usd` ลง `steps.cost_usd` เสมอ (notional, หัก quota Pro)
8. โมเดลตั้งใน `config/models.json` (alias `sonnet`/`opus`/`haiku` หรือ id ที่ gateway ของคุณมี) — ใช้ key ของคุณเองผ่าน `.env`
9. รัน `python3 tools/db_init.py` หลังจบตอน เพื่อให้ dashboard เห็นข้อมูลจริง
10. ห้าม `pkill -f 'claude -p'` (ฆ่า shell ตัวเอง) — เก็บ PID แล้ว terminate
11. สร้างตอนใหม่ผ่าน `orchestrator.py` เท่านั้น (ห้ามรัน tools ทีละตัวมือเพื่อผลิตตอนใหม่) — ตัวอย่างใน `examples/` ห้ามแก้ (เป็น fixture ของเทสต์)
12. แก้ `shortclip/` หรือ `tools/` แล้วต้องรัน `python3 -m unittest discover -s tests` ให้ผ่านก่อน commit
13. ห้ามเพิ่ม id ตายตัวต่อตอนใน `tools/retime.py` — ใช้สัญญา composition (`const SH`, `id="subN"`, `data-line`, `data-endcard`); ep1v2 ใน examples เป็น LEGACY ตัวเดียว
