"""Unit tests for core.triggers — matching, the security gate, and serialisation."""
from core.triggers import Trigger, TriggerEngine, parse_trigger_line


# ── parse_trigger_line ──────────────────────────────────────────────────────────

def test_parse_defaults_to_contains():
    assert parse_trigger_line("Brownout") == ("contains", "Brownout")


def test_parse_regex_prefix():
    assert parse_trigger_line("[regex] hard fault|HardFault") == (
        "regex", "hard fault|HardFault")


def test_parse_python_prefix():
    typ, pat = parse_trigger_line("[python] lambda l: 'x' in l")
    assert typ == "python"
    assert pat == "lambda l: 'x' in l"


# ── contains / regex matching ───────────────────────────────────────────────────

def test_contains_is_case_insensitive():
    t = Trigger(name="e", pattern="error", type="contains")
    assert t.compile() is None
    assert t.matches("fatal ERROR here")
    assert not t.matches("all good")


def test_regex_compiles_and_matches():
    t = Trigger(name="hf", pattern=r"hard ?fault", type="regex")
    assert t.compile() is None
    assert t.matches("HARDFAULT at 0x0800")
    assert t.matches("hard fault")


def test_regex_bad_pattern_returns_error():
    t = Trigger(name="bad", pattern=r"(unclosed", type="regex")
    err = t.compile()
    assert err is not None


def test_disabled_trigger_never_matches():
    t = Trigger(name="e", pattern="err", type="contains", enabled=False)
    t.compile()
    assert not t.matches("err")


# ── python triggers ─────────────────────────────────────────────────────────────

def test_python_lambda_numeric_threshold():
    t = Trigger(name="hot",
                pattern="lambda l: 'temp=' in l and int(l.split('temp=')[1].split()[0]) > 70",
                type="python")
    assert t.compile() is None
    assert t.matches("temp=90 ok")
    assert not t.matches("temp=20 ok")


def test_python_non_callable_rejected():
    t = Trigger(name="x", pattern="1 + 1", type="python")
    err = t.compile()
    assert err == "Expression must be callable (lambda)"


def test_python_runtime_exception_is_swallowed():
    # int('') raises; matches() must return False, not propagate.
    t = Trigger(name="x", pattern="lambda l: int(l.split('=')[1]) > 0", type="python")
    t.compile()
    assert t.matches("v=5") is True
    assert t.matches("no equals sign") is False


# ── security gate (OSS2) ─────────────────────────────────────────────────────────

def test_blocked_python_never_evals():
    t = Trigger(name="evil", pattern="lambda l: True", type="python", enabled=True)
    t._blocked = True
    # compile refuses, matches refuses — code path never reaches eval
    assert t.compile() == "Blocked: untrusted Python trigger (not enabled)"
    assert t.matches("anything") is False
    assert t._fn is None


def test_trust_reenables_blocked_python():
    t = Trigger(name="ok", pattern="lambda l: 'z' in l", type="python")
    t._blocked = True
    assert t.matches("z") is False
    assert t.trust() is None
    assert t.matches("z") is True


def test_from_dict_list_blocks_python_when_untrusted():
    eng = TriggerEngine()
    data = [
        {"name": "plain", "pattern": "boot", "type": "contains"},
        {"name": "code", "pattern": "lambda l: True", "type": "python"},
    ]
    eng.from_dict_list(data, allow_python=False)
    triggers = eng.get_triggers()
    py = [t for t in triggers if t.type == "python"][0]
    assert py._blocked is True
    assert py.enabled is False
    assert py.matches("x") is False       # inert
    # the contains rule is unaffected
    plain = [t for t in triggers if t.type == "contains"][0]
    assert plain.matches("boot") is True


def test_from_dict_list_allows_python_when_trusted():
    eng = TriggerEngine()
    data = [{"name": "code", "pattern": "lambda l: 'hit' in l", "type": "python"}]
    eng.from_dict_list(data, allow_python=True)
    py = eng.get_triggers()[0]
    assert py._blocked is False
    assert py.matches("a hit here") is True


def test_count_python():
    data = [
        {"type": "contains"}, {"type": "python"},
        {"type": "regex"}, {"type": "python"},
    ]
    assert TriggerEngine.count_python(data) == 2


# ── TriggerEngine hot path ───────────────────────────────────────────────────────

def test_engine_check_fires_callback_and_counts_hits():
    eng = TriggerEngine()
    seen = []
    eng.on_match(lambda t, line, ts: seen.append((t.name, line, ts)))
    eng.add_trigger(Trigger(name="err", pattern="err", type="contains"))
    eng.check("ERR happened", "00:00:01")
    eng.check("all fine", "00:00:02")
    eng.check("another err", "00:00:03")
    assert len(seen) == 2
    assert eng.get_triggers()[0].hit_count == 2


def test_engine_callback_exception_does_not_break_loop():
    eng = TriggerEngine()
    def boom(*_):
        raise RuntimeError("callback bug")
    hits = []
    eng.on_match(boom)
    eng.on_match(lambda *a: hits.append(a))
    eng.add_trigger(Trigger(name="x", pattern="go", type="contains"))
    eng.check("go go", "t")          # must not raise
    assert len(hits) == 1


def test_add_trigger_rejects_bad_regex():
    eng = TriggerEngine()
    err = eng.add_trigger(Trigger(name="b", pattern="(bad", type="regex"))
    assert err is not None
    assert eng.get_triggers() == []


def test_roundtrip_to_from_dict_list():
    eng = TriggerEngine()
    eng.add_trigger(Trigger(name="a", pattern="boot", type="contains", color="#123456"))
    eng.add_trigger(Trigger(name="b", pattern=r"\d+", type="regex", action_sound=True))
    data = eng.to_dict_list()

    eng2 = TriggerEngine()
    eng2.from_dict_list(data)
    out = eng2.get_triggers()
    assert [t.name for t in out] == ["a", "b"]
    assert out[0].color == "#123456"
    assert out[1].action_sound is True
    assert out[1].matches("value 42")
