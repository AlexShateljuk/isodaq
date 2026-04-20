"""
IsoDAQ Studio — main entry point
"""
import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("IsoDAQ Studio")
    app.setApplicationVersion("0.1.0")

    # Qt6 enables high-DPI scaling automatically — no attribute needed

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
