"""Unit tests for core.signaling.normalize / _is_local (pure URL logic, no network)."""
from core.signaling import _is_local, normalize


# ── _is_local ─────────────────────────────────────────────────────────────────────

def test_is_local_for_loopback_and_private():
    assert _is_local("localhost")
    assert _is_local("127.0.0.1")
    assert _is_local("192.168.1.10")
    assert _is_local("10.0.0.5")


def test_is_local_false_for_public_host():
    assert not _is_local("isodaq-production.up.railway.app")
    assert not _is_local("8.8.8.8")


# ── normalize ─────────────────────────────────────────────────────────────────────

def test_normalize_strips_stray_path():
    assert normalize("https://relay.example.com/health") == "https://relay.example.com"


def test_normalize_upgrades_http_to_https_for_public_host():
    # This was the real colleague-404 bug: http against an HTTPS-only relay.
    assert normalize("http://isodaq-production.up.railway.app") == \
        "https://isodaq-production.up.railway.app"


def test_normalize_preserves_http_for_localhost():
    assert normalize("http://localhost:9877") == "http://localhost:9877"


def test_normalize_preserves_http_for_private_lan():
    assert normalize("http://192.168.1.50:9877") == "http://192.168.1.50:9877"


def test_normalize_adds_scheme_when_missing():
    assert normalize("relay.example.com") == "https://relay.example.com"


def test_normalize_keeps_port():
    assert normalize("https://relay.example.com:8443/x") == "https://relay.example.com:8443"
