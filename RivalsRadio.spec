# PyInstaller spec for RivalsRadio.
# Build with:  pyinstaller RivalsRadio.spec
# Produces a single windowed executable in dist/ (no console window).

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

# CustomTkinter ships its themes/fonts as data files that must be bundled.
ctk_datas = collect_data_files("customtkinter")
# Bundle the app icon so the window can set it at runtime too.
ctk_datas += [("assets/icon.ico", "assets")]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=ctk_datas,
    # spotipy/cv2/mss are imported lazily in places; list them so PyInstaller's
    # static analysis definitely bundles them.
    hiddenimports=[
        "mss",
        "cv2",
        "numpy",
        "PIL",
        "PIL.ImageTk",
        "spotipy",
        "spotipy.oauth2",
        "soundcard",
        "cffi",
        "pygetwindow",
        "customtkinter",
        "darkdetect",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="RivalsRadio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # windowed app: no terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",
)
