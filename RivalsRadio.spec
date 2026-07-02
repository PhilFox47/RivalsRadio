# PyInstaller spec for RivalsRadio.  Build with:  pyinstaller RivalsRadio.spec

from PyInstaller.utils.hooks import collect_data_files

datas = collect_data_files("customtkinter")
# Ship the app icon and the default hero roster + art.
datas += [("assets", "assets")]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "numpy", "PIL", "PIL.ImageTk", "spotipy", "spotipy.oauth2",
        "soundcard", "cffi", "customtkinter", "darkdetect", "pygame",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="RivalsRadio",
    debug=False,
    strip=False,
    upx=True,
    runtime_tmpdir=None,
    console=False,
    icon="assets/icon.ico",
)
