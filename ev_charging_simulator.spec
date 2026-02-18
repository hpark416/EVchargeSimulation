# PyInstaller spec for EV Charging Simulator
# Build: pyinstaller ev_charging_simulator.spec
# Windows: produces dist\EV Charging Simulator.exe
# macOS: produces dist\EV Charging Simulator.app (then create .dmg separately)

import sys

# --add-data: (source, dest). On Windows use ; in shell, here we use tuple.
# PyInstaller expects (src, dest) where dest is relative to bundle root.
block_cipher = None
icon_src = 'ev_icon.png'
# Data file: include ev_icon.png so the frozen app can find it
datas = [(icon_src, '.')] if __import__('os').path.exists(icon_src) else []

# Hidden imports often needed for PyQt5 + matplotlib
hiddenimports = [
    'numpy', 'matplotlib', 'matplotlib.backends.backend_qt5agg',
    'matplotlib.figure', 'matplotlib.backend_bases',
    'PyQt5.QtCore', 'PyQt5.QtGui', 'PyQt5.QtWidgets',
]

a = Analysis(
    ['ev_charging_simulator.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
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
    name='EV Charging Simulator',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,   # No terminal window (GUI app)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
