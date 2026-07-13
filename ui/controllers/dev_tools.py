"""DevTools — element inspector and live theme hot-reload.

Extracted from MainWindow (OSS6). Two developer aids:

  * **Inspector** (Ctrl+Shift+I) — always available. While on, DevTools installs
    itself as an application-wide event filter and the next click prints the
    clicked widget's className / objectName / ancestry to the terminal. This is
    why DevTools owns its *own* ``eventFilter`` — the inspect click is app-wide,
    whereas MainWindow keeps the per-widget key handling for the command and
    search fields.
  * **Theme hot-reload** (ISODAQ_DEV=1) — a QFileSystemWatcher re-applies styling
    whenever ``ui/themes.py`` is saved (also Ctrl+Shift+T).

Colour globals live in ``ui.main_window`` and are re-bound on theme change, so
they are read through the module at call time (``_win.C_SYS``).
"""
from __future__ import annotations

import importlib
import os

from PyQt6.QtCore import QEvent, QObject
from PyQt6.QtGui import QCursor, QKeySequence, QShortcut
from PyQt6.QtWidgets import QApplication

import ui.main_window as _win   # colour globals (runtime access only)


class DevTools(QObject):
    """Element inspector + theme hot-reload, attached to a MainWindow."""

    def __init__(self, mw):
        super().__init__(mw)
        self._mw = mw
        self._inspect_mode = False
        self._theme_path: str | None = None
        self._theme_watcher = None

    def setup(self) -> None:
        mw = self._mw
        # Element inspector — always available, hidden shortcut
        QShortcut(QKeySequence("Ctrl+Shift+I"), mw, activated=self.toggle_inspect)

        if os.environ.get("ISODAQ_DEV"):
            # Live theme hot-reload: re-apply styling whenever ui/themes.py is saved
            from PyQt6.QtCore import QFileSystemWatcher
            import ui.themes as _themes_mod
            self._theme_path = _themes_mod.__file__
            self._theme_watcher = QFileSystemWatcher([self._theme_path], self)
            self._theme_watcher.fileChanged.connect(self.reload_theme_file)
            QShortcut(QKeySequence("Ctrl+Shift+T"), mw,
                      activated=lambda: self.reload_theme_file(self._theme_path))
            mw._log("SYS", "DEV mode: theme hot-reload on (edit ui/themes.py → auto-restyle)", _win.C_SYS)
            mw._log("SYS", "DEV mode: Ctrl+Shift+I inspect · Ctrl+Shift+T reload theme", _win.C_DIM)

    def toggle_inspect(self) -> None:
        self._inspect_mode = not self._inspect_mode
        app = QApplication.instance()
        if self._inspect_mode:
            app.installEventFilter(self)
            self._mw._log("SYS", "INSPECT ON — click any element to print its name "
                                 "(Ctrl+Shift+I to exit)", _win.C_SYS)
        else:
            app.removeEventFilter(self)
            self._mw._log("SYS", "INSPECT OFF", _win.C_DIM)

    def _describe_widget(self, w) -> None:
        cls  = w.metaObject().className()
        name = w.objectName()
        chain, cur, nearest = [], w, ""
        while cur is not None:
            on = cur.objectName()
            chain.append(f"{cur.metaObject().className()}" + (f"#{on}" if on else ""))
            if on and not nearest:
                nearest = on
            cur = cur.parent()
        sz = w.size()
        head = (f"objectName='{name}'" if name
                else f"(unnamed; nearest named ancestor: '{nearest or '—'}')")
        self._mw._log("SYS", f"[INSPECT] {cls}  {head}  {sz.width()}×{sz.height()}", _win.C_OK)
        self._mw._log("SYS", "   path: " + "  <  ".join(chain[:6]), _win.C_DIM)

    def reload_theme_file(self, path: str) -> None:
        import ui.themes as _themes_mod
        try:
            importlib.reload(_themes_mod)
            # Rebind the names main_window imported directly from ui.themes so its
            # own _apply_theme picks up the reloaded functions.
            _win.build_stylesheet  = _themes_mod.build_stylesheet
            _win.theme_colors      = _themes_mod.theme_colors
            _win.set_current_theme = _themes_mod.set_current_theme
            _win.tint_titlebar     = _themes_mod.tint_titlebar
            self._mw._apply_theme(self._mw._current_theme)
            self._mw._log("SYS", "Theme reloaded ✓", _win.C_OK)
        except Exception as e:
            self._mw._log("ERR", f"Theme reload failed: {e}", _win.C_ERR)
        # Editors replace the file on save, which drops the watch — re-add it
        if self._theme_watcher and path not in self._theme_watcher.files():
            self._theme_watcher.addPath(path)

    def eventFilter(self, obj, event):
        # Inspect mode (app-wide filter): first click reports the widget, eats it.
        if self._inspect_mode and event.type() == QEvent.Type.MouseButtonPress:
            wdg = QApplication.widgetAt(QCursor.pos())
            if wdg is not None:
                self._describe_widget(wdg)
            return True
        return super().eventFilter(obj, event)
