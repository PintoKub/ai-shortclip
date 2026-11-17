# ตั้งค่าการโพสต์อัตโนมัติ (YouTube · TikTok · Instagram)

ทุกอย่างทำในเว็บ: **dashboard → แท็บ ⑤ บัญชีโพสต์** (วิธีทำย่อ ๆ อยู่ในการ์ดแต่ละอันด้วย)
key และ token เก็บในเครื่องของคุณ ที่ `secrets/publish/` (สิทธิ์ 600, ไม่เข้า git) หน้าเว็บไม่แสดงค่าลับกลับมา

## YouTube (~3 นาที) — ใช้ Google OAuth client ของคุณเอง
1. สร้างโปรเจกต์ใน https://console.cloud.google.com แล้วเปิด YouTube Data API v3 (**Enable**) → สร้าง OAuth client (Desktop app) แล้วนำ Client ID/Secret ไปใส่ในแท็บ ⑤ → YouTube
2. ถ้า OAuth consent screen ยังเป็นโหมด *Testing*: Audience → Test users → เพิ่มอีเมลของบัญชีที่มีช่อง YouTube
3. แท็บ ⑤ → YouTube → **เชื่อมบัญชี** → กด "เปิดหน้าอนุญาตของ Google" → เลือกบัญชี → อนุญาต
4. หน้าสุดท้ายจะขึ้นว่าเปิดไม่ได้ (localhost) — คัดลอกลิงก์ทั้งบรรทัดจากแถบที่อยู่ มาวาง → **ยืนยัน**
- วิดีโอขึ้นเป็น **ส่วนตัว** ตามที่เลือกไว้ (เปลี่ยนเป็นสาธารณะใน YouTube Studio ทีหลัง)
- ตั้งแต่ 2 พ.ย. 2026 YouTube ไม่ล็อกคลิปจากแอปที่ยังไม่ผ่าน audit แล้ว ตั้งสาธารณะจากหน้านี้ได้เลยถ้าต้องการ
- โควตา API ฟรีพอโพสต์ได้หลายคลิปต่อวัน

## TikTok (~15 นาที + รอ TikTok อนุมัติแอป)
1. https://developers.tiktok.com → Manage apps → Connect an app (กรอกชื่อแอป, ไอคอน, Terms/Privacy URL ใช้ `https://YOUR-PUBLIC-URL/` ได้)
2. Add products: **Login Kit** + **Content Posting API** (เปิด *Direct Post* ด้วยถ้าต้องการโพสต์ตรง)
3. Scopes: `user.info.basic`, `video.upload`, `video.publish`
4. Login Kit → Redirect URI: `https://YOUR-PUBLIC-URL/oauth/tiktok/callback`
5. แท็บ ⑤ → TikTok → ใส่ **Client key / Client secret** → บันทึกแอป → **เชื่อมบัญชี**
- ระหว่างยังไม่ผ่าน audit:
  - **Inbox (แนะนำ)** ใช้ได้เลย — คลิปเข้าไปเป็นดราฟต์ แจ้งเตือนในแอป TikTok ให้กดโพสต์ (ใส่เพลงได้) ค้างได้ไม่เกิน 5 ดราฟต์/24 ชม.
  - **Direct Post** ได้แค่ "เฉพาะฉัน" และบัญชีต้องตั้งเป็น *ส่วนตัว* ตอนโพสต์ (ข้อกำหนดของ TikTok)
- ยื่น audit ได้ในหน้าแอป (หน้าโพสต์ของเราทำตามกฎ UX ของ TikTok แล้ว: แสดงชื่อบัญชี, เลือกการมองเห็นเองไม่มีค่าเริ่ม, ปุ่ม comment/duet/stitch, เนื้อหาเชิงพาณิชย์, Music Usage Confirmation, ตัวอย่างคลิป, ยืนยันก่อนส่ง)

## Instagram Reels (~15 นาที)
1. บัญชี IG ต้องเป็น **Professional** (Business หรือ Creator): แอป IG → Settings → Account type and tools
2. https://developers.facebook.com/apps → Create app → use case *Manage messaging & content on Instagram*
3. Instagram → API setup with Instagram login:
   - เพิ่มบัญชี IG ของคุณเป็น **Instagram tester** แล้วไปกดยอมรับในแอป IG (Settings → Website permissions → Tester invites)
   - Business login settings → OAuth redirect URI: `https://YOUR-PUBLIC-URL/oauth/instagram/callback`
4. คัดลอก **Instagram App ID** และ **Instagram App Secret** → แท็บ ⑤ → Instagram → บันทึกแอป → **เชื่อมบัญชี**
- โพสต์เป็นสาธารณะทันทีตามบัญชี (IG API ไม่มีโหมดส่วนตัว) — ระบบถามยืนยันก่อนทุกครั้ง
- IG ดึงไฟล์จากลิงก์ลับชั่วคราว `https://YOUR-PUBLIC-URL/pub/<ลายเซ็น>/video.mp4` (หมดอายุ 2 ชม., ใช้ได้เฉพาะ final.mp4 ของตอนที่อนุมัติแล้ว)
- token อยู่ได้ 60 วันและต่ออายุเองเมื่อมีการโพสต์ ถ้าไม่ได้ใช้เกิน 60 วันต้องเชื่อมใหม่

## วิธีโพสต์
③ ผลิตตอน → เลือกตอนที่ **อนุมัติคลิปสุดท้ายแล้ว** → ขั้น **5. โพสต์** → เลือกแพลตฟอร์ม → แก้ชื่อ/แคปชั่น/แฮชแท็ก → กดโพสต์ → ยืนยัน
สถานะอัปเดตเองในประวัติการโพสต์ (ล้ม → ปุ่มลองใหม่; TikTok/YouTube → ปุ่มเช็กสถานะ)
แฮชแท็กเริ่มต้นแก้ได้ที่ `config/publish.json` (`default_hashtags`)

## คำสั่ง (ถ้าไม่ใช้เว็บ)
```
python3 orchestrator.py publish accounts
echo '{"client_key":"…","client_secret":"…"}' | python3 orchestrator.py publish set-app tiktok   # อ่านจาก stdin
python3 orchestrator.py publish new <episode_id> youtube --options-json '{"title":"…","privacy":"private"}' --run
python3 orchestrator.py publish list
python3 tools/publish_selftest.py      # ทดสอบทั้งระบบกับเซิร์ฟเวอร์จำลอง (ไม่แตะบัญชีจริง)
```
