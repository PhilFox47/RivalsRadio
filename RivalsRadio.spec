# PyInstaller spec for RivalsRadio.
# Build with:  pyinstaller RivalsRadio.spec
# Produces a single windowed executable in dist/ (no console window).

block_cipher = None

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[],
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
    # icon="docs/icon.ico",  # add an .ico here if you want a custom icon
)
