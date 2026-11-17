"""Deterministic quality gates. An LLM cannot mark its own work approved."""
from __future__ import annotations

import json
import re
from pathlib import Path

BEATS = ("hook", "setup", "escalate", "twist", "cliff")


class ValidationError(ValueError):
    pass


def require(cond, message):
    if not cond:
        raise ValidationError(message)


def read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"invalid JSON {path}: {exc}") from exc


def validate_script(data: dict, series: str, number: int) -> dict:
    require(isinstance(data, dict), "script must be an object")
    require(data.get("series") == series, f"series must be {series}")
    require(int(data.get("episode", -1)) == number, f"episode must be {number}")
    require(isinstance(data.get("title"), str) and data["title"].strip(), "title missing")
    require(f"EP.{number}" in data["title"], "title must contain EP.<n>")
    require(len(data.get("title_options", [])) == 3, "exactly 3 title_options required")
    require(data.get("hook_type") in {"countdown", "forbidden", "midscene", "lie"}, "invalid hook_type")
    lines = data.get("lines")
    require(isinstance(lines, list) and 15 <= len(lines) <= 21, "script needs 18±3 lines")
    ids = [x.get("id") for x in lines]
    require(len(ids) == len(set(ids)) and all(ids), "line ids must be unique")
    texts = [x.get("text", "").strip() for x in lines]
    require(all(texts), "empty narration line")
    require(all(x.get("beat") in BEATS for x in lines), "invalid/missing beat")
    require(set(x["beat"] for x in lines) == set(BEATS), "all five beats are required")
    require(all(isinstance(x.get("on_screen", ""), str) for x in lines), "on_screen must be a string")
    require(len(texts[0]) <= 80, "title narration is too long for a 2.8s title card")
    require(any(re.search(r"[0-9๐-๙]|หนึ่ง|สอง|สาม|สี่|ห้า|หก|เจ็ด|แปด|เก้า|สิบ|นาที|วินาที", t)
                for t in texts[:4]), "hook needs a number or countdown")
    require(not any(re.search(r"[A-Za-z]{4,}", t) for t in texts), "English/loanword in narration; move it on_screen")
    return data


def validate_shots(data: dict, script: dict) -> dict:
    shots = data.get("shots", [])
    require(15 <= len(shots) <= 21, "shots must contain 18±3 entries")
    require(len(shots) == len(script["lines"]), "shots/lines count mismatch")
    require(50 <= float(data.get("duration", 0)) <= 70, "timeline must be 50–70 seconds")
    require(data.get("width") == 1080 and data.get("height") == 1920 and data.get("fps") == 30,
            "timeline must be 1080x1920 30fps")
    require(float(shots[0]["end"]) <= 3.2, "TITLE CARD must end by ~2.8s (0.4s tolerance)")
    previous = 0.0
    for i, (shot, line) in enumerate(zip(shots, script["lines"]), 1):
        require(shot["line"] == line["id"], f"shot {i} line mismatch")
        require(shot["subtitle"] == line["text"], f"shot {i} subtitle differs from script")
        start, end = float(shot["start"]), float(shot["end"])
        require(start >= previous - 0.06 and end > start, f"shot {i} has invalid/non-monotonic timing")
        require(end - start <= 9.0, f"shot {i} exceeds 9s; alignment may have slipped")
        previous = start
    return data


def _attrs(tag: str) -> dict:
    return dict(re.findall(r'([\w-]+)="([^"]*)"', tag))


def end_card_start(shots: dict) -> float:
    return round(float(shots["shots"][-1]["end"]) + 0.4, 3)


def expected_sh(shots: dict, count: int):
    """`const SH` holds every shot start plus the end-card start (N+1 values).

    The delivered ep1v2 uses N values (its last shot shares the previous scene),
    so the existing length decides which of the two layouts is expected.
    """
    starts = [round(float(s["start"]), 3) for s in shots["shots"]]
    if count == len(starts) + 1:
        return starts + [end_card_start(shots)]
    if count == len(starts):
        return starts[:-1] + [end_card_start(shots)]
    return None


def verify_composition(html: str, shots: dict, tol: float = 0.011) -> list[str]:
    """Problems that make picture and sound drift apart. Empty list = in sync.

    Timing problems are repairable by tools/retime.py; a changed subtitle text is not.
    """
    import html as html_lib

    problems = []
    rows = shots["shots"]
    dur = float(shots["duration"])
    end = end_card_start(shots)

    def close(a, b):
        try:
            return abs(float(a) - float(b)) <= tol
        except (TypeError, ValueError):
            return False

    m = re.search(r"const W=\d+,H=\d+,DUR=([\d.]+)", html)
    if not m:
        problems.append("DUR: `const W=..,H=..,DUR=..` not found")
    elif not close(m.group(1), dur):
        problems.append(f"DUR is {m.group(1)}, timeline is {dur}")
    root = re.search(r'<[^>]*\bid="root"[^>]*>', html)
    if not root:
        problems.append('root: element id="root" not found')
    elif not close(_attrs(root.group(0)).get("data-duration"), dur):
        problems.append(f"root data-duration is not {dur}")
    m = re.search(r"const SH=\[([^\]]*)\];", html)
    if not m:
        problems.append("SH: `const SH=[...];` not found")
    else:
        try:
            have = [float(x) for x in m.group(1).split(",") if x.strip()]
        except ValueError:
            have = []
        want = expected_sh(shots, len(have))
        if want is None:
            problems.append(f"SH has {len(have)} values; need {len(rows)} or {len(rows) + 1}")
        elif any(not close(a, b) for a, b in zip(have, want)):
            problems.append("SH values differ from shots.json")
    for i, shot in enumerate(rows, 1):
        m = re.search(rf'<(\w+)\b[^>]*\bid="sub{i}"[^>]*>(.*?)</\1>', html, re.S)
        if not m:
            problems.append(f"sub{i}: element not found")
            continue
        attrs = _attrs(m.group(0).split(">", 1)[0])
        if not close(attrs.get("data-start"), shot["start"]) or \
                not close(attrs.get("data-duration"), float(shot["end"]) - float(shot["start"])):
            problems.append(f"sub{i} timing differs from shots.json")
        # Karaoke wraps each word in <span class="kw">; the spoken text must still match exactly.
        if html_lib.unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip() != shot["subtitle"].strip():
            problems.append(f"sub{i} text differs from script (not repairable by retime)")
    by_line = {s["line"]: s for s in rows}
    for tag in re.findall(r'<[^>]*\bdata-line="[^"]*"[^>]*>', html):
        attrs = _attrs(tag)
        shot = by_line.get(attrs["data-line"])
        name = attrs.get("id", attrs["data-line"])
        if not shot:
            problems.append(f"{name}: data-line {attrs['data-line']} is not in shots.json")
        elif not close(attrs.get("data-start"), shot["start"]) or \
                not close(attrs.get("data-duration"), float(shot["end"]) - float(shot["start"])):
            problems.append(f"{name} timing differs from line {attrs['data-line']}")
    for tag in re.findall(r'<[^>]*\sdata-endcard\b[^>]*>', html):
        attrs = _attrs(tag)
        if not close(attrs.get("data-start"), end) or not close(attrs.get("data-duration"), dur - end):
            problems.append(f"{attrs.get('id', 'endcard')} timing differs from end card {end}–{dur}")
    return problems


def write_manifest(epdir: Path, script: dict, shots: dict, qa: dict, steps: list[dict]) -> Path:
    passes = [{"role": s["role"], "model": s.get("model"), "attempt": s.get("attempt"),
               "cost_usd": s.get("cost_usd") or 0, "usage": s.get("usage_json")}
              for s in steps if (s.get("cost_usd") or 0) > 0]
    manifest = {
        "series": script["series"], "episode": script["episode"], "title": script["title"],
        "duration": qa["video"]["duration"], "width": 1080, "height": 1920, "fps": 30,
        "shots": len(shots["shots"]), "voice": {**(shots.get("voice") or {"engine": "edge"}), "mode": "per-beat continuous"},
        "qa": {"passed": qa["passed"], "lufs": qa.get("audio", {}).get("lufs"),
               "true_peak": qa.get("audio", {}).get("true_peak")},
        "cost": {"claude_code_passes": passes,
                 "total_usd_notional": round(sum(x["cost_usd"] for x in passes), 4),
                 "note": "notional from Claude Code; quota, not a card charge"},
        "files": {"final": "renders/final.mp4", "web": "renders/web.mp4",
                  "srt": f"renders/{epdir.name}.srt", "composition": "composition/index.html"},
    }
    path = epdir / "renders/manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    return path
