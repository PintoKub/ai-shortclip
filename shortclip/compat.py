"""Small cross-platform helpers so the pipeline and dashboard also run on Windows (and in the .exe).

Why this exists:
- `os.kill(pid, 0)` is a harmless liveness probe on POSIX, but on Windows it calls TerminateProcess and
  KILLS the process. Use `pid_alive()` instead.
- `fcntl` does not exist on Windows; `try_lock()` uses `msvcrt.locking` there.
- `npx` / `claude` are `.cmd` shims on Windows that `subprocess` cannot start by bare name; `resolve_cmd()`
  turns them into a full path first.
"""
from __future__ import annotations

import os
import shutil
import subprocess

WINDOWS = os.name == "nt"


def pid_alive(pid) -> bool:
    """True if a process with this pid is running. Never sends a signal / never kills anything."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if WINDOWS:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        PROCESS_QUERY_LIMITED_INFORMATION, STILL_ACTIVE = 0x1000, 259
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False  # gone, or not ours (same rule as POSIX below)
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)   # signal 0 = existence probe on POSIX (NOT on Windows, see above)
        return True
    except OSError:       # gone, or a recycled pid owned by another user: either way not our worker
        return False


def try_lock(handle) -> bool:
    """Non-blocking exclusive lock on an open file; False if another process holds it."""
    if WINDOWS:
        import msvcrt
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def unlock(handle) -> None:
    if WINDOWS:
        import msvcrt
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        return
    import fcntl
    fcntl.flock(handle, fcntl.LOCK_UN)


def resolve_cmd(cmd: list[str]) -> list[str]:
    """On Windows, turn a bare program name (`npx`, `claude`) into its full path (`...\\npx.cmd`)."""
    if not WINDOWS or not cmd or os.path.dirname(cmd[0]):
        return cmd
    found = shutil.which(cmd[0])
    return [found, *cmd[1:]] if found else cmd


def detached_popen_kwargs() -> dict:
    """Popen kwargs for a child that outlives its parent (the dashboard request / window)."""
    if WINDOWS:
        flags = (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
                 | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
        return {"creationflags": flags, "close_fds": True}
    return {"start_new_session": True}


def ensure_utf8_mode() -> None:
    """Thai text everywhere: on Windows re-run this script in Python UTF-8 mode (-X utf8) if it is not already.

    Windows' default code page (cp874 / cp1252) breaks `open()` / `read_text()` / subprocess pipes on Thai text.
    PYTHONUTF8=1 is also exported so every child Python (orchestrator, tools) inherits UTF-8 mode.
    The frozen .exe is built with UTF-8 mode switched on, so this is a no-op there.
    """
    import sys
    if not WINDOWS or sys.flags.utf8_mode:
        return
    os.environ["PYTHONUTF8"] = "1"
    if getattr(sys, "frozen", False):
        return
    raise SystemExit(subprocess.call([sys.executable, "-X", "utf8", *sys.orig_argv[1:]]))
