"""
IsoDAQ Studio — main entry point

GUI mode (default):
  python main.py
  isodaq.exe

CLI mode (headless serial monitor):
  isodaq.exe -com COM8 -baud 115200 -keywords "starting device,ERROR"
  isodaq.exe --cli -p COM8 -b 115200 -k "BOOT OK" --fail-on ERROR --timeout 30
  isodaq.exe --cli --help
"""
import sys


# CLI mode: triggered by --cli flag OR direct serial port flags (-com / --port / -p)
_CLI_TRIGGERS = frozenset(["--cli", "-com", "--com", "--port", "-p"])


def _is_cli_mode() -> bool:
    return bool(_CLI_TRIGGERS.intersection(sys.argv[1:]))


def _cli_argv() -> list[str]:
    return [a for a in sys.argv[1:] if a != "--cli"]


def main():
    if _is_cli_mode():
        from core.cli_runner import run_cli
        sys.exit(run_cli(_cli_argv()))

    # GUI mode — import Qt only when needed
    from pathlib import Path
    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import QApplication
    from ui.main_window import MainWindow

    _ICON = Path(__file__).parent / "ui" / "resources" / "icon.png"

    app = QApplication(sys.argv)
    app.setApplicationName("IsoDAQ Studio")
    app.setApplicationVersion("0.1.0")
    if _ICON.exists():
        app.setWindowIcon(QIcon(str(_ICON)))

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
