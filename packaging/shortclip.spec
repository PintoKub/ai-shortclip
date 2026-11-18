# PyInstaller spec for ShortClip.exe — build with:  python packaging/build_exe.py
# One-folder build: dist/ShortClip/ShortClip.exe + _internal/ (Python + libraries). build_exe.py then copies the
# project source (orchestrator.py, shortclip/, tools/, dashboard/, config/, assets/ …) next to the .exe.
import ast
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).resolve().parent
LOCAL = {"shortclip", "orchestrator", "launcher", "fake_platforms"} | {p.stem for p in (ROOT / "tools").glob("*.py")}
SCAN = [ROOT / "orchestrator.py", ROOT / "launcher.py", *(ROOT / "shortclip").glob("*.py"),
        *(ROOT / "tools").glob("*.py"), *(ROOT / "dashboard").glob("*.py")]


def imports_of(path: Path) -> set[str]:
    """Every absolute import in a project file: those files run from source at runtime, so the modules they
    import must be inside the .exe even though launcher.py itself never imports them."""
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module)
    return {m for m in found if m.split(".")[0] not in LOCAL and m != "__future__"}


hidden = set()
for f in SCAN:
    hidden |= imports_of(f)
# code passed as `-c` strings (shortclip/thumbnail.py) and modules run with `-m`
hidden |= {"asyncio", "playwright.async_api", "playwright.sync_api", "edge_tts", "edge_tts.__main__",
           "playwright.__main__", "json", "sqlite3", "http.server", "wave", "audioop"}
if sys.version_info >= (3, 13):
    hidden.discard("audioop")

datas, binaries = [], []
for pkg in ("edge_tts", "faster_whisper", "ctranslate2", "pythainlp", "playwright", "onnxruntime", "av",
            "tokenizers", "huggingface_hub", "certifi", "requests"):
    try:
        d, b, h = collect_all(pkg)
    except Exception as exc:  # optional package not installed in this build env
        print(f"[shortclip.spec] skip {pkg}: {exc}")
        continue
    datas += d
    binaries += b
    hidden |= set(h)

a = Analysis(
    [str(ROOT / "launcher.py")],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(hidden),
    excludes=sorted(LOCAL - {"launcher"}) + ["tkinter", "pytest"],   # project code runs from the files next to the .exe
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [("X utf8", None, "OPTION")],   # Python UTF-8 mode: Thai text in files and pipes on Windows code pages
    exclude_binaries=True,
    name="ShortClip",
    console=True,                   # the window shows the dashboard URL, first-run password and logs
    icon=None,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="ShortClip", upx=False)
