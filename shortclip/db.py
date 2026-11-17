"""SQLite access. No ORM and no third-party dependencies."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path

from .usage import normalize_rows

ROOT = Path(__file__).resolve().parent.parent
_SCHEMA = ROOT / "db/schema.sql"
_MIGRATIONS = (
    ("jobs", "current_step", "TEXT"),
    ("jobs", "run_id", "TEXT"),
    ("jobs", "params_json", "TEXT"),
    ("steps", "position", "INTEGER DEFAULT 0"),
    ("steps", "pid", "INTEGER"),
    ("steps", "log_path", "TEXT"),
    ("series", "rules_json", "TEXT"),
    ("series", "bible_json", "TEXT"),
    ("series", "plan_id", "INTEGER"),
)
_INIT_LOCK = threading.Lock()


def connect(path: str | Path | None = None, *, readonly: bool = False) -> sqlite3.Connection:
    path = Path(path or os.environ.get("SHORTCLIP_DB", ROOT / "db/shortclip.db"))
    if readonly:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(path, timeout=30, isolation_level=None)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=30000")
    return con


def init_db(con: sqlite3.Connection) -> None:
    with _INIT_LOCK:
        con.executescript(_SCHEMA.read_text())
        # Existing Phase-0 databases are upgraded in place.
        for table, column, decl in _MIGRATIONS:
            cols = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        normalize_rows(con)


def row_dict(row) -> dict | None:
    return dict(row) if row is not None else None


def json_field(value):
    if value is None or isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def event(con, job_id: int | None, message: str, *, level: str = "info", payload=None) -> None:
    con.execute(
        "INSERT INTO events(job_id,level,message,payload_json) VALUES(?,?,?,?)",
        (job_id, level, message, json.dumps(payload, ensure_ascii=False) if payload is not None else None),
    )


def artifact(con, episode_id: int, step_id: int | None, kind: str, path: str | Path, meta=None) -> None:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"artifact missing: {p}")
    con.execute(
        """INSERT INTO artifacts(episode_id,step_id,kind,path,bytes,meta_json) VALUES(?,?,?,?,?,?)
           ON CONFLICT DO NOTHING""",
        (episode_id, step_id, kind, str(p), p.stat().st_size,
         json.dumps(meta, ensure_ascii=False) if meta is not None else None),
    )
    # Older schemas have no uniqueness constraint: collapse exact duplicates.
    con.execute(
        "DELETE FROM artifacts WHERE episode_id=? AND kind=? AND path=? AND id NOT IN "
        "(SELECT MIN(id) FROM artifacts WHERE episode_id=? AND kind=? AND path=?)",
        (episode_id, kind, str(p), episode_id, kind, str(p)),
    )


def close_stale_steps(con, job_id: int) -> int:
    """A running step with no live owner is resumable, not successful."""
    rows = con.execute("SELECT id,pid FROM steps WHERE job_id=? AND status='running'", (job_id,)).fetchall()
    count = 0
    for row in rows:
        alive = False
        if row["pid"]:
            try:
                os.kill(row["pid"], 0)
                alive = True
            except (ProcessLookupError, PermissionError):
                alive = False
        if not alive:
            con.execute("UPDATE steps SET status='queued',pid=NULL,error='interrupted; queued for resume' WHERE id=?", (row["id"],))
            count += 1
    if count:
        event(con, job_id, f"recovered {count} interrupted step(s)", level="warn")
    return count
