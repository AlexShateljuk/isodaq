"""
core/connection.py — one device connection: transport-independent downstream.

SER3 (multiple simultaneous connections), step 1. A ``Connection`` owns the
per-device *data* pipeline objects — the parser, trigger engine, logger and the
last-parsed cache — plus identity (id, name, colour). The RX pipeline
(``MainWindow._on_line_received``) reaches these through MainWindow properties
that delegate to the **active** connection, so supporting more connections later
becomes a matter of switching which one is active.

At this step the transport (the reader) and the display widgets (terminal,
chart, side panels) still live on ``MainWindow`` and are shared; they migrate
onto ``Connection`` in later SER3 steps. See ``SER3_REFACTOR.md``.
"""
from __future__ import annotations

import uuid

from PyQt6.QtCore import QObject
from PyQt6.QtGui import QColor

from core.data_parser import DataParser
from core.logger import Logger
from core.serial_reader import SerialReader
from core.triggers import TriggerEngine

# Per-connection tint for tags / tabs, cycled as connections are added.
PALETTE = ["#4fc3f7", "#81c784", "#ffb74d", "#e57373", "#ba68c8", "#4db6ac"]


class Connection(QObject):
    """One device: its transport-independent downstream data objects + identity.

    The transport (reader) is attached later by the connection/serial layer;
    this object owns only what is safe to duplicate per device today."""

    def __init__(self, name: str = "Main", color: str | None = None, parent=None):
        super().__init__(parent)
        self.id: str = uuid.uuid4().hex[:8]
        self.name: str = name
        self.color: QColor = QColor(color or PALETTE[0])

        # Per-connection data pipeline (mirrors what MainWindow owned singly).
        self.parser = DataParser()
        self.engine = TriggerEngine()
        self.logger = Logger()
        self.last_parsed: dict[str, float] = {}

        # Transport: the active reader plus a per-kind cache (Serial/Network/SSH)
        # so switching transport within this connection doesn't leak threads or
        # re-wire signals. Signal wiring is done by SerialController (UI layer).
        self.reader = SerialReader(parent=self)
        self.readers: dict[str, object] = {"Serial": self.reader}
