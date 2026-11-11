# isodaq-agent on a Raspberry Pi

Serves the Pi's serial ports to IsoDAQ (the **Agent** transport).

**Easiest:** in IsoDAQ, Device → **Set up a Raspberry Pi…**, enter the Pi's address, user
and password. IsoDAQ copies these files over SSH, runs `install.sh` and pairs.

**By hand,** on the Pi, from a copy of the repository:

```bash
sudo sh packaging/agent/install.sh            # then pair in IsoDAQ with the printed code
sudo isodaq-agent pair                        # a new code later
sudo isodaq-agent clients                     # paired computers;  revoke NAME  removes one
journalctl -u isodaq-agent -f                 # logs
sudo sh packaging/agent/install.sh --uninstall [--purge]
```

| File | |
|---|---|
| `install.sh` | installs or updates; `--port`, `--name`, `--allow PATTERN`, `--no-allow` |
| `isodaq-agent.service` | systemd unit template (filled in by `install.sh`) |

See the wiki page *Serial Connection → Agent* for security notes.
