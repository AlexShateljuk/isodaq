# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for IsoDAQ Studio.

Build:
    pyinstaller isodaq.spec

Output:
    dist/IsoDAQ Studio/   (folder mode, recommended)
"""

import sys
from pathlib import Path

ROOT = Path(SPECPATH)

block_cipher = None

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        # Ship the pre-generated SVG arrow assets used by themes.py
        (str(ROOT / "ui" / "resources"), "ui/resources"),
    ],
    hiddenimports=[
        # pyqtgraph pulls in these at runtime
        "pyqtgraph.graphicsItems.ViewBox.axisCtrlTemplate_pyqt6",
        "pyqtgraph.graphicsItems.PlotItem.plotConfigTemplate_pyqt6",
        "pyqtgraph.imageview.ImageViewTemplate_pyqt6",
        # numpy internals
        "numpy.core._methods",
        "numpy.lib.format",
        # pyserial platform back-ends
        "serial.serialutil",
        "serial.serialposix",
        "serial.serialwin32",
        "serial.tools.list_ports",
        "serial.tools.list_ports_common",
        "serial.tools.list_ports_posix",
        "serial.tools.list_ports_windows",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "scipy",
        "pandas",
        "IPython",
        "jupyter",
        "PIL",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,       # folder mode — faster startup than onefile
    name="IsoDAQ Studio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,               # no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="ui/resources/icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="IsoDAQ Studio",
)

# macOS .app bundle
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="IsoDAQ Studio.app",
        icon="ui/resources/icon.icns",
        bundle_identifier="com.esomtech.isodaq-studio",
        info_plist={
            "NSHighResolutionCapable": True,
            "CFBundleShortVersionString": "0.1.0",
        },
    )
