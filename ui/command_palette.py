"""
ui/command_palette.py — Ctrl/Cmd+K command palette (VS Code / JetBrains pattern).

A frameless popup over the main window: type to fuzzy-filter every command,
↑/↓ to move, Enter to run, Esc to close. Commands are gathered fresh each time
the palette opens (menus, tool windows, connections, view toggles), so new menu
actions show up without registering them here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PyQt6.QtCore import QEvent, QRect, QSize, Qt
from PyQt6.QtGui import QAction, QColor, QPainter
from PyQt6.QtWidgets import (
    QFrame,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
)

from core.i18n import tr


@dataclass
class Command:
    title: str
    category: str
    run: Callable[[], None]
    shortcut: str = ""


def fuzzy_score(query: str, text: str) -> int | None:
    """Subsequence match score (higher is better), or None if no match.

    Rewards consecutive characters and matches at word starts, so "tg" ranks
    "Toggle Graphs" above "Settings › Log colorizer"."""
    if not query:
        return 0
    q, t = query.lower(), text.lower()
    score, ti, prev = 0, 0, -2
    for ch in q:
        ti = t.find(ch, ti)
        if ti < 0:
            return None
        score += 1
        if ti == prev + 1:
            score += 3                       # consecutive run
        if ti == 0 or not t[ti - 1].isalnum():
            score += 5                       # start of a word
        prev = ti
        ti += 1
    return score - len(t) // 20              # mild preference for short titles


def _qcolor(css: str) -> QColor:
    """QColor from a theme value — also accepts CSS ``rgba(r,g,b,a)`` with a
    0–1 alpha, which QColor's own parser rejects (it rendered as black)."""
    if css.startswith("rgba("):
        r, g, b, a = (v.strip() for v in css[5:-1].split(","))
        return QColor(int(r), int(g), int(b), round(float(a) * 255))
    return QColor(css)


class _Delegate(QStyledItemDelegate):
    """Row: category (dim) · title · shortcut (right-aligned, dim)."""

    def __init__(self, colors: dict, parent=None):
        super().__init__(parent)
        self._c = colors

    def sizeHint(self, option, index):           # noqa: N802  (Qt naming)
        return QSize(option.rect.width(), 26)

    def paint(self, painter: QPainter, option, index):
        cmd: Command = index.data(Qt.ItemDataRole.UserRole)
        painter.save()
        r = option.rect
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(r, _qcolor(self._c["accent_bg"]))
        fm = option.fontMetrics
        x = r.left() + 12
        painter.setPen(_qcolor(self._c["fg_dim"]))
        cat = f"{cmd.category}: "
        painter.drawText(QRect(x, r.top(), r.width(), r.height()),
                         Qt.AlignmentFlag.AlignVCenter, cat)
        x += fm.horizontalAdvance(cat)
        painter.setPen(_qcolor(self._c["fg"]))
        painter.drawText(QRect(x, r.top(), r.right() - x - 110, r.height()),
                         Qt.AlignmentFlag.AlignVCenter, cmd.title)
        if cmd.shortcut:
            painter.setPen(_qcolor(self._c["fg_dim"]))
            painter.drawText(QRect(r.right() - 120, r.top(), 108, r.height()),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                             cmd.shortcut)
        painter.restore()


class CommandPalette(QFrame):
    """The popup. Call :meth:`open_with` with the current command list."""

    MAX_ROWS = 12

    def __init__(self, parent, colors: dict):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("commandPalette")
        self._commands: list[Command] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)

        self._edit = QLineEdit()
        self._edit.setObjectName("paletteEdit")
        self._edit.setPlaceholderText(tr("Type a command…  (connect, hex, graphs, theme…)"))
        self._edit.textChanged.connect(self._refilter)
        self._edit.installEventFilter(self)
        lay.addWidget(self._edit)

        self._list = QListWidget()
        self._list.setObjectName("paletteList")
        self._list.setItemDelegate(_Delegate(colors, self._list))
        self._list.setUniformItemSizes(True)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.itemActivated.connect(lambda _it: self._run_current())
        self._list.itemClicked.connect(lambda _it: self._run_current())
        lay.addWidget(self._list)

    # ── API ────────────────────────────────────────────────────────────────────

    def open_with(self, commands: list[Command]) -> None:
        self._commands = commands
        self._edit.clear()
        self._refilter("")
        host = self.parentWidget()
        width = min(640, max(420, host.width() // 2))
        self.resize(width, 26 * self.MAX_ROWS + 52)
        top_left = host.mapToGlobal(host.rect().topLeft())
        self.move(top_left.x() + (host.width() - width) // 2, top_left.y() + 70)
        self.show()
        self._edit.setFocus()

    # ── Internals ──────────────────────────────────────────────────────────────

    def _refilter(self, text: str) -> None:
        text = text.strip()
        scored = []
        for i, cmd in enumerate(self._commands):
            s = fuzzy_score(text, f"{cmd.category} {cmd.title}")
            if s is not None:
                scored.append((-s, i, cmd))
        scored.sort()
        self._list.clear()
        for _, _, cmd in scored:
            it = QListWidgetItem(f"{cmd.category}: {cmd.title}")
            it.setData(Qt.ItemDataRole.UserRole, cmd)
            self._list.addItem(it)
        if self._list.count():
            self._list.setCurrentRow(0)

    def _run_current(self) -> None:
        it = self._list.currentItem()
        if it is None:
            return
        cmd: Command = it.data(Qt.ItemDataRole.UserRole)
        self.hide()
        cmd.run()

    def eventFilter(self, obj, event):           # noqa: N802  (Qt naming)
        if obj is self._edit and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                n = self._list.count()
                if n:
                    step = 1 if key == Qt.Key.Key_Down else -1
                    self._list.setCurrentRow((self._list.currentRow() + step) % n)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._run_current()
                return True
            if key == Qt.Key.Key_Escape:
                self.hide()
                return True
        return super().eventFilter(obj, event)


def _menu_text(text: str) -> str:
    """A menu label as shown: ``&`` marks a mnemonic, ``&&`` is a literal one."""
    return text.replace("&&", "\0").replace("&", "").replace("\0", "&")


def menu_commands(menubar) -> list[Command]:
    """Every enabled action in the menu bar, as ``Menu › Submenu`` commands."""
    out: list[Command] = []

    def walk(menu, path):
        for act in menu.actions():
            if act.isSeparator():
                continue
            sub = act.menu()
            label = _menu_text(act.text()).rstrip("…").strip()
            if sub is not None:
                walk(sub, [*path, label])
            elif act.isEnabled() and isinstance(act, QAction):
                out.append(Command(label, " › ".join(path), act.trigger,
                                   act.shortcut().toString(
                                       act.shortcut().SequenceFormat.NativeText)))

    for act in menubar.actions():
        if act.menu() is not None:
            walk(act.menu(), [_menu_text(act.text())])
    return out
