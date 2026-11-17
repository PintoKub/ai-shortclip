#!/usr/bin/env python3
"""db_init.py — สร้าง/อัปเดต SQLite ของ AI ShortClip แล้ว import ของที่ทำเสร็จแล้ว

ใช้:  python3 tools/db_init.py [--db db/shortclip.db]
อ่าน:  db/schema.sql, projects/*/*/renders/manifest.json, script.json, shots.json,
ไม่เรียก LLM, ไม่แตะไฟล์โปรเจกต์, รันซ้ำได้ (idempotent)
"""
import argparse, glob, json, os, sqlite3, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CLAUDE_LOGS = {}   # (episode-dir, role) -> claude JSON log to import cost from (none by default)


def load_json(p):
    try:
        return json.loads(Path(p).read_text())
    except Exception:
        return None


def ensure_db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript((ROOT / "db/schema.sql").read_text())
    return con


def upsert_series(con):
    con.execute(
        "INSERT INTO series(slug,title,genre) VALUES(?,?,?) "
        "ON CONFLICT(slug) DO UPDATE SET title=excluded.title",
        ("zombie-school", "ซอมบี้บุกโรงเรียน", "สยอง/ตลก/เอาชีวิตรอด"),
    )
    return con.execute("SELECT id FROM series WHERE slug='zombie-school'").fetchone()[0]


def import_episode(con, sid, epdir):
    ep_id_slug = epdir.name                      # ep1 / ep1v2
    man = load_json(epdir / "renders/manifest.json") or {}
    script = load_json(epdir / "script.json") or {}
    shots = load_json(epdir / "shots.json") or {}
    number = int("".join(c for c in ep_id_slug.split("v")[0] if c.isdigit()) or 0)
    variant = ep_id_slug.split("v")[1] if "v" in ep_id_slug else ""
    title = script.get("title") or man.get("title") or ep_id_slug
    if variant:
        title = f"{title} (v{variant})"
    # ตอนที่ orchestrator สร้างเองมี params_json: DB เป็นแหล่งจริง ห้าม import ทับสถานะ
    owned = con.execute(
        "SELECT e.id FROM episodes e JOIN jobs j ON j.episode_id=e.id JOIN series s ON s.id=e.series_id "
        "WHERE s.id=? AND e.number=? AND e.variant=? AND j.params_json IS NOT NULL",
        (sid, int("".join(c for c in ep_id_slug.split("v")[0] if c.isdigit()) or 0),
         ep_id_slug.split("v")[1] if "v" in ep_id_slug else "")).fetchone()
    if owned:
        row = con.execute("SELECT title,status,duration FROM episodes WHERE id=?", (owned[0],)).fetchone()
        return owned[0], row[0], row[1], row[2]
    final = epdir / "renders/final.mp4"
    status = "qa_ok" if final.exists() else ("video_ok" if (epdir / "renders/web.mp4").exists() else "draft")
    duration = man.get("duration") or man.get("duration_s")

    con.execute(
        "INSERT INTO episodes(series_id,number,variant,title,status,script_json,shots_json,duration,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,datetime('now')) "
        "ON CONFLICT(series_id,number,variant) DO UPDATE SET title=excluded.title,status=excluded.status,"
        "script_json=excluded.script_json,shots_json=excluded.shots_json,duration=excluded.duration,"
        "updated_at=datetime('now')",
        (sid, number, variant, title, status,
         json.dumps(script, ensure_ascii=False) if script else None,
         json.dumps(shots, ensure_ascii=False) if shots else None,
         duration),
    )
    eid = con.execute("SELECT id FROM episodes WHERE series_id=? AND number=? AND variant=?",
                      (sid, number, variant)).fetchone()[0]

    # job + steps (cost/usage จาก log จริงของ Claude Code)
    existing = con.execute("SELECT id FROM jobs WHERE episode_id=? AND kind='new_episode'", (eid,)).fetchone()
    if existing:
        jid = existing[0]
    else:
        cur = con.execute(
            "INSERT INTO jobs(episode_id,kind,status,started_at,finished_at) "
            "VALUES(?,'new_episode','done',datetime('now'),datetime('now'))", (eid,))
        jid = cur.lastrowid

    scratch = Path(os.environ.get("SHORTCLIP_CLAUDE_LOGS") or ROOT / "db" / "claude-logs")
    for (slug, role), fname in CLAUDE_LOGS.items():
        if slug != ep_id_slug:
            continue
        d = load_json(scratch / fname)
        if not d:
            continue
        if con.execute("SELECT 1 FROM steps WHERE job_id=? AND role=?", (jid, role)).fetchone():
            continue
        con.execute(
            "INSERT INTO steps(job_id,role,model,status,usage_json,cost_usd,started_at,finished_at) "
            "VALUES(?,?,?,?,?,?,datetime('now'),datetime('now'))",
            (jid, role, d.get("model") or "claude-sonnet-5-5", d.get("subtype", "success"),
             json.dumps(d.get("usage", {})), float(d.get("total_cost_usd") or 0)),
        )

    # artifacts
    for kind, rel in [("final", "renders/final.mp4"), ("web", "renders/web.mp4"),
                      ("draft", "renders/draft.mp4"), ("srt", f"renders/{epdir.name}.srt"),
                      ("mix", "audio/mix.wav"), ("shots", "shots.json"), ("script", "script.json")]:
        p = epdir / rel
        if p.exists() and not con.execute(
                "SELECT 1 FROM artifacts WHERE episode_id=? AND path=?", (eid, str(p))).fetchone():
            con.execute("INSERT INTO artifacts(episode_id,kind,path,bytes) VALUES(?,?,?,?)",
                        (eid, kind, str(p), p.stat().st_size))
    return eid, title, status, duration


def import_sounds(con, epdir):
    for p in sorted((epdir / "audio").rglob("*.wav")):
        kind = "voice" if p.parent.name == "beats" else "sfx"
        name = f"{p.parent.name}/{p.stem}" if p.parent.name != "audio" else p.stem
        if con.execute("SELECT 1 FROM sounds WHERE kind=? AND name=?", (kind, name)).fetchone():
            continue
        con.execute("INSERT INTO sounds(kind,name,path,license) VALUES(?,?,?,?)",
                    (kind, name, str(p), "generated"))
    bgm = epdir / "audio/bgm_bed.wav"
    if bgm.exists() and not con.execute("SELECT 1 FROM sounds WHERE kind='bgm'").fetchone():
        con.execute("INSERT INTO sounds(kind,name,path,license) VALUES('bgm','bgm_bed',?,'generated')",
                    (str(bgm),))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "db/shortclip.db"))
    a = ap.parse_args()
    con = ensure_db(Path(a.db))
    sid = upsert_series(con)
    rows = []
    for epdir in sorted((ROOT / "projects").glob("*/*")):
        if (epdir / "script.json").exists():
            rows.append(import_episode(con, sid, epdir))
            import_sounds(con, epdir)
    con.commit()

    print(f"db = {a.db}")
    for eid, title, status, dur in rows:
        print(f"  ep#{eid} {title[:48]:48s} {status:8s} {dur}")
    print("\n-- v_episode_cost --")
    for r in con.execute("SELECT number,title,status,round(duration,2),round(cost_usd,4),output_tokens "
                         "FROM v_episode_cost ORDER BY number"):
        print("  ", r)
    print("\n-- counts --")
    for t in ("episodes", "jobs", "steps", "artifacts", "sounds"):
        print(f"   {t:10s}", con.execute(f"SELECT count(*) FROM {t}").fetchone()[0])
    print("   total cost", con.execute("SELECT round(sum(cost_usd),4) FROM steps").fetchone()[0])


if __name__ == "__main__":
    sys.exit(main())
