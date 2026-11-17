-- AI ShortClip — SQLite schema (Phase 1)
-- ใช้: sqlite3 ~/ai-shortclip/db/shortclip.db < db/schema.sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- 1 ซีรีส์ = 1 character sheet ที่ล็อกหน้าตัวละคร
CREATE TABLE IF NOT EXISTS series (
  id               INTEGER PRIMARY KEY,
  slug             TEXT UNIQUE NOT NULL,
  title            TEXT NOT NULL,
  genre            TEXT,
  character_sheet  TEXT,              -- JSON: rig ids, palette, proportions
  style_ref        TEXT,              -- path ของ composition ที่ใช้เป็นเรฟ
  created_at       TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS episodes (
  id             INTEGER PRIMARY KEY,
  series_id      INTEGER NOT NULL REFERENCES series(id) ON DELETE CASCADE,
  number         INTEGER NOT NULL,
  variant        TEXT NOT NULL DEFAULT '',  -- '' = v1, '2' = ep1v2 (รีเมค)
  title          TEXT,
  hook_type      TEXT,                -- countdown | forbidden | midscene | lie
  target_seconds REAL DEFAULT 60,
  status         TEXT DEFAULT 'draft',-- draft|script_ok|audio_ok|video_ok|qa_ok|published|failed
  script_json    TEXT,
  visuals_json   TEXT,
  shots_json     TEXT,
  duration       REAL,
  created_at     TEXT DEFAULT (datetime('now')),
  updated_at     TEXT DEFAULT (datetime('now')),
  UNIQUE (series_id, number, variant)
);

-- 1 คำสั่งของผู้ใช้ = 1 job
CREATE TABLE IF NOT EXISTS jobs (
  id          INTEGER PRIMARY KEY,
  episode_id  INTEGER REFERENCES episodes(id) ON DELETE CASCADE,
  kind        TEXT NOT NULL,          -- new_episode | rerender | repolish | publish
  status      TEXT NOT NULL DEFAULT 'queued',  -- queued|running|waiting_gate|done|failed|cancelled
  gate        TEXT,                   -- gate1_script | gate2_final
  requested_by TEXT,
  current_step TEXT,                -- role ที่รันอยู่/ค้างอยู่ ใช้ resume
  run_id      TEXT,                 -- UUID ต่อครั้งที่รัน กัน process เก่าเขียนทับ
  params_json TEXT,                 -- series/hook/idea/fixture ของ job (ไม่มี secrets)
  started_at  TEXT,
  finished_at TEXT,
  error       TEXT,
  created_at  TEXT DEFAULT (datetime('now'))
);

-- แต่ละ role ใน job (writer/director/animator/sound/editor/qa)
CREATE TABLE IF NOT EXISTS steps (
  id          INTEGER PRIMARY KEY,
  job_id      INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  role        TEXT NOT NULL,
  model       TEXT,                   -- เช่น cc/claude-fable-5-2, claude/claude-sonnet-5-5
  status      TEXT NOT NULL DEFAULT 'queued',
  attempt     INTEGER DEFAULT 1,
  position    INTEGER DEFAULT 0,      -- ลำดับใน pipeline
  pid         INTEGER,                -- process ที่รันอยู่ (ช่วย resume/ตรวจ zombie)
  log_path    TEXT,
  input_json  TEXT,
  output_json TEXT,
  usage_json  TEXT,                   -- input/output/cache tokens จาก --output-format json
  cost_usd    REAL DEFAULT 0,         -- notional (หัก quota ไม่ตัดบัตร)
  started_at  TEXT,
  finished_at TEXT,
  error       TEXT
);

CREATE TABLE IF NOT EXISTS artifacts (
  id          INTEGER PRIMARY KEY,
  episode_id  INTEGER REFERENCES episodes(id) ON DELETE CASCADE,
  step_id     INTEGER REFERENCES steps(id) ON DELETE SET NULL,
  kind        TEXT NOT NULL,          -- script|shots|mix|srt|draft|final|web|thumbnail
  path        TEXT NOT NULL,
  bytes       INTEGER,
  meta_json   TEXT,
  created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS events (
  id          INTEGER PRIMARY KEY,
  job_id      INTEGER REFERENCES jobs(id) ON DELETE CASCADE,
  level       TEXT NOT NULL DEFAULT 'info',  -- info|warn|error|gate
  message     TEXT NOT NULL,
  payload_json TEXT,
  created_at  TEXT DEFAULT (datetime('now'))
);

-- คลังเสียง: SFX/BGM ที่ render ไว้แล้ว ใช้ซ้ำได้
CREATE TABLE IF NOT EXISTS sounds (
  id        INTEGER PRIMARY KEY,
  kind      TEXT NOT NULL,            -- sfx|bgm|voice
  name      TEXT NOT NULL,            -- impact/deep, fit/bell/small_bell ...
  path      TEXT NOT NULL,
  dur       REAL,
  tags      TEXT,
  license   TEXT,
  source    TEXT,
  created_at TEXT DEFAULT (datetime('now')),
  UNIQUE (kind, name)
);

CREATE INDEX IF NOT EXISTS idx_steps_job   ON steps(job_id);
CREATE INDEX IF NOT EXISTS idx_events_job  ON events(job_id, created_at);
CREATE INDEX IF NOT EXISTS idx_art_ep      ON artifacts(episode_id, kind);

-- สรุปค่าใช้จ่ายต่อตอน (ใช้ใน dashboard)
CREATE VIEW IF NOT EXISTS v_episode_cost AS
SELECT e.id AS episode_id, e.series_id, e.number, e.variant, e.title, e.status, e.duration,
       COALESCE(SUM(s.cost_usd), 0) AS cost_usd,
       COALESCE(SUM(json_extract(s.usage_json, '$.output_tokens')), 0) AS output_tokens,
       MAX(s.finished_at) AS last_step_at
FROM episodes e
LEFT JOIN jobs j ON j.episode_id = e.id
LEFT JOIN steps s ON s.job_id = j.id
GROUP BY e.id;

-- คิวตอนถัดไป (dashboard)
CREATE VIEW IF NOT EXISTS v_queue AS
SELECT j.id, j.kind, j.status, j.gate, e.number, e.variant, e.title, j.created_at
FROM jobs j LEFT JOIN episodes e ON e.id = j.episode_id
WHERE j.status IN ('queued','running','waiting_gate')
ORDER BY j.created_at;

-- การวางเรื่องก่อนผลิต: ไอเดีย → คอนเซปต์หลายแบบ → เลือก+สรุป → series bible (ตัวละคร/ตอน/lore) → อนุมัติเป็นซีรีส์
CREATE TABLE IF NOT EXISTS plans (
  id           INTEGER PRIMARY KEY,
  status       TEXT NOT NULL DEFAULT 'new',  -- drafting_concepts|concepts_ready|refining|summary_ready|drafting_bible|bible_ready|approved|failed
  idea         TEXT,
  options_json TEXT,                  -- จำนวนแบบ, แนว, โทน, ผู้ชม, สถานที่, ต้องมี, ห้ามมี
  concepts_json TEXT,
  chosen_json  TEXT,                  -- คอนเซปต์ที่เลือก (+ที่ผู้ใช้แก้)
  summary_json TEXT,                  -- สรุปก่อนไปสร้าง bible
  bible_opts_json TEXT,               -- จำนวนตอน, ตัวละครที่อยากได้, จุดอิงจริง
  bible_json   TEXT,
  series_slug  TEXT,                  -- ซีรีส์ที่สร้างหลังอนุมัติ
  busy_step    TEXT,                  -- concepts|refine|bible ขณะ Claude ทำงาน
  pid          INTEGER,
  cost_usd     REAL DEFAULT 0,
  usage_json   TEXT,
  warnings_json TEXT,
  error        TEXT,
  created_at   TEXT DEFAULT (datetime('now')),
  updated_at   TEXT DEFAULT (datetime('now'))
);

-- การโพสต์ขึ้นแพลตฟอร์ม (ผู้ใช้กดเองทุกครั้ง)
CREATE TABLE IF NOT EXISTS posts (
  id            INTEGER PRIMARY KEY,
  episode_id    INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  platform      TEXT NOT NULL,          -- youtube|tiktok|instagram
  status        TEXT NOT NULL DEFAULT 'queued', -- queued|uploading|processing|inbox|published|published_private|failed
  options_json  TEXT,                   -- caption/title/privacy/toggles ที่ผู้ใช้เลือก
  remote_id     TEXT,                   -- youtube video id / tiktok publish_id / ig media id
  url           TEXT,
  response_json TEXT,
  error         TEXT,
  attempts      INTEGER DEFAULT 0,
  pid           INTEGER,
  posted_at     TEXT,
  created_at    TEXT DEFAULT (datetime('now')),
  updated_at    TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_posts_ep ON posts(episode_id, platform);
