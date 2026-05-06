"""
core/updater.py — Background GitHub release checker.

Fires update_available(version, url) if a newer tag exists.
Runs in a QThread so it never blocks the UI.
"""
from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal


class UpdateChecker(QThread):
    update_available = pyqtSignal(str, str)   # (version_str, release_url)

    _API = "https://api.github.com/repos/AlexShateljuk/isodaq/releases/latest"

    def __init__(self, current_version: str, parent=None):
        super().__init__(parent)
        self._current = current_version

    def run(self):
        try:
            import json
            import urllib.request
            req = urllib.request.Request(
                self._API,
                headers={"Accept": "application/vnd.github+json",
                         "User-Agent": "IsoDAQ-Studio-updater/1"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read())
            tag = data.get("tag_name", "").lstrip("v")
            url = data.get("html_url", "")
            if tag and url and self._is_newer(tag, self._current):
                self.update_available.emit(tag, url)
        except Exception:
            pass  # network unavailable, rate-limited, etc. — silently ignore

    @staticmethod
    def _is_newer(remote: str, current: str) -> bool:
        def _parse(v: str) -> tuple[int, ...]:
            try:
                return tuple(int(x) for x in v.split(".")[:3])
            except ValueError:
                return (0,)
        return _parse(remote) > _parse(current)
