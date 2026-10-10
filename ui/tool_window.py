"""
ui/tool_window.py — IntelliJ-style tool windows: edge stripes + docks.

Two pieces make up the layout shell:

* :class:`StripeBar` — a thin strip of toggle buttons pinned to a window edge.
  Side stripes draw their labels rotated so the strip stays ~22px wide. Clicking
  a button opens its tool; clicking the active one collapses the dock back to
  the stripe, so every panel can be hidden and the terminal can own the window.

* :class:`ToolDock` — the container a stripe opens: a 22px header with the tool
  title plus a close button, over a stack of that edge's tool widgets.

A dock is hidden entirely when no tool on its edge is active, which is what
keeps the layout usable on small displays.
"""
from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QStyleOptionButton,
    QStylePainter,
    QVBoxLayout,
    QWidget,
)


class VerticalButton(QPushButton):
    """Checkable button whose label is drawn rotated 90°, for side stripes.

    ``side="left"`` reads bottom-to-top, ``side="right"`` top-to-bottom — the
    same convention IntelliJ uses, so labels always face into the window.
    """

    def __init__(self, text: str, side: str = "left", parent=None):
        super().__init__(text, parent)
        self._side = side
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        s = super().sizeHint()
        return QSize(s.height(), s.width())   # transposed: tall and narrow

    def minimumSizeHint(self) -> QSize:       # noqa: N802  (Qt naming)
        # NOT transposed: Qt's implementation already calls our overridden
        # sizeHint(), so transposing here would undo the rotation.
        return self.sizeHint()

    def paintEvent(self, event):              # noqa: N802  (Qt naming)
        painter = QStylePainter(self)
        opt = QStyleOptionButton()
        self.initStyleOption(opt)
        # Rotate the painter, then hand the style a transposed rect so the
        # stylesheet still draws the background/border normally.
        if self._side == "left":
            painter.rotate(-90)
            painter.translate(-self.height(), 0)
        else:
            painter.rotate(90)
            painter.translate(0, -self.width())
        opt.rect = opt.rect.transposed()
        painter.drawControl(QStyle.ControlElement.CE_PushButton, opt)


class StripeBar(QWidget):
    """Strip of tool toggle buttons pinned to one window edge."""

    #: emitted with the tool key the user clicked (may be the active one)
    clicked_tool = pyqtSignal(str)

    def __init__(self, side: str = "left", parent=None):
        super().__init__(parent)
        self._side = side
        self.setObjectName("stripeBar")
        self._buttons: dict[str, QPushButton] = {}

        vertical = side in ("left", "right")
        lay = QVBoxLayout(self) if vertical else QHBoxLayout(self)
        lay.setContentsMargins(*( (2, 6, 2, 6) if vertical else (6, 2, 6, 2) ))
        lay.setSpacing(4)
        lay.addStretch()          # buttons are inserted before this stretch
        self._lay = lay

        if vertical:
            self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        else:
            self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def add_tool(self, key: str, label: str) -> None:
        if self._side in ("left", "right"):
            btn = VerticalButton(label, side=self._side)
        else:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setObjectName("stripeBtn")
        btn.clicked.connect(lambda _=False, k=key: self.clicked_tool.emit(k))
        self._buttons[key] = btn
        self._lay.insertWidget(self._lay.count() - 1, btn)

    def set_active(self, key: str | None) -> None:
        """Check the active tool's button, uncheck the rest."""
        for k, btn in self._buttons.items():
            btn.setChecked(k == key)

    def tool_label(self, key: str) -> str:
        btn = self._buttons.get(key)
        return btn.text() if btn is not None else ""

    def set_badge(self, key: str, text: str) -> None:
        """Append a small count to a stripe label (e.g. Events · 3)."""
        btn = self._buttons.get(key)
        if btn is not None:
            btn.setText(text)
            btn.updateGeometry()


class ToolDock(QWidget):
    """Dockable area that shows one tool at a time, with a title header."""

    #: emitted when the user closes the dock via its header button
    closed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("toolDock")
        self._tools: dict[str, QWidget] = {}
        self._titles: dict[str, str] = {}
        self._actions: dict[str, list[QWidget]] = {}
        self._current: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QWidget()
        header.setObjectName("toolDockHeader")
        header.setFixedHeight(22)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(9, 0, 4, 0)
        hl.setSpacing(6)
        self._title = QLabel("")
        self._title.setObjectName("toolDockTitle")
        hl.addWidget(self._title)
        hl.addStretch()
        # Per-tool action buttons live here, swapped when the tool changes.
        self._action_host = QWidget()
        self._action_lay = QHBoxLayout(self._action_host)
        self._action_lay.setContentsMargins(0, 0, 0, 0)
        self._action_lay.setSpacing(4)
        hl.addWidget(self._action_host)
        close = QPushButton("×")
        close.setObjectName("toolDockClose")
        close.setFixedSize(16, 16)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.clicked.connect(self.closed.emit)
        hl.addWidget(close)
        root.addWidget(header)

        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)

    def add_tool(self, key: str, title: str, widget: QWidget,
                 actions: list[QWidget] | None = None) -> None:
        self._tools[key] = widget
        self._titles[key] = title
        self._actions[key] = actions or []
        for act in self._actions[key]:
            act.setParent(self._action_host)
            act.hide()
            self._action_lay.addWidget(act)
        self._stack.addWidget(widget)

    def show_tool(self, key: str) -> None:
        widget = self._tools.get(key)
        if widget is None:
            return
        self._current = key
        self._stack.setCurrentWidget(widget)
        self._title.setText(self._titles[key].upper())
        for k, acts in self._actions.items():
            for act in acts:
                act.setVisible(k == key)

    def has_tool(self, key: str) -> bool:
        return key in self._tools

    @property
    def current(self) -> str | None:
        return self._current

    def float_current(self):
        """Detach the current tool into its own window; it returns to this
        dock's stack when that window closes. Returns the window, or None."""
        key = self._current
        if key is None:
            return None
        widget = self._tools[key]
        self._stack.removeWidget(widget)
        return _FloatToolWindow(widget, self._titles[key], self, key)


class _FloatToolWindow(QWidget):
    """A tool detached from its dock; re-docks on close."""

    def __init__(self, widget: QWidget, title: str, dock: "ToolDock", key: str):
        super().__init__(None, Qt.WindowType.Window)
        self._widget, self._dock, self._key = widget, dock, key
        self.setWindowTitle(f"IsoDAQ — {title}")
        self.resize(1100, 720)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(widget)
        widget.show()

    def closeEvent(self, event):              # noqa: N802  (Qt naming)
        self._widget.setParent(None)          # detach before this window dies
        self._dock._stack.addWidget(self._widget)
        event.accept()
        self.deleteLater()
