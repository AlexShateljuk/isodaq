"""Unit tests for core.logger — file formatting and a Logger session round-trip."""
import sqlite3
import time

from core.logger import Logger, _FileWriter, _Msg


# ── _FileWriter formatting (synchronous, deterministic) ──────────────────────────

def test_csv_header_and_row(tmp_path):
    fw = _FileWriter()
    fw.open(tmp_path / "log.csv", "csv")
    fw.write_batch([_Msg(raw="hello world", ts="00:00:01")])
    fw.close()
    text = (tmp_path / "log.csv").read_text()
    assert text.splitlines()[0] == "timestamp,trigger,raw"
    assert '00:00:01,"","hello world"' in text


def test_csv_escapes_embedded_quotes(tmp_path):
    fw = _FileWriter()
    fw.open(tmp_path / "log.csv", "csv")
    fw.write_batch([_Msg(raw='he said "hi"', ts="t")])
    fw.close()
    # Inner quotes must be doubled per CSV rules
    assert '"he said ""hi"""' in (tmp_path / "log.csv").read_text()


def test_csv_trigger_tag(tmp_path):
    fw = _FileWriter()
    fw.open(tmp_path / "log.csv", "csv")
    fw.write_batch([_Msg(raw="overheat", ts="t", is_trigger=True, trigger_name="HOT")])
    fw.close()
    assert '"[TRIGGER:HOT]"' in (tmp_path / "log.csv").read_text()


def test_json_format(tmp_path):
    import json
    fw = _FileWriter()
    fw.open(tmp_path / "log.json", "json")
    fw.write_batch([_Msg(raw="line", ts="t", is_trigger=True, trigger_name="X")])
    fw.close()
    rec = json.loads((tmp_path / "log.json").read_text().strip())
    assert rec == {"ts": "t", "raw": "line", "trigger": "X"}


def test_raw_txt_format(tmp_path):
    fw = _FileWriter()
    fw.open(tmp_path / "log.txt", "txt")
    fw.write_batch([_Msg(raw="boot ok", ts="12:00:00")])
    fw.close()
    assert "[12:00:00]  boot ok" in (tmp_path / "log.txt").read_text()


def test_bytes_written_tracks_output(tmp_path):
    fw = _FileWriter()
    fw.open(tmp_path / "log.csv", "csv")
    before = fw.bytes_written
    fw.write_batch([_Msg(raw="x", ts="t")])
    fw.close()
    assert fw.bytes_written > before


# ── Logger end-to-end (async thread) ─────────────────────────────────────────────

def test_logger_session_writes_csv_and_db(tmp_path):
    lg = Logger()
    lg.set_log_dir(str(tmp_path))
    lg.set_format("csv")
    lg.set_prefix("unit")
    csv_path, db_path = lg.start()
    assert lg.active
    assert csv_path.parent == tmp_path
    assert csv_path.suffix == ".csv"

    for i in range(5):
        lg.write_line(f"row {i}", ts=f"00:00:0{i}")
    lg.write_trigger_event("HOT", "overheat", ts="00:00:09")

    lg.shutdown()   # flushes remaining buffer and joins the writer thread

    text = csv_path.read_text()
    assert "row 0" in text and "row 4" in text
    assert "[TRIGGER:HOT]" in text

    con = sqlite3.connect(str(db_path))
    n = con.execute("SELECT COUNT(*) FROM log").fetchone()[0]
    trig = con.execute("SELECT COUNT(*) FROM log WHERE is_trigger=1").fetchone()[0]
    con.close()
    assert n == 6
    assert trig == 1


def test_write_line_ignored_when_inactive(tmp_path):
    lg = Logger()
    lg.set_log_dir(str(tmp_path))
    # not started → active is False → writes are dropped, no file created
    lg.write_line("ignored")
    time.sleep(0.05)
    lg.shutdown()
    assert list(tmp_path.glob("*")) == []


def test_prefix_falls_back_when_blank():
    lg = Logger()
    lg.set_prefix("   ")
    lg.set_use_file(False)
    lg.set_use_db(False)
    f, d = lg.start()
    assert f is None and d is None    # both sinks disabled
    lg.shutdown()
