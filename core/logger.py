"""
core/logger.py — Dual high-speed logger

Two parallel sinks, both fed from the same queue:
  1. FILE   — batched writelines() + flush()   (CSV / JSON / raw)
  2. SQLITE  — executemany() with WAL mode      (fast bulk inserts)

Architecture:
  Serial thread
      │
      ▼
  queue.Queue  (unbounded — serial thread never blocks)
      │
      ▼
  _LogWriter thread
      ├── _FileWriter   → .csv / .json / .txt
      └── _SQLiteWriter → .db  (WAL, PRAGMA synchronous=NORMAL)
"""
from __future__ import annotations

import queue
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

FILE_BATCH    = 256
FILE_INTERVAL = 0.2
DB_BATCH      = 512
DB_INTERVAL   = 0.5


@dataclass
class _Msg:
    raw: str
    ts:  str
    is_trigger:   bool = False
    trigger_name: str  = ""


class _FileWriter:
    def __init__(self):
        self._f    = None
        self._fmt  = "csv"
        self._lock = threading.Lock()
        self._bytes = 0

    def open(self, path: Path, fmt: str):
        with self._lock:
            self._close()
            self._fmt = fmt
            path.parent.mkdir(parents=True, exist_ok=True)
            self._f = open(path, "w", encoding="utf-8", buffering=1)
            self._bytes = 0
            if fmt == "csv":
                self._f.write("timestamp,trigger,raw\n"); self._f.flush()

    def close(self):
        with self._lock: self._close()

    def _close(self):
        if self._f:
            try: self._f.flush(); self._f.close()
            except OSError: pass
            self._f = None

    def write_batch(self, msgs: list[_Msg]):
        with self._lock:
            if not self._f: return
            data = "".join(self._fmt_line(m) for m in msgs)
            try:
                self._f.write(data); self._f.flush()
                self._bytes += len(data.encode())
            except OSError: pass

    def _fmt_line(self, m: _Msg) -> str:
        trig_tag = f"[TRIGGER:{m.trigger_name}]" if m.is_trigger else ""
        if self._fmt == "csv":
            safe = m.raw.replace('"', '""')
            return f'{m.ts},"{trig_tag}","{safe}"\n'
        elif self._fmt == "json":
            import json
            return json.dumps({"ts": m.ts, "raw": m.raw,
                               "trigger": m.trigger_name or None}) + "\n"
        else:
            return f"[{m.ts}] {trig_tag} {m.raw}\n"

    @property
    def active(self) -> bool: return self._f is not None
    @property
    def bytes_written(self) -> int: return self._bytes


class _SQLiteWriter:
    def __init__(self):
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self._rows = 0

    def open(self, path: Path):
        with self._lock:
            self._close()
            path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(path), check_same_thread=False)
            c = self._conn
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("PRAGMA cache_size=10000")
            c.execute("""CREATE TABLE IF NOT EXISTS log (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                ts           TEXT NOT NULL,
                raw          TEXT NOT NULL,
                is_trigger   INTEGER DEFAULT 0,
                trigger_name TEXT DEFAULT ''
            )""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_ts ON log(ts)")
            c.commit()
            self._rows = 0

    def close(self):
        with self._lock: self._close()

    def _close(self):
        if self._conn:
            try: self._conn.commit(); self._conn.close()
            except Exception: pass
            self._conn = None

    def write_batch(self, msgs: list[_Msg]):
        with self._lock:
            if not self._conn: return
            rows = [(m.ts, m.raw, int(m.is_trigger), m.trigger_name) for m in msgs]
            try:
                self._conn.executemany(
                    "INSERT INTO log(ts,raw,is_trigger,trigger_name) VALUES(?,?,?,?)", rows)
                self._conn.commit()
                self._rows += len(rows)
            except Exception: pass

    @property
    def active(self) -> bool: return self._conn is not None
    @property
    def rows_written(self) -> int: return self._rows


class _LogWriter(threading.Thread):
    def __init__(self, q: queue.Queue):
        super().__init__(daemon=True, name="LogWriter")
        self._q         = q
        self._stop_ev   = threading.Event()   # renamed: threading.Thread has its own _stop
        self._flush_req = threading.Event()   # session-stop asks the thread to drain + flush
        self._flush_done = threading.Event()
        self.file       = _FileWriter()
        self.db         = _SQLiteWriter()

    def stop(self): self._stop_ev.set()

    def flush_and_wait(self, timeout: float = 2.0) -> None:
        """Drain everything queued so far and write it, *before* the caller closes
        the sinks. Prevents loss of the last (sub-interval) batch on session stop."""
        if not self.is_alive():
            return
        self._flush_done.clear()
        self._flush_req.set()
        self._flush_done.wait(timeout=timeout)

    def _drain_into(self, fbuf: list[_Msg], dbuf: list[_Msg]) -> None:
        while True:
            try:
                m: _Msg = self._q.get_nowait()
            except queue.Empty:
                break
            if self.file.active: fbuf.append(m)
            if self.db.active:   dbuf.append(m)

    def run(self):
        fbuf: list[_Msg] = []
        dbuf: list[_Msg] = []
        t_file = t_db = time.monotonic()

        while not self._stop_ev.is_set():
            self._drain_into(fbuf, dbuf)
            if len(fbuf) >= FILE_BATCH:
                self.file.write_batch(fbuf); fbuf.clear(); t_file = time.monotonic()
            if len(dbuf) >= DB_BATCH:
                self.db.write_batch(dbuf);   dbuf.clear(); t_db   = time.monotonic()

            now = time.monotonic()
            if fbuf and now - t_file >= FILE_INTERVAL:
                self.file.write_batch(fbuf); fbuf.clear(); t_file = now
            if dbuf and now - t_db   >= DB_INTERVAL:
                self.db.write_batch(dbuf);   dbuf.clear(); t_db   = now

            # Session-stop flush handshake: drain the queue and write both buffers
            # now, while the sinks are still open.
            if self._flush_req.is_set():
                self._drain_into(fbuf, dbuf)
                self.file.write_batch(fbuf); fbuf.clear(); t_file = time.monotonic()
                self.db.write_batch(dbuf);   dbuf.clear(); t_db   = time.monotonic()
                self._flush_req.clear()
                self._flush_done.set()

            time.sleep(0.005)

        # Final drain on thread shutdown
        self._drain_into(fbuf, dbuf)
        if fbuf: self.file.write_batch(fbuf)
        if dbuf: self.db.write_batch(dbuf)
        self.file.close(); self.db.close()


class Logger:
    """Public logging facade.

    :meth:`write_line` / :meth:`write_trigger_event` are called from the serial
    thread and only enqueue (never block on I/O); a background writer thread
    batches to a file (CSV/JSON/txt) and/or SQLite. :meth:`start` opens a new
    timestamped session, :meth:`stop` flushes and closes it, :meth:`shutdown`
    also stops the writer thread. Writes while inactive are dropped.
    """

    def __init__(self):
        self._q: queue.Queue[_Msg] = queue.Queue()
        self._w = _LogWriter(self._q)
        self._w.start()
        self._active   = False
        self._use_file = True
        self._use_db   = True
        self._prefix   = "log_session"
        self._fmt      = "csv"
        self._log_dir  = Path.home() / "isodaq_logs"
        self._cur_file: Path | None = None
        self._cur_db:   Path | None = None

    # ── Config ────────────────────────────────────────────────────────────────
    def set_prefix(self, v: str):      self._prefix   = v.strip() or "log_session"
    def set_format(self, v: str):      self._fmt      = v.lower()
    def set_log_dir(self, v: str):     self._log_dir  = Path(v)
    def set_use_file(self, v: bool):   self._use_file = v
    def set_use_db(self, v: bool):     self._use_db   = v

    # ── Session ───────────────────────────────────────────────────────────────
    def start(self) -> tuple[Path | None, Path | None]:
        """Open a new timestamped session. Returns ``(file_path, db_path)``;
        either is ``None`` if that sink is disabled."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._cur_file = self._cur_db = None

        if self._use_file:
            ext = {"json": "json", "csv": "csv"}.get(self._fmt, "txt")
            self._cur_file = self._log_dir / f"{self._prefix}_{ts}.{ext}"
            self._w.file.open(self._cur_file, self._fmt)

        if self._use_db:
            self._cur_db = self._log_dir / f"{self._prefix}_{ts}.db"
            self._w.db.open(self._cur_db)

        self._active = True
        return self._cur_file, self._cur_db

    def stop(self):
        self._active = False               # no new lines enter the queue past here
        self._w.flush_and_wait()           # drain + write the last buffered batch first
        self._w.file.close()
        self._w.db.close()

    @property
    def active(self) -> bool: return self._active
    @property
    def file_bytes(self) -> int: return self._w.file.bytes_written
    @property
    def db_rows(self) -> int: return self._w.db.rows_written
    @property
    def current_file(self) -> Path | None: return self._cur_file
    @property
    def current_db(self) -> Path | None: return self._cur_db

    # ── Write (called from serial thread) ─────────────────────────────────────
    def write_line(self, raw: str, ts: str | None = None):
        if not self._active: return
        self._q.put(_Msg(raw=raw, ts=ts or _now()))

    def write_trigger_event(self, name: str, raw: str, ts: str):
        if not self._active: return
        self._q.put(_Msg(raw=raw, ts=ts, is_trigger=True, trigger_name=name))

    # ── Shutdown ──────────────────────────────────────────────────────────────
    def shutdown(self):
        self.stop()
        self._w.stop()
        self._w.join(timeout=3.0)


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]
