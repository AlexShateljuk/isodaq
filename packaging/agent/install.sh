#!/bin/sh
# Install isodaq-agent as a service on a Raspberry Pi (or any Linux with systemd).
#
#   sudo sh packaging/agent/install.sh [--port 7780] [--name lab-pi] [--allow PATTERN]
#   sudo sh packaging/agent/install.sh --uninstall [--purge]
#
# What it does:
#   * makes sure python3 can import pyserial and cryptography (apt packages on
#     Debian / Raspberry Pi OS: nothing is compiled)
#   * copies the agent to /opt/isodaq-agent and puts an `isodaq-agent` command in
#     /usr/local/bin
#   * creates the system user `isodaq-agent` (in the dialout group, for the ports)
#     with its settings, certificate and paired clients in /var/lib/isodaq-agent
#   * installs and starts isodaq-agent.service, then prints a pairing code
#
# Run it again to update the agent; pairings and the certificate are kept.
#
# ISODAQ_AGENT_ROOT=/some/folder installs into that folder instead and skips
# everything that changes the system (user, apt, systemd). Used by the tests.
set -eu

PORT=7780
NAME=""
ALLOW='/dev/serial/by-id/*'
UNINSTALL=0
PURGE=0
PRINT_CODE=1

while [ $# -gt 0 ]; do
    case "$1" in
        --port) PORT="$2"; shift 2 ;;
        --name) NAME="$2"; shift 2 ;;
        --allow) ALLOW="$2"; shift 2 ;;
        --no-allow) ALLOW=""; shift ;;
        --uninstall) UNINSTALL=1; shift ;;
        --purge) PURGE=1; shift ;;
        --no-code) PRINT_CODE=0; shift ;;
        -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
    esac
done

case "$PORT" in
    ''|*[!0-9]*) echo "--port needs a number" >&2; exit 2 ;;
esac
case "$NAME" in
    *[!A-Za-z0-9._-]*) echo "--name: letters, digits, '.', '_' and '-' only" >&2; exit 2 ;;
esac
case "$ALLOW" in
    *[!A-Za-z0-9./_*?:-]*) echo "--allow: a path pattern such as /dev/serial/by-id/*" >&2; exit 2 ;;
esac

ROOT="${ISODAQ_AGENT_ROOT:-}"
SYSTEM=1
SERVICE_USER=isodaq-agent
if [ -n "$ROOT" ]; then
    SYSTEM=0
    SERVICE_USER="$(id -un)"
fi
LIB="$ROOT/opt/isodaq-agent"
BIN="$ROOT/usr/local/bin/isodaq-agent"
STATE="$ROOT/var/lib/isodaq-agent"
UNIT="$ROOT/etc/systemd/system/isodaq-agent.service"
HERE="$(cd "$(dirname "$0")" && pwd)"

say() { printf '%s\n' "$*"; }

if [ "$SYSTEM" = 1 ] && [ "$(id -u)" != 0 ]; then
    say "Run this with sudo: it installs a system service." >&2
    exit 1
fi

# ── Uninstall ────────────────────────────────────────────────────────────────
if [ "$UNINSTALL" = 1 ]; then
    if [ "$SYSTEM" = 1 ] && command -v systemctl >/dev/null 2>&1; then
        systemctl disable --now isodaq-agent.service 2>/dev/null || true
    fi
    rm -f "$UNIT" "$BIN"
    rm -rf "$LIB"
    [ "$SYSTEM" = 1 ] && command -v systemctl >/dev/null 2>&1 && systemctl daemon-reload
    if [ "$PURGE" = 1 ]; then
        rm -rf "$STATE"
        if [ "$SYSTEM" = 1 ] && id isodaq-agent >/dev/null 2>&1; then
            userdel isodaq-agent || true
        fi
        say "isodaq-agent removed, with its certificate and pairings."
    else
        say "isodaq-agent removed. Its certificate and pairings are kept in $STATE"
        say "(--uninstall --purge removes them too)."
    fi
    exit 0
fi

# ── Where the agent's code is ────────────────────────────────────────────────
SRC=""
for candidate in "$HERE/isodaq_agent" "$HERE/../../isodaq_agent"; do
    if [ -f "$candidate/server.py" ]; then
        SRC="$(cd "$candidate" && pwd)"
        break
    fi
done
if [ -z "$SRC" ]; then
    say "Can't find the isodaq_agent folder next to this script or in the repository." >&2
    exit 1
fi

# ── Python and its two packages ──────────────────────────────────────────────
PYTHON="$(command -v python3 || true)"
if [ -z "$PYTHON" ]; then
    if [ "$SYSTEM" = 1 ] && command -v apt-get >/dev/null 2>&1; then
        apt-get install -y python3
        PYTHON="$(command -v python3)"
    else
        say "python3 is needed (3.8 or later)." >&2
        exit 1
    fi
fi
if ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 8))'; then
    say "python3 is $("$PYTHON" -V 2>&1); isodaq-agent needs 3.8 or later." >&2
    exit 1
fi
if ! "$PYTHON" -c 'import serial, cryptography' 2>/dev/null; then
    if [ "$SYSTEM" = 1 ] && command -v apt-get >/dev/null 2>&1; then
        say "Installing python3-serial and python3-cryptography…"
        apt-get install -y python3-serial python3-cryptography
    else
        say "python3 can't import pyserial and cryptography." >&2
        say "Install them, e.g. with your package manager, or: $PYTHON -m pip install pyserial cryptography" >&2
        exit 1
    fi
fi

# ── Files ────────────────────────────────────────────────────────────────────
mkdir -p "$LIB" "$(dirname "$BIN")" "$(dirname "$UNIT")"
rm -rf "$LIB/isodaq_agent"
mkdir -p "$LIB/isodaq_agent"
cp "$SRC"/*.py "$LIB/isodaq_agent/"
chmod 755 "$LIB" "$LIB/isodaq_agent"
chmod 644 "$LIB/isodaq_agent/"*.py

cat > "$BIN" <<EOF
#!/bin/sh
# isodaq-agent: the agent's commands (serve, pair, clients, revoke, ports), run as
# its service user on its settings folder, so a code made by \`sudo isodaq-agent
# pair\` is one the running service can read.
export PYTHONPATH="$LIB"
if [ "\$(id -un)" = "$SERVICE_USER" ]; then
    exec "$PYTHON" -m isodaq_agent --config "$STATE" "\$@"
elif [ "\$(id -u)" = 0 ]; then
    exec runuser -u "$SERVICE_USER" -- env PYTHONPATH="$LIB" "$PYTHON" -m isodaq_agent --config "$STATE" "\$@"
fi
exec sudo -u "$SERVICE_USER" env PYTHONPATH="$LIB" "$PYTHON" -m isodaq_agent --config "$STATE" "\$@"
EOF
chmod 755 "$BIN"

# ── User and settings folder ─────────────────────────────────────────────────
if [ "$SYSTEM" = 1 ]; then
    if ! id isodaq-agent >/dev/null 2>&1; then
        useradd --system --home-dir "$STATE" --no-create-home --shell /usr/sbin/nologin \
            --user-group isodaq-agent
    fi
    getent group dialout >/dev/null 2>&1 && usermod -a -G dialout isodaq-agent
fi
mkdir -p "$STATE"
chmod 700 "$STATE"
[ "$SYSTEM" = 1 ] && chown -R isodaq-agent:isodaq-agent "$STATE"

# ── Service ──────────────────────────────────────────────────────────────────
ARGS="serve --port $PORT"
[ -n "$NAME" ] && ARGS="$ARGS --name $NAME"
[ -n "$ALLOW" ] && ARGS="$ARGS --allow '$ALLOW'"
sed -e "s|@PYTHON@|$PYTHON|g" -e "s|@LIB@|$LIB|g" -e "s|@STATE@|$STATE|g" \
    -e "s|@ARGS@|$ARGS|g" "$HERE/isodaq-agent.service" > "$UNIT"
chmod 644 "$UNIT"

if [ "$SYSTEM" = 1 ]; then
    if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
        systemctl daemon-reload
        systemctl enable isodaq-agent.service >/dev/null 2>&1
        systemctl restart isodaq-agent.service
        sleep 1
        if ! systemctl is-active --quiet isodaq-agent.service; then
            say "The service didn't start. See: journalctl -u isodaq-agent -n 50" >&2
            exit 1
        fi
        say "isodaq-agent is running and starts at boot (port $PORT)."
    else
        say "No systemd here: start the agent with  isodaq-agent $ARGS"
    fi
fi

if [ "$PRINT_CODE" = 1 ]; then
    "$BIN" pair --port "$PORT"
fi
