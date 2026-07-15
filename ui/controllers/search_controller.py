"""SearchController — in-terminal find bar (F1) and trigger→line jump (F2).

Extracted from MainWindow (OSS6). Owns the find bar widget and the match state,
and operates on the shared terminal (``mw._terminal``). It installs itself as the
event filter on its own search field so Esc/Enter navigation stays self-contained
(MainWindow's event filter no longer knows about search).

Public API used by MainWindow:
  * :meth:`build_bar` — create the (hidden) find bar; MainWindow adds it to the
    left panel layout.
  * :meth:`open` — Ctrl+F / View → Find.
  * :meth:`close` — Esc / the bar's ✕ button.
  * :meth:`jump_to_line` — TriggerEventsPanel double-click → scroll to the line
    that fired the trigger.
"""
from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtGui import QColor, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QWidget,
)

import ui.main_window as _win   # colour globals (runtime access only)
from core.i18n import tr


class SearchController(QObject):
    """Find-in-terminal (F1) and jump-to-line (F2), attached to a MainWindow."""

    def __init__(self, mw):
        super().__init__(mw)
        self._mw = mw
        self._bar: QWidget | None = None
        self._edit: QLineEdit | None = None
        self._count: QLabel | None = None
        self._case: QCheckBox | None = None
        self._matches: list[tuple[int, int]] = []   # (pos, length)
        self._index: int = -1

    # ── Widget ──────────────────────────────────────────────────────────────────

    def build_bar(self) -> QWidget:
        """In-terminal search bar (F1) — hidden until Ctrl+F."""
        bar = QWidget()
        bar.setObjectName("searchBar")
        self._bar = bar
        h = QHBoxLayout(bar)
        h.setContentsMargins(10, 4, 10, 4)
        h.setSpacing(6)

        # Compact glyph button: inline padding:0 so the single char isn't clipped
        # by the global QPushButton padding (the cause of the blank boxes).
        def _nav(glyph: str, tip: str, slot) -> QPushButton:
            b = QPushButton(glyph)
            b.setObjectName("searchNav")
            b.setFixedSize(30, 26)
            b.setStyleSheet("padding:0; font-size:14px;")
            b.setToolTip(tip)
            b.clicked.connect(slot)
            return b

        h.addWidget(self._mw._lbl(tr("Find"), dim=True))
        self._edit = QLineEdit()
        self._edit.setObjectName("searchEdit")
        self._edit.setPlaceholderText(tr("Search terminal…"))
        self._edit.setMinimumWidth(220)
        self._edit.setMaximumWidth(360)
        self._edit.installEventFilter(self)
        self._edit.textChanged.connect(self._update)
        h.addWidget(self._edit)

        h.addWidget(_nav("▲", tr("Previous match (Shift+Enter)"), lambda: self._step(-1)))
        h.addWidget(_nav("▼", tr("Next match (Enter)"), lambda: self._step(+1)))

        self._count = QLabel("0/0")
        self._count.setObjectName("dimLabelMono")
        self._count.setMinimumWidth(46)
        self._count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        h.addWidget(self._count)

        self._case = QCheckBox("Aa")
        self._case.setToolTip(tr("Match case"))
        self._case.stateChanged.connect(self._update)
        h.addWidget(self._case)

        h.addStretch()   # nav group stays left; close button goes to the far right
        h.addWidget(_nav("✕", tr("Close (Esc)"), self.close))
        bar.hide()
        return bar

    # ── Open / close ────────────────────────────────────────────────────────────

    def open(self) -> None:
        term = self._mw._terminal
        self._bar.show()
        sel = term.textCursor().selectedText()
        if sel:
            self._edit.setText(sel)
        self._edit.setFocus()
        self._edit.selectAll()
        self._update()

    def close(self) -> None:
        self._bar.hide()
        self._matches = []
        self._index = -1
        self._mw._terminal.setExtraSelections([])
        self._mw._terminal.setFocus()

    # ── Matching ────────────────────────────────────────────────────────────────

    def _update(self) -> None:
        """Recompute matches over the terminal text and highlight them."""
        needle = self._edit.text()
        text   = self._mw._terminal.toPlainText()
        self._matches = []
        if needle:
            hay = text if self._case.isChecked() else text.lower()
            ndl = needle if self._case.isChecked() else needle.lower()
            start = 0
            while True:
                i = hay.find(ndl, start)
                if i < 0:
                    break
                self._matches.append((i, len(ndl)))
                start = i + len(ndl)
        self._index = 0 if self._matches else -1
        self._render_highlights()
        self._show_current()

    def _step(self, delta: int) -> None:
        if not self._matches:
            return
        self._index = (self._index + delta) % len(self._matches)
        self._show_current()

    def _render_highlights(self) -> None:
        sels = []
        all_fmt = QTextCharFormat()
        all_fmt.setBackground(QColor("#5a4a00"))
        cur_fmt = QTextCharFormat()
        cur_fmt.setBackground(QColor("#b58900"))
        cur_fmt.setForeground(QColor("#1e1e1e"))
        doc = self._mw._terminal.document()
        for idx, (pos, length) in enumerate(self._matches):
            sel = QTextEdit.ExtraSelection()
            cur = QTextCursor(doc)
            cur.setPosition(pos)
            cur.setPosition(pos + length, QTextCursor.MoveMode.KeepAnchor)
            sel.cursor = cur
            sel.format = cur_fmt if idx == self._index else all_fmt
            sels.append(sel)
        self._mw._terminal.setExtraSelections(sels)

    def _show_current(self) -> None:
        term = self._mw._terminal
        n = len(self._matches)
        self._count.setText(f"{self._index + 1}/{n}" if n else "0/0")
        if self._index < 0 or not self._matches:
            return
        pos, length = self._matches[self._index]
        cur = term.textCursor()
        cur.setPosition(pos)
        cur.setPosition(pos + length, QTextCursor.MoveMode.KeepAnchor)
        term.setTextCursor(cur)
        term.ensureCursorVisible()
        self._render_highlights()   # refresh which match is "current"

    # ── Jump from a trigger event to the log line that fired it (F2) ─────────────

    def jump_to_line(self, line_id: int) -> None:
        mw = self._mw
        if line_id is None or line_id < 0:
            mw._log("SYS", "No log location recorded for this event.", _win.C_DIM)
            return
        doc = mw._terminal.document()
        block = doc.firstBlock()
        while block.isValid():
            if block.userState() == line_id:
                cur = QTextCursor(block)
                cur.select(QTextCursor.SelectionType.LineUnderCursor)
                mw._terminal.setTextCursor(cur)
                mw._terminal.ensureCursorVisible()
                mw._terminal.setFocus()
                return
            block = block.next()
        mw._log("SYS",
                "That line has scrolled out of the terminal buffer.", _win.C_DIM)

    # ── Event filter (own search field: Esc closes, Enter steps) ─────────────────

    def eventFilter(self, obj, event):
        if obj is self._edit and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Escape:
                self.close()
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                back = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                self._step(-1 if back else +1)
                return True
        return super().eventFilter(obj, event)
