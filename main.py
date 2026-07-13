"""
IsoDAQ Studio — main entry point
"""
import json
import sys
from pathlib import Path
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from core import i18n
from ui.main_window import MainWindow

_ICON = Path(__file__).parent / "ui" / "resources" / "icon.png"


def _saved_language() -> str:
    """Read the persisted language preference before any UI is built."""
    try:
        return json.loads(MainWindow._CONFIG_PATH.read_text()).get("language", "")
    except Exception:
        return ""


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("IsoDAQ Studio")
    app.setApplicationVersion("0.2.2")
    if _ICON.exists():
        app.setWindowIcon(QIcon(str(_ICON)))

    # Load translations before any UI is built
    # (saved preference → ISODAQ_LANG → system locale → en)
    i18n.init(_saved_language())

    # Qt6 enables high-DPI scaling automatically — no attribute needed

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
