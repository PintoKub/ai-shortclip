#!/usr/bin/env python3
"""ShortClip launcher: the entry point of `ShortClip.exe` (also runs from source: `python launcher.py`).

    ShortClip.exe                 start the dashboard and open it in the browser
    ShortClip.exe doctor          check that ffmpeg / Node / Claude Code / Chromium are installed
    ShortClip.exe setup           download Playwright's Chromium (covers / thumbnails)
    ShortClip.exe orchestrator …  run the orchestrator CLI (same as `python orchestrator.py …`)

The app re-runs itself through `sys.executable` (orchestrator.py, tools/*.py, `-m edge_tts`, `-c <code>`).
In the frozen .exe `sys.executable` IS this launcher, so it also behaves like a small `python` interpreter:

    ShortClip.exe [-u] [-X opt] path/to/script.py args…
    ShortClip.exe -m module args…
    ShortClip.exe -c "code" args…

The project files (orchestrator.py, shortclip/, tools/, dashboard/, config/, assets/ …) live next to the .exe as
plain source, so projects/, db/ and .env stay in that folder and the code can still be read and edited.
"""
from __future__ import annotations

import os
import runpy
import shutil
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
WINDOWS = os.name == "nt"
USAGE = __doc__.split("\n\n")[1]


# --------------------------------------------------------------------------- python-interpreter emulation
def _run_script(path: str, args: list[str]) -> None:
    script = Path(path).resolve()
    sys.argv = [str(script), *args]
    sys.path.insert(0, str(script.parent))          # like `python script.py`
    runpy.run_path(str(script), run_name="__main__")


def _run_module(name: str, args: list[str]) -> None:
    sys.argv = [name, *args]
    sys.path.insert(0, os.getcwd())                 # like `python -m module`
    runpy.run_module(name, run_name="__main__", alter_sys=True)


def _run_code(code: str, args: list[str]) -> None:
    sys.argv = ["-c", *args]
    sys.path.insert(0, "")
    exec(compile(code, "<string>", "exec"), {"__name__": "__main__", "__builtins__": __builtins__})


def python_like(argv: list[str]) -> bool:
    """Handle `[-u] [-X opt] [-B] (script.py | -m mod | -c code) args…`. Returns False if argv is not that."""
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-u", "-B", "-E", "-s", "-I", "-O"):
            if a == "-u":
                os.environ["PYTHONUNBUFFERED"] = "1"
                for stream in (sys.stdout, sys.stderr):
                    try:
                        stream.reconfigure(line_buffering=True, write_through=True)
                    except (AttributeError, ValueError):
                        pass
            i += 1
        elif a in ("-X", "-W"):
            i += 2                                  # options of the real interpreter; UTF-8 mode is built in
        elif a.startswith(("-X", "-W")) and len(a) > 2:
            i += 1
        elif a == "-m" and i + 1 < len(argv):
            _run_module(argv[i + 1], argv[i + 2:])
            return True
        elif a == "-c" and i + 1 < len(argv):
            _run_code(argv[i + 1], argv[i + 2:])
            return True
        elif a.lower().endswith((".py", ".pyw")):
            _run_script(a, argv[i + 1:])
            return True
        else:
            return False
    return False


# --------------------------------------------------------------------------- doctor / setup
def _which(name: str) -> str | None:
    env = {"ffmpeg": "FFMPEG", "ffprobe": "FFPROBE"}.get(name)
    if env and os.environ.get(env):
        return os.environ[env] if Path(os.environ[env]).exists() else None
    return shutil.which(name)


def _chromium_installed() -> bool:
    if os.environ.get("CHROME") and Path(os.environ["CHROME"]).exists():
        return True
    base = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if base and base != "0":
        roots = [Path(base)]
    elif WINDOWS:
        roots = [Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "ms-playwright"]
    elif sys.platform == "darwin":
        roots = [Path.home() / "Library/Caches/ms-playwright"]
    else:
        roots = [Path.home() / ".cache/ms-playwright"]
    return any(r.is_dir() and any(r.glob("chromium*")) for r in roots)


def doctor() -> list[tuple[str, bool, str]]:
    """(thing, ok, how-to-fix) for everything the pipeline needs outside this program."""
    sys.path.insert(0, str(ROOT))
    from shortclip.external import api_key, claude_logged_in
    win = WINDOWS
    checks = [
        ("ffmpeg", bool(_which("ffmpeg")),
         "winget install Gyan.FFmpeg  (then open a new window)" if win else "apt install ffmpeg / brew install ffmpeg"),
        ("ffprobe", bool(_which("ffprobe")), "comes with ffmpeg"),
        ("Node.js 22+ (npx)", bool(_which("node") and _which("npx")),
         "winget install OpenJS.NodeJS.LTS" if win else "https://nodejs.org"),
        ("Claude Code CLI (claude)", bool(_which("claude")), "npm i -g @anthropic-ai/claude-code"),
        ("Claude key or login", bool(api_key() or claude_logged_in()),
         f"put ANTHROPIC_API_KEY=... in {ROOT / '.env'}  (or run `claude login`)"),
        ("Chromium for covers", _chromium_installed(),
         ("ShortClip.exe setup" if FROZEN else "python launcher.py setup")),
    ]
    return checks


def print_doctor() -> bool:
    ok_all = True
    print(f"ShortClip folder: {ROOT}")
    for name, ok, fix in doctor():
        print(f"  [{'OK' if ok else '!!'}] {name}" + ("" if ok else f"   ->  {fix}"))
        ok_all &= ok
    return ok_all


def setup() -> int:
    """Download Playwright's Chromium with the bundled Playwright driver."""
    print("Downloading Chromium for Playwright (~150 MB)…", flush=True)
    return subprocess.call([sys.executable, "-m", "playwright", "install", "chromium"])


# --------------------------------------------------------------------------- dashboard
def first_run_files() -> None:
    env, example = ROOT / ".env", ROOT / ".env.example"
    if not env.exists() and example.exists():
        shutil.copyfile(example, env)
        print(f"Created {env} — open it in Notepad and put your ANTHROPIC_API_KEY there.", flush=True)


def start_dashboard(args: list[str]) -> None:
    os.chdir(ROOT)
    first_run_files()
    print("Checking tools…")
    if not print_doctor():
        print("Some tools are missing: the dashboard still starts, but those steps will fail until they are installed.\n")
    port = os.environ.get("SHORTCLIP_PORT", "8099")
    if "--port" in args and args.index("--port") + 1 < len(args):
        port = args[args.index("--port") + 1]
    if not os.environ.get("SHORTCLIP_NO_BROWSER") and "--no-browser" not in args:
        threading.Timer(1.5, webbrowser.open, (f"http://127.0.0.1:{port}",)).start()
    args = [a for a in args if a != "--no-browser"]
    print(f"Password: see the line below on first start, later in {ROOT / '.dashboard_pass'}")
    print("Close this window (or press Ctrl+C) to stop ShortClip.\n", flush=True)
    _run_script(str(ROOT / "dashboard" / "server.py"), args)


def _pause_if_double_clicked() -> None:
    """Keep the console open after an error when the .exe was started from Explorer."""
    if WINDOWS and FROZEN and sys.stdin and sys.stdin.isatty():
        try:
            input("\nPress Enter to close…")
        except (EOFError, KeyboardInterrupt):
            pass


def main(argv: list[str]) -> int:
    if argv and python_like(argv):
        return 0
    cmd, rest = (argv[0], argv[1:]) if argv else ("dashboard", [])
    if cmd in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    if cmd == "doctor":
        return 0 if print_doctor() else 1
    if cmd == "setup":
        return setup()
    if cmd == "orchestrator":
        os.chdir(ROOT)
        _run_script(str(ROOT / "orchestrator.py"), rest)
        return 0
    if cmd == "dashboard" or cmd.startswith("--"):
        start_dashboard(rest if cmd == "dashboard" else argv)
        return 0
    print(f"unknown command {cmd!r}\n\n{USAGE}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()   # child processes of libraries that use multiprocessing re-enter here
    sys.path.insert(0, str(ROOT))
    from shortclip.compat import ensure_utf8_mode
    ensure_utf8_mode()
    try:
        code = main(sys.argv[1:])
    except SystemExit as exc:       # scripts run in-process end with sys.exit(…)
        code = exc.code
        if code not in (None, 0) and not isinstance(code, int):
            print(code, file=sys.stderr)
            code = 1
    except KeyboardInterrupt:
        code = 130
    except Exception:
        import traceback
        traceback.print_exc()
        if not sys.argv[1:]:
            _pause_if_double_clicked()
        code = 1
    sys.exit(code or 0)
