"""Smoke test: MainWindow must construct headless without raising.

This is the safety net for the OSS6 decomposition. There are no full UI tests,
so this catches the most likely refactor break — a mis-wired signal or a missing
attribute surfacing during construction. It is deliberately hermetic:

  * runs under the offscreen Qt platform (no display needed),
  * sandboxes the settings file to a tmp dir (never touches the real config),
  * stubs the update check (no network).

Skipped when PyQt6 (and the GUI stack) isn't installed, so the lean core CI job
that only has pytest stays green; it runs wherever the GUI deps are present.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")
pytest.importorskip("pyqtgraph")


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    app.setApplicationVersion("0.0.0-test")
    yield app


def test_mainwindow_constructs(qapp, tmp_path, monkeypatch):
    from ui.controllers.update_manager import UpdateManager
    from ui.main_window import MainWindow

    # Sandbox persisted settings and skip the network update check.
    monkeypatch.setattr(MainWindow, "_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(UpdateManager, "start", lambda self: None)

    w = MainWindow()
    try:
        # Core services are wired.
        assert w._logger is not None
        assert w._engine is not None
        assert w._reader is not None
        assert w._parser is not None
        assert w._macro_runner is not None
        # The window shell was built.
        assert w._terminal is not None
        assert w._main_splitter is not None
        assert w.centralWidget() is not None
        # OSS6 controllers are attached.
        assert w._session is not None
        assert w._updates is not None
        assert w._devtools is not None
        assert w._search is not None
        assert w._settings is not None
    finally:
        w.close()


def test_mainwindow_ukrainian_locale(qapp, tmp_path, monkeypatch):
    """With the uk catalog loaded, wrapped UI strings render in Ukrainian.

    Guards the i18n coverage: if a widget's text is un-wrapped (or a uk.json key
    drifts from the source string), these assertions fail.
    """
    from core import i18n
    from ui.controllers.update_manager import UpdateManager
    from ui.main_window import MainWindow

    monkeypatch.setattr(MainWindow, "_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(UpdateManager, "start", lambda self: None)

    prev = i18n.current_language()
    assert i18n.set_language("uk") == "uk"
    try:
        w = MainWindow()
        try:
            # Buttons, tool stripes and the status bar resolve through the uk catalog.
            assert w._conn_btn.text() == i18n.tr("Connect") == "Підключити"
            assert w._send_btn.text() == "Надіслати"
            assert w._stripe_right.tool_label("graphs") == "Графіки"
            assert "Відключено" in w._sb_conn.text()
            # menuBar first menu is "File" → "Файл"
            assert w.menuBar().actions()[0].text() == "Файл"
        finally:
            w.close()
    finally:
        i18n.set_language(prev)   # don't leak locale into other tests


def test_settings_roundtrip(qapp, tmp_path, monkeypatch):
    """SettingsManager.save() then a fresh MainWindow.load() restores UI state."""
    from ui.controllers.update_manager import UpdateManager
    from ui.main_window import MainWindow

    cfg = tmp_path / "config.json"
    monkeypatch.setattr(MainWindow, "_CONFIG_PATH", cfg)
    monkeypatch.setattr(UpdateManager, "start", lambda self: None)

    a = MainWindow()
    a._scrollback_limit = 12345
    a._signaling_url = "https://relay.example.test"
    a._terminal_font_size = 17
    a._apply_theme("light")
    a._settings.save()
    a.close()

    assert cfg.exists()

    b = MainWindow()   # __init__ runs _settings.load()
    try:
        assert b._scrollback_limit == 12345
        assert b._signaling_url == "https://relay.example.test"
        assert b._terminal_font_size == 17
        assert b._current_theme == "light"
    finally:
        b.close()


def test_ssh_sessions_persist_without_password(qapp, tmp_path, monkeypatch):
    """Saved SSH sessions survive a restart; the password never touches disk."""
    from ui.controllers.update_manager import UpdateManager
    from ui.main_window import MainWindow

    cfg = tmp_path / "config.json"
    monkeypatch.setattr(MainWindow, "_CONFIG_PATH", cfg)
    monkeypatch.setattr(UpdateManager, "start", lambda self: None)

    a = MainWindow()
    a._ssh_sessions = [
        {"name": "Lab Pi", "host": "192.168.1.20", "port": 22,
         "username": "pi", "key_path": "", "command": "cat /dev/ttyACM0"},
        {"name": "Router", "host": "10.0.0.1", "port": 2222,
         "username": "root", "key_path": "~/.ssh/id_ed25519", "command": "logread -f"},
    ]
    a._serial._ssh_pw_cache["Lab Pi"] = "hunter2"
    a._ssh_params = dict(a._ssh_sessions[0], password="hunter2")
    a._settings.save()
    a.close()

    import json
    saved = json.loads(cfg.read_text())
    assert saved["ssh_active"] == "Lab Pi"
    assert [s["name"] for s in saved["ssh_sessions"]] == ["Lab Pi", "Router"]
    assert all("password" not in s for s in saved["ssh_sessions"])

    b = MainWindow()
    try:
        assert len(b._ssh_sessions) == 2
        assert b._ssh_params.get("name") == "Lab Pi"
        assert b._ssh_params.get("password", "") == ""   # never restored
    finally:
        b.close()


def test_multi_connection_isolation(qapp, tmp_path, monkeypatch):
    """SER3: a second connection is fully isolated — its own panels, parser,
    logger and terminal — and switching tabs swaps the right-side panel stacks."""
    from ui.controllers.update_manager import UpdateManager
    from ui.main_window import MainWindow

    monkeypatch.setattr(MainWindow, "_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(UpdateManager, "start", lambda self: None)

    w = MainWindow()
    try:
        a = w._active_conn
        w._add_connection()
        b = w._active_conn
        assert a is not b
        assert len(w._connections) == 2

        # Every per-connection object/panel is distinct.
        for attr in ("parser", "engine", "logger", "reader",
                     "parse", "chart", "indicator", "trigger",
                     "events", "analytics", "logger_panel"):
            assert getattr(a, attr) is not getattr(b, attr), attr

        # RX routes to the source connection only (own terminal + own parser).
        a.parser.from_dict_list([{"name": "t", "prefix": "DATA:", "key": "t"}])
        b.parser.from_dict_list([{"name": "t", "prefix": "DATA:", "key": "t"}])
        w._on_conn_line(a, "DATA: t=1.0", "00:00:00.000")
        w._on_conn_line(b, "DATA: t=2.0", "00:00:01.000")
        assert a.last_parsed["t"] == 1.0 and b.last_parsed["t"] == 2.0
        w._flush_terminals()   # terminal writes are batched (~30 fps)
        assert "t=1.0" in w._terminals[a.id].toPlainText()
        assert "t=1.0" not in w._terminals[b.id].toPlainText()

        # Switching active swaps the terminal + every right-side panel stack.
        w._conn_bar.setCurrentIndex(w._conn_tab_index(a.id))
        assert w._active_conn is a
        assert w._terminal is w._terminals[a.id]
        assert w._chart_stack.currentWidget() is a.chart
        assert w._events_stack.currentWidget() is a.events
        assert w._trigger_stack.currentWidget() is a.trigger
        assert w._parse_panel is a.parse   # property follows active
    finally:
        w.close()


def test_ssh_legacy_single_session_migrates(qapp, tmp_path, monkeypatch):
    """A pre-sessions config ("ssh": {...}) is migrated to one saved session."""
    import json

    from ui.controllers.update_manager import UpdateManager
    from ui.main_window import MainWindow

    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "transport": "SSH",
        "ssh": {"host": "old.host", "port": 22, "username": "me",
                "key_path": "", "command": "cat /dev/ttyUSB0"},
    }))
    monkeypatch.setattr(MainWindow, "_CONFIG_PATH", cfg)
    monkeypatch.setattr(UpdateManager, "start", lambda self: None)

    w = MainWindow()
    try:
        assert len(w._ssh_sessions) == 1
        assert w._ssh_sessions[0]["host"] == "old.host"
        assert w._ssh_sessions[0]["name"] == "me@old.host"
    finally:
        w.close()


def test_hex_view_renders_received_bytes(qapp, tmp_path, monkeypatch):
    from ui.controllers.update_manager import UpdateManager
    from ui.main_window import MainWindow

    monkeypatch.setattr(MainWindow, "_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(UpdateManager, "start", lambda self: None)
    w = MainWindow()
    try:
        conn = w._active_conn
        w._chk_hex.setChecked(True)
        raw = bytes(range(0x41, 0x41 + 20)) + b"\r\n"     # 22 bytes → 2 rows
        w._on_conn_line(conn, "ABCDEFGHIJKLMNOPQRST", "00:00:00.000", raw)
        w._on_conn_line(conn, "next", "00:00:00.001", b"next\n")
        w._flush_terminals()
        text = w._terminal.toPlainText()
        assert "00000000  41 42 43 44 45 46 47 48  49 4A 4B 4C 4D 4E 4F 50" in text
        assert "|ABCDEFGHIJKLMNOP|" in text
        assert "00000010  51 52 53 54 0D 0A" in text and "|QRST..|" in text
        assert "00000016  6E 65 78 74 0A" in text        # offsets run on

        # The view is decided on arrival: turning HEX off before the next paint
        # must not turn already-received hex lines into text (and vice versa).
        w._on_conn_line(conn, "late", "00:00:00.002", b"late\n")
        w._chk_hex.setChecked(False)
        w._on_conn_line(conn, "plain", "00:00:00.003", b"plain\n")
        w._flush_terminals()
        text = w._terminal.toPlainText()
        assert "0000001B  6C 61 74 65 0A" in text
        assert "RX   plain" in text
    finally:
        w.close()


def test_quick_plot_and_keep(qapp, tmp_path, monkeypatch):
    """Data-only lines plot without setup; keeping one makes it a Parsing channel."""
    from ui.controllers.update_manager import UpdateManager
    from ui.main_window import MainWindow

    monkeypatch.setattr(MainWindow, "_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(UpdateManager, "start", lambda self: None)
    w = MainWindow()
    try:
        conn = w._active_conn
        chart = conn.chart
        for i in range(5):
            w._on_conn_line(conn, f"temp:{20 + i} hum:40", "00:00:00.000")
        w._on_conn_line(conn, "Free heap: 12345 bytes", "00:00:00.000")
        assert set(chart._channels) == {"temp", "hum"} == chart._auto
        assert list(chart._channels["temp"]["buf_v"]) == [20, 21, 22, 23, 24]

        chart.keep_requested.emit("temp")
        assert "temp" in conn.parser.channel_names() and "temp" not in chart._auto
        w._on_conn_line(conn, "temp:30 hum:41", "00:00:00.000")
        assert chart._channels["temp"]["buf_v"][-1] == 30
        assert len(chart._channels["temp"]["buf_v"]) == 6   # parser only, not twice

        chart.remove_channel("hum")                         # hide an auto series
        w._on_conn_line(conn, "temp:31 hum:42", "00:00:00.000")
        assert "hum" not in chart._channels

        chart.set_quick_enabled(False)
        w._on_conn_line(conn, "1,2,3", "00:00:00.000")
        assert set(chart._channels) == {"temp"}
    finally:
        w.close()
