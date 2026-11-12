# PyInstaller spec for Grabbit (Mac and Windows).
# Build with packaging/build_mac.sh or packaging/build_windows.ps1, not by hand.

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

ROOT = Path(SPECPATH).parent            # grabbit-desktop/
REPO = ROOT.parent
WEB = REPO / "grabbit-web"               # the server's `app` package
EXT = REPO / "grabbit-extension"
TOOLS = ROOT / "build" / "tools" / "bin"  # from packaging/fetch_tools.py
ICONS = ROOT / "packaging" / "icons"
IS_MAC = sys.platform == "darwin"
IS_WIN = os.name == "nt"

sys.path.insert(0, str(ROOT))
from grabbit_desktop import VERSION  # noqa: E402

exe_suffix = ".exe" if IS_WIN else ""
missing = [t for t in ("ffmpeg", "ffprobe", "deno") if not (TOOLS / (t + exe_suffix)).is_file()]
if missing and not os.environ.get("GRABBIT_ALLOW_MISSING_TOOLS"):
    raise SystemExit(f"Missing {', '.join(missing)} in {TOOLS}. Run: uv run python packaging/fetch_tools.py")


def tree(src: Path, dest: str) -> list[tuple[str, str]]:
    """All files under src (skipping dotfiles), placed under dest/ in the app."""
    out = []
    for p in sorted(src.rglob("*")):
        rel = p.relative_to(src)
        if p.is_file() and not any(part.startswith(".") or part == "__pycache__" for part in rel.parts):
            out.append((str(p), str(Path(dest) / rel.parent)))
    return out


datas = [
    *tree(WEB / "app" / "static", "app/static"),
    *tree(EXT, "extension"),
    *tree(ROOT / "grabbit_desktop" / "setup", "grabbit_desktop/setup"),
    (str(ICONS / ("tray-mac.png" if IS_MAC else "tray.png")), "icons"),
    (str(ROOT / "packaging" / "THIRD-PARTY.txt"), "."),
]
datas += collect_data_files("yt_dlp_ejs")                 # YouTube's JavaScript challenge solver
datas += copy_metadata("yt-dlp") + copy_metadata("yt-dlp-ejs")  # versions, read by the updater

binaries = [(str(p), "bin") for p in sorted(TOOLS.glob("*")) if p.is_file()]

hiddenimports = [
    "app.main",
    "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on",
]
hiddenimports += ["pystray._darwin", "PyObjCTools.AppHelper"] if IS_MAC else ["pystray._win32"] if IS_WIN else []

a = Analysis(
    [str(ROOT / "packaging" / "launch.py")],
    pathex=[str(ROOT), str(WEB)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "_tkinter", "pytest", "_pytest", "IPython", "matplotlib", "numpy"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Grabbit",
    console=False,
    icon=str(ICONS / ("Grabbit.icns" if IS_MAC else "Grabbit.ico")),
    upx=False,
    argv_emulation=False,
)

coll = COLLECT(exe, a.binaries, a.datas, name="Grabbit", upx=False)

if IS_MAC:
    app = BUNDLE(
        coll,
        name="Grabbit.app",
        icon=str(ICONS / "Grabbit.icns"),
        bundle_identifier="com.grabbit.desktop",
        version=VERSION,
        info_plist={
            "CFBundleName": "Grabbit",
            "CFBundleDisplayName": "Grabbit",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "LSUIElement": True,            # menu bar app: no Dock icon
            "LSMinimumSystemVersion": "11.0",
            "NSHighResolutionCapable": True,
            "NSHumanReadableCopyright": "Grabbit",
        },
    )
