# AI ShortClip — CLAUDE.md (โหลดอัตโนมัติทุกครั้งที่ `claude -p` รันในโฟลเดอร์นี้)

โปรเจกต์: ระบบสร้างคลิปสั้น "การ์ตูน 2D เล่าเรื่อง" ภาษาไทย 1080×1920 30fps ยาว 50–70 วิ/ตอน
สถาปัตยกรรม/แผนเต็ม: `PLAN.md`

## บทบาทของคุณ (Animator)
คุณรับ `script.json` + `shots.json` แล้วเขียน **HyperFrames composition** (HTML + Canvas + GSAP)
ทุกเฟรมต้องเป็นฟังก์ชันของเวลา `t` เท่านั้น (deterministic) — ห้ามใช้ `Date.now()`, `Math.random()` ที่ไม่ seed,
ห้าม `requestAnimationFrame` เป็นตัวขับภาพ; animation ทั้งหมดผูกกับ `gsap.timeline({paused:true})`
ที่ลงทะเบียนใน `window.__timelines["<data-composition-id>"]`

## กฎเหล็ก
1. **อ่าน skill ก่อนทำงานทุกครั้ง** (เท่าที่ติดตั้งใน `~/.claude/skills`): `hyperframes` (render contract) → `hyperframes-cli`;
   ถ้ามี `video-director` / `huashu-motion` / `rossark-motion-skill` ก็อ่านด้วย (กฎภาพ ภาษาโมชัน ตัวละครมีชีวิต)
2. **ฟอนต์**: ต้องประกาศ `@font-face` ชี้ `assets/fonts/*.ttf` (Kanit / Sarabun) — ฟอนต์ถูกคัดลอกมากับ composition อ้างอิง
   และ lint ของ HyperFrames ต้องเห็น `@font-face` ไม่งั้น fallback ผิด
3. **ตัวอักษรต้องอ่านออกบนมือถือ**: ตัวซับ ≥ 44px @1080, ตัวหัวเรื่อง ≥ 80px, ขอบ/เงาดำกันกลืนพื้น
4. **Gate ก่อนส่งงาน**: `hyperframes lint` ต้อง 0 error → `hyperframes check` → render draft
   → `hyperframes snapshot` ทุกบีต แล้วเปิดดูจริง (ผมจะตรวจซ้ำ)
5. **ห้ามล้น 90 วิ** และต้องมีครบ 5 บีต: hook (0–3) / setup / escalate ×3 / twist / cliff
6. ไฟล์ผลลัพธ์ที่ระบบตรวจ: `index.html` + `renders/draft.mp4` เท่านั้น (snapshot ไว้ดูเอง ไม่ต้องสร้าง render.json)
7. ซับคาราโอเกะ (ไฮไลต์ทีละคำ) ระบบใส่ให้เองหลังส่งงาน: เขียนซับเป็นข้อความล้วนใน `<p id="subN">`

เครื่องมือที่ต้องมี: `ffmpeg`/`ffprobe` ใน PATH, Node 22+ (`npx hyperframes`), Chromium
