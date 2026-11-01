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

# Bundle the SSH transport (paramiko is a declared runtime dep, so this normally
# fires). Guarded so a stripped-down dev venv without paramiko still builds — it
# just ships without SSH. PyInstaller's contrib hooks pull in cryptography.
_ssh_hidden = []
try:
    import paramiko  # noqa: F401
    _ssh_hidden = ["paramiko"]
except ImportError:
    pass

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
        # pyserial URL handlers used by the Network transport
        "serial.urlhandler.protocol_socket",
        "serial.urlhandler.protocol_rfc2217",
        "serial.urlhandler.protocol_loop",
        "serial.rfc2217",
    ] + _ssh_hidden,
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
            "CFBundleShortVersionString": "0.4.0",
        },
    )
