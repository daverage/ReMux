# PyInstaller spec for ReMux on macOS, Windows and Linux.
# Build with:  python -m PyInstaller ReMux.spec --noconfirm
#   macOS   -> dist/ReMux.app
#   Windows -> dist/ReMux.exe   (single file)
#   Linux   -> dist/ReMux       (single file)
import sys

VERSION = "1.0.0"
MACOS = sys.platform == "darwin"

a = Analysis(["remux.py"], datas=[("assets/icon.png", "assets")])
pyz = PYZ(a.pure)

if MACOS:
    # A .app bundle is already a folder, so build one-dir and wrap it.
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ReMux", console=False, icon="assets/icon.icns")
    coll = COLLECT(exe, a.binaries, a.datas, name="ReMux")
    app = BUNDLE(
        coll,
        name="ReMux.app",
        icon="assets/icon.icns",
        bundle_identifier="com.daverage.remux",
        version=VERSION,
        info_plist={"NSHighResolutionCapable": True, "LSMinimumSystemVersion": "11.0"},
    )
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, name="ReMux", console=False, icon="assets/icon.ico")
