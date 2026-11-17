"""External processes (Claude Code, project tools, ffmpeg).

Secrets are inherited from the parent environment and are never written to logs.
Every stdout/stderr stream goes to a per-step log; only small summaries enter SQLite.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
ROOT = Path(__file__).resolve().parent.parent
FFMPEG = os.environ.get("FFMPEG") or shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = os.environ.get("FFPROBE") or shutil.which("ffprobe") or "ffprobe"


class StepError(RuntimeError):
    pass


def load_models(root=ROOT) -> dict:
    return json.loads((Path(root) / "config/models.json").read_text())


def model_choices(models: dict) -> set[str]:
    return {c["id"] for c in models.get("choices", [])}


def set_models(root, changes: dict) -> dict:
    """Update role→model in config/models.json; only curated, tested ids are accepted."""
    path = Path(root) / "config/models.json"
    models = json.loads(path.read_text())
    ok, roles = model_choices(models), set(models.get("roles", {}))
    for role, model in changes.items():
        if role not in roles:
            raise ValueError(f"unknown role {role!r} (roles: {', '.join(sorted(roles))})")
        if model not in ok:
            raise ValueError(f"model {model!r} is not in choices: {', '.join(sorted(ok))}")
        models[role] = model
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(models, ensure_ascii=False, indent=1))
    tmp.replace(path)
    return models


def load_dotenv(root=ROOT) -> None:
    """Load KEY=VALUE lines from <repo>/.env into os.environ (real environment variables win)."""
    path = Path(root) / ".env"
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_dotenv()


def api_key() -> str | None:
    """The user's own key: ANTHROPIC_API_KEY (Anthropic) or ANTHROPIC_AUTH_TOKEN (gateway/proxy)."""
    return os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN") or None


def process_env(require_key: bool = True) -> dict:
    """Only Claude steps need an API key; TTS/ffmpeg/HyperFrames run without one.

    Bring your own key via .env:
      ANTHROPIC_API_KEY=sk-ant-...            (Anthropic API)
    or a gateway / proxy that speaks the Anthropic API:
      ANTHROPIC_BASE_URL=https://your-gateway
      ANTHROPIC_AUTH_TOKEN=your-gateway-key
    If you are already logged in to Claude Code (`claude login`) no key is needed at all.
    """
    if require_key and not api_key() and not claude_logged_in():
        raise StepError("No Claude credentials: put ANTHROPIC_API_KEY=... in .env (see .env.example) "
                        "or run `claude login`; refusing to start Claude Code")
    env = {**os.environ}
    # a long Write (bible, composition) can take minutes per call on slow gateways
    env.setdefault("API_TIMEOUT_MS", "1200000")
    if os.environ.get("CHROME"):
        env["CHROME"] = os.environ["CHROME"]
    return env


def claude_logged_in() -> bool:
    return (HOME / ".claude/.credentials.json").is_file() or (HOME / ".claude.json").is_file() and \
        '"oauthAccount"' in (HOME / ".claude.json").read_text(errors="replace")


def run_process(cmd: list[str], *, cwd: Path, log_path: Path, timeout: int, env=None) -> dict:
    """Run without shell; return parsed output and keep complete logs on disk."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w") as log:
        log.write("COMMAND: " + " ".join(cmd) + "\n\n")
        log.flush()
        try:
            proc = subprocess.run(cmd, cwd=cwd, env=env or process_env(require_key=False), stdout=log,
                                  stderr=subprocess.STDOUT, text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            log.write(f"\nTIMEOUT after {timeout}s\n")
            raise StepError(f"timeout: {cmd[0]} after {timeout}s") from exc
    tail = log_path.read_text(errors="replace")[-5000:]
    if proc.returncode:
        raise StepError(f"{cmd[0]} exited {proc.returncode}: {tail[-1000:]}")
    return {"returncode": proc.returncode, "log": str(log_path), "tail": tail}


def rate_limit_wait(log_path: Path) -> int | None:
    """Seconds to wait when the Claude log is a 429 rate-limit (costs nothing), else None."""
    try:
        data = parse_claude_log(log_path)
    except (StepError, OSError):
        return None
    if data.get("api_error_status") != 429:
        return None
    m = re.search(r"reset after (?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+)s)?", str(data.get("result", "")))
    secs = 0
    if m and any(m.groups()):
        h, mi, se = (int(x or 0) for x in m.groups())
        secs = h * 3600 + mi * 60 + se
    return min(max(secs + 60, 120), 2 * 3600)


def claude_json(prompt: str, *, model: str, cwd: Path, log_path: Path, max_turns: int,
                timeout: int = 3600, tools="Read,Write,Edit,Bash,Glob,Grep", sleep=None) -> dict:
    import time
    sleep = sleep or time.sleep
    for attempt in range(3):
        # prompt may be a callable: a retry after a mid-run 429 can then tell Claude what is already on disk
        text = prompt() if callable(prompt) else prompt
        cmd = ["claude", "-p", text, "--model", model, "--allowedTools", tools,
               "--max-turns", str(max_turns), "--output-format", "json"]
        try:
            run_process(cmd, cwd=cwd, log_path=log_path, timeout=timeout, env=process_env(require_key=True))
            break
        except StepError:
            wait = rate_limit_wait(log_path)
            if wait is None or attempt == 2:
                raise
            # A 429 is rejected before any work: waiting is free, failing the job is not.
            with log_path.open("a") as log:
                log.write(f"\nRATE LIMITED (429): sleeping {wait}s then retrying\n")
            marker = log_path.with_suffix(".wait")  # the dashboard shows "waiting for quota until HH:MM"
            marker.write_text(json.dumps({"until": time.time() + wait, "attempt": attempt + 1}))
            try:
                sleep(wait)
            finally:
                marker.unlink(missing_ok=True)
    data = parse_claude_log(log_path)
    if data.get("subtype") != "success":
        raise StepError(f"Claude subtype={data.get('subtype')}: {str(data.get('result'))[-500:]}")
    return data


def parse_claude_log(log_path: Path) -> dict:
    """Parse a previous --output-format json log without calling Claude again."""
    raw = log_path.read_text(errors="replace")
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if "subtype" in data and "usage" in data:
            return data
    raise StepError("Claude returned no JSON result; see " + str(log_path))


def tool(name: str, epdir: Path, log_path: Path, timeout=3600, extra=()) -> dict:
    return run_process([sys.executable, str(ROOT / "tools" / name), str(epdir), *extra],
                       cwd=ROOT, log_path=log_path, timeout=timeout)


def hyperframes(comp: Path, action: str, log_path: Path, timeout=1800) -> dict:
    cmd = ["npx", "hyperframes", action]
    if action == "render":
        cmd += ["-q", "draft"]
    return run_process(cmd, cwd=comp, log_path=log_path, timeout=timeout)


def newest_draft(comp: Path) -> Path | None:
    files = list((comp / "renders").glob("*.mp4"))
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def make_web(epdir: Path, log_path: Path) -> Path:
    final = epdir / "renders/final.mp4"
    web = epdir / "renders/web.mp4"
    run_process([str(FFMPEG), "-y", "-loglevel", "error", "-i", str(final),
                 "-vf", "scale=720:1280", "-c:v", "libx264", "-preset", "slow", "-crf", "27",
                 "-maxrate", "1100k", "-bufsize", "2200k", "-c:a", "aac", "-b:a", "128k",
                 "-movflags", "+faststart", str(web)], cwd=epdir, log_path=log_path, timeout=1800)
    if web.stat().st_size >= 10_000_000:
        raise StepError(f"web.mp4 is {web.stat().st_size / 1e6:.1f} MB (must be <10 MB)")
    return web


def lint_zero(tail: str) -> bool:
    """True only for an exact `0 error(s)` summary; '10 error(s)' must not pass."""
    return re.search(r"(?<![0-9])0 error\(s\)", tail) is not None


def probe_duration(path: Path, cwd: Path, log_path: Path) -> float:
    result = run_process([str(FFPROBE), "-v", "error", "-show_entries",
                          "format=duration", "-of", "csv=p=0", str(path)], cwd=cwd,
                         log_path=log_path, timeout=60)
    try:
        return float(result["tail"].strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise StepError(f"cannot probe duration of {path}") from exc
