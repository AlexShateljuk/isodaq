"""Command buttons, toggles, sliders and number boxes (CMP10 / CMP11)."""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")

from core.controls import Control, load_controls


# ── Model ───────────────────────────────────────────────────────────────────────

def test_render_fills_value_and_escapes():
    c = Control(kind="slider", label="PWM", command=r"SET PWM={value}\x21", step=1)
    assert c.render(42) == b"SET PWM=42!"
    c = Control(kind="number", label="V", command="V={value}", step=0.05, maximum=5)
    assert c.render(1.5) == b"V=1.50"


def test_render_toggle_sides():
    c = Control(kind="toggle", label="Fan", command="FAN ON", command_off="FAN OFF")
    assert c.render(on=True) == b"FAN ON"
    assert c.render(on=False) == b"FAN OFF"


def test_snap_stays_on_steps_and_in_range():
    c = Control(kind="slider", label="x", command="{value}", minimum=10, maximum=20, step=2.5)
    assert c.snap(11.2) == 10
    assert c.snap(11.4) == 12.5
    assert c.snap(13) == 12.5
    assert c.snap(99) == 20
    assert c.snap(-5) == 10


@pytest.mark.parametrize("kwargs", [
    {"kind": "button", "label": "", "command": "x"},
    {"kind": "button", "label": "a", "command": "  "},
    {"kind": "nope", "label": "a", "command": "x"},
    {"kind": "toggle", "label": "a", "command": "ON"},
    {"kind": "slider", "label": "a", "command": "no placeholder"},
    {"kind": "slider", "label": "a", "command": "{value}", "minimum": 5, "maximum": 5},
    {"kind": "slider", "label": "a", "command": "{value}", "step": 0},
    {"kind": "slider", "label": "a", "command": "{value}", "maximum": 1e9, "step": 0.001},
    {"kind": "number", "label": "a", "command": "{value}", "maximum": float("inf")},
    {"kind": "slider", "label": "a", "command": "{value}", "step": float("nan")},
])
def test_invalid_definitions_are_rejected(kwargs):
    assert Control(**kwargs).validate() is not None


def test_load_skips_junk_and_cleans_keys():
    got = load_controls(json.loads(json.dumps([
        {"kind": "button", "label": "A", "command": "a", "key": "F1"},
        {"kind": "button", "label": "B", "command": "b", "key": "F1"},    # taken
        {"kind": "button", "label": "C", "command": "c", "key": "F5"},    # the app's
        {"kind": "slider", "label": "D", "command": "{value}", "key": "F2", "value": 500},
        {"kind": "bogus", "label": "E", "command": "e"},
        {"kind": "button", "label": "F", "command": "f", "minimum": "x"},
        "not a dict",
    ])))
    assert [c.label for c in got] == ["A", "B", "C", "D"]
    assert [c.key for c in got] == ["F1", "", "", ""]
    assert got[3].value == 100          # clamped into its range
    assert load_controls({"not": "a list"}) == []


def test_load_rejects_nan_from_json():
    data = json.loads('[{"kind":"slider","label":"x","command":"{value}","value":NaN}]')
    assert load_controls(data) == []


# ── Panel ───────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def panel(qapp):
    from ui.control_panel import ControlPanel
    sent = []
    p = ControlPanel(lambda data, shown, eol: sent.append((data, shown, eol)) or p.send_ok)
    p.send_ok = True
    p.sent = sent
    p.resize(400, 400)
    return p


def _pump(qapp, ms):
    import time
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.005)


def test_loading_sends_nothing(panel):
    panel.from_dict_list([
        {"kind": "toggle", "label": "Fan", "command": "ON", "command_off": "OFF", "value": 1},
        {"kind": "slider", "label": "PWM", "command": "P={value}", "value": 40},
        {"kind": "number", "label": "V", "command": "V={value}", "value": 3},
    ])
    assert panel.sent == []


def test_button_key_and_palette(panel):
    panel.from_dict_list([{"kind": "button", "label": "Reset", "command": r"RST\x21",
                           "key": "F6", "line_ending": False}])
    panel.fire_key("F6")
    assert panel.sent == [(b"RST!", "RST!", False)]
    panel.fire_key("F7")                 # not bound
    assert len(panel.sent) == 1
    (label, run), = panel.run_commands()
    assert label == "Reset"
    run()
    assert len(panel.sent) == 2


def test_control_bytes_are_shown_safely(panel):
    panel.from_dict_list([{"kind": "button", "label": "STX", "command": r"\x02GO"}])
    panel.press(panel.controls[0])
    assert panel.sent[0][1] == "·GO"


def test_toggle_reverts_if_send_fails(panel):
    panel.from_dict_list([{"kind": "toggle", "label": "Fan", "command": "ON",
                           "command_off": "OFF", "key": "F2"}])
    fan = panel.controls[0]
    panel.fire_key("F2")
    assert panel.sent[-1][0] == b"ON" and fan.value == 1
    panel.send_ok = False
    panel.fire_key("F2")
    assert panel.sent[-1][0] == b"OFF"
    assert fan.value == 1 and panel._toggles[id(fan)].isChecked()   # still on


def test_slider_throttles_and_always_sends_last(panel, qapp):
    from ui.control_panel import SLIDER_THROTTLE_MS, _SliderRow
    panel.from_dict_list([{"kind": "slider", "label": "PWM", "command": "P={value}"}])
    row = panel.findChild(_SliderRow)
    for v in range(1, 51):               # a fast drag
        row._slider.setValue(v)
    assert panel.sent == []
    _pump(qapp, SLIDER_THROTTLE_MS + 50)
    assert panel.sent[-1][0] == b"P=50" and len(panel.sent) == 1
    row._slider.setValue(60)
    row._flush()                         # mouse released
    assert panel.sent[-1][0] == b"P=60"
    _pump(qapp, SLIDER_THROTTLE_MS + 50)
    assert len(panel.sent) == 2          # no repeat of the same value


def test_number_box_clamps_and_sends(panel):
    from ui.control_panel import _NumberRow
    panel.from_dict_list([{"kind": "number", "label": "V", "command": "V={value}",
                           "maximum": 5, "step": 0.5}])
    row = panel.findChild(_NumberRow)
    row._box.setValue(2.5)
    row.submit()
    assert panel.sent[-1][0] == b"V=2.5"


def test_import_adds_and_keeps_existing_shortcuts(panel, tmp_path):
    panel.from_dict_list([{"kind": "button", "label": "Mine", "command": "m", "key": "F1"}])
    f = tmp_path / "c.json"
    f.write_text(json.dumps([{"kind": "button", "label": "Theirs", "command": "t", "key": "F1"},
                             {"kind": "button", "label": "Other", "command": "o", "key": "F2"}]))
    panel.import_path(str(f))
    assert [(c.label, c.key) for c in panel.controls] == \
        [("Mine", "F1"), ("Theirs", ""), ("Other", "F2")]


def test_columns_follow_width(panel, qapp):
    panel.from_dict_list([{"kind": "button", "label": str(i), "command": "x"} for i in range(6)])
    panel.show()
    panel.resize(250, 300)
    qapp.processEvents()
    assert panel._columns == 2
    panel.resize(750, 300)
    qapp.processEvents()
    assert panel._columns == 6
    panel.close()


def test_number_box_shows_plain_numbers(qapp):
    from PyQt6.QtCore import QLocale

    from ui.number_box import NumberBox
    QLocale.setDefault(QLocale(QLocale.Language.German))   # comma decimals
    try:
        box = NumberBox()
        box.setDecimals(4)
        box.setRange(-1000, 100000)
        for value, text in ((50, "50"), (3.3, "3.3"), (0.1, "0.1"), (1500, "1500"), (-2.5, "-2.5")):
            box.setValue(value)
            assert box.text() == text
            assert box.value() == value
    finally:
        QLocale.setDefault(QLocale.c())
