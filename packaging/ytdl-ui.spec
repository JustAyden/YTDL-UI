# PyInstaller spec for the single-file YTDL-UI executable.
# Build with:  python packaging/build.py   (wraps `pyinstaller packaging/ytdl-ui.spec`)
# -*- mode: python ; coding: utf-8 -*-
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))  # noqa: F821 (SPECPATH is injected)
NAME = os.environ.get("YTDLUI_EXE_NAME", "ytdl-ui")

datas = [(os.path.join(ROOT, "static"), "static")]
hiddenimports = collect_submodules("waitress") + collect_submodules("ytdlui")
for pkg in ("yt_dlp_ejs", "certifi"):
    try:
        datas += collect_data_files(pkg)
        hiddenimports += collect_submodules(pkg)
    except Exception:
        pass

icon = None
if sys.platform == "win32":
    icon = os.path.join(SPECPATH, "icon.ico")  # noqa: F821
elif sys.platform == "darwin":
    icon = os.path.join(SPECPATH, "icon.png")  # noqa: F821  (converted to .icns by PyInstaller)
if icon and not os.path.exists(icon):
    icon = None

a = Analysis(  # noqa: F821
    [os.path.join(ROOT, "packaging", "entry.py")],
    pathex=[ROOT],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "PIL", "IPython", "matplotlib", "numpy"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=NAME,
    console=True,
    upx=False,
    strip=False,
    icon=icon,
    version=os.path.join(SPECPATH, "version_info.txt") if sys.platform == "win32" and os.path.exists(os.path.join(SPECPATH, "version_info.txt")) else None,  # noqa: F821
)
