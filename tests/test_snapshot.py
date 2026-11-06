"""Trigger snapshots (CMP21): which lines end up in the file, and when."""
import time

import pytest

from core.snapshot import MAX_LINES, SnapshotRecorder, clamp_lines
from core.triggers import Trigger, TriggerEngine


class Rig:
    def __init__(self, tmp_path, **trigger):
        self.folder = tmp_path / "logs" / "snapshots"
        self.saved, self.errors = [], []
        self.engine = TriggerEngine()
        self.rec = SnapshotRecorder(lambda: self.folder, lambda: "/dev/ttyTEST")
        self.rec.on_saved = lambda t, path, text: self.saved.append((t, path, text))
        self.rec.on_error = self.errors.append
        self.engine.on_match(self.rec.on_match)
        options = {"snapshot": True, "snapshot_before": 2, "snapshot_after": 2, **trigger}
        self.engine.add_trigger(Trigger("Fault", "FAULT", **options))

    def line(self, text):
        """As the receive path does it: recorder first, then the trigger check."""
        self.rec.feed(text, "12:00:00.000")
        self.engine.check(text, "12:00:00.000")

    def lines(self):
        return [ln.split("  ", 1)[1] for ln in self.saved[-1][2].splitlines()
                if ln.startswith("12:")]


def test_lines_before_and_after_with_the_match_marked(tmp_path):
    rig = Rig(tmp_path)
    for text in ("a", "b", "c", "FAULT 1", "d", "e", "f"):
        rig.line(text)
    assert len(rig.saved) == 1
    _trigger, path, text = rig.saved[0]
    assert path.parent == rig.folder and path.read_text() == text
    body = [ln for ln in text.splitlines() if ln.startswith("12:")]
    assert [ln[14:] for ln in body] == ["    b", "    c", ">>  FAULT 1", "    d", "    e"]
    assert "Fault" in text and "/dev/ttyTEST" in text and "[contains] FAULT" in text


def test_nothing_is_saved_when_snapshots_are_off(tmp_path):
    rig = Rig(tmp_path, snapshot=False)
    for text in ("a", "FAULT", "b", "c", "d"):
        rig.line(text)
    assert not rig.saved and not rig.folder.exists()


def test_match_with_no_lines_after_is_written_at_once(tmp_path):
    rig = Rig(tmp_path, snapshot_after=0)
    rig.line("a")
    rig.line("FAULT")
    assert len(rig.saved) == 1 and rig.lines()[-1].endswith("FAULT")


def test_match_early_in_the_session_has_what_there_is(tmp_path):
    rig = Rig(tmp_path, snapshot_before=50, snapshot_after=0)
    rig.line("FAULT")
    assert len(rig.lines()) == 1


def test_further_matches_are_marked_in_the_open_snapshot(tmp_path):
    rig = Rig(tmp_path, snapshot_after=3)
    for text in ("FAULT 1", "x", "FAULT 2", "y"):
        rig.line(text)
    assert len(rig.saved) == 1
    text = rig.saved[0][2]
    assert "Matched 2 times" in text
    assert sum(">>" in ln for ln in text.splitlines()) == 2


def test_quiet_device_still_gets_its_file(tmp_path):
    rig = Rig(tmp_path, snapshot_after=50)
    rig.rec.IDLE_TIMEOUT = 0.05
    rig.line("a")
    rig.line("FAULT")
    rig.line("b")
    rig.rec.poll()
    assert not rig.saved                 # not quiet yet
    time.sleep(0.07)
    rig.rec.poll()
    assert len(rig.saved) == 1
    assert "Ended 49 line(s) short" in rig.saved[0][2]


def test_slow_trickle_is_cut_off_by_the_age_limit(tmp_path):
    rig = Rig(tmp_path, snapshot_after=50)
    rig.rec.MAX_AGE = 0.05
    rig.line("FAULT")
    time.sleep(0.07)
    rig.line("still talking")
    rig.rec.poll()
    assert len(rig.saved) == 1


def test_flush_writes_what_is_open(tmp_path):
    rig = Rig(tmp_path, snapshot_after=50)
    rig.line("FAULT")
    rig.rec.flush()
    rig.rec.flush()
    assert len(rig.saved) == 1


def test_a_trigger_firing_constantly_cant_fill_the_disk(tmp_path):
    rig = Rig(tmp_path, snapshot_after=0, snapshot_before=0)
    for _ in range(1000):
        rig.line("FAULT")
    assert len(rig.saved) == 1           # MIN_INTERVAL apart
    rig.rec.MIN_INTERVAL = 0
    rig.line("FAULT")
    assert "999 earlier match(es)" in rig.saved[-1][2]


def test_session_limit(tmp_path):
    rig = Rig(tmp_path, snapshot_after=0, snapshot_before=0)
    rig.rec.MIN_INTERVAL = 0
    rig.rec.MAX_FILES = 3
    for _ in range(6):
        rig.line("FAULT")
    assert len(rig.saved) == 3 and len(rig.errors) == 1


def test_same_second_doesnt_overwrite(tmp_path):
    rig = Rig(tmp_path, snapshot_after=0)
    rig.rec.MIN_INTERVAL = 0
    for n in range(5):
        rig.line(f"FAULT {n}")
    paths = {p for _t, p, _x in rig.saved}
    assert len(paths) == 5


@pytest.mark.parametrize("name", ["../../evil", "a/b\\c", "..", "", "*?:\"<>|", "CON\x00"])
def test_trigger_name_cant_leave_the_folder(tmp_path, name):
    rig = Rig(tmp_path, snapshot_after=0)
    rig.engine.get_triggers()[0].name = name
    rig.line("FAULT")
    _t, path, _x = rig.saved[0]
    assert path.parent == rig.folder


def test_folder_that_cant_be_made_is_reported(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    rig = Rig(tmp_path, snapshot_after=0)
    rig.folder = blocker / "snapshots"
    rig.line("FAULT")
    assert rig.errors and not rig.saved


def test_ring_is_bounded_and_counts_are_clamped(tmp_path):
    rig = Rig(tmp_path, snapshot_before=10 ** 9, snapshot_after=0)
    for n in range(MAX_LINES + 500):
        rig.rec.feed(str(n), "12:00:00.000")
    rig.line("FAULT")
    assert len(rig.lines()) == MAX_LINES + 1


@pytest.mark.parametrize("value, expected", [
    (5, 5), (-1, 0), (10 ** 9, MAX_LINES), ("7", 7), ("x", 42), (None, 42), ([], 42),
])
def test_clamp_lines(value, expected):
    assert clamp_lines(value, 42) == expected


def test_trigger_options_round_trip_and_bad_values_from_a_file():
    engine = TriggerEngine()
    engine.add_trigger(Trigger("x", "y", snapshot=True, snapshot_before=7, snapshot_after=9,
                               snapshot_notify=True))
    again = TriggerEngine()
    again.from_dict_list(engine.to_dict_list())
    t = again.get_triggers()[0]
    assert (t.snapshot, t.snapshot_before, t.snapshot_after, t.snapshot_notify) == (True, 7, 9, True)

    again.from_dict_list([{"name": "old file", "pattern": "p"}])
    t = again.get_triggers()[0]
    assert (t.snapshot, t.snapshot_before, t.snapshot_after) == (False, 100, 50)

    again.from_dict_list([{"name": "n", "pattern": "p", "snapshot_before": "many",
                           "snapshot_after": -3}])
    t = again.get_triggers()[0]
    assert (t.snapshot_before, t.snapshot_after) == (100, 0)
