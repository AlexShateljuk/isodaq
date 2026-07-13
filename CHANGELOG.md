# Changelog

All notable changes to IsoDAQ Studio are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.2] — 2026-07-13

This is primarily an open-source-readiness release: licensing, security
hardening, tests + CI, docs, a major internal refactor, and i18n groundwork.

### Added
- **Apache-2.0 license** (`LICENSE`, `NOTICE`).
- Community & project docs: `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`,
  this changelog, and GitHub issue/PR templates.
- Unit test suite (`tests/`, 58 tests) covering triggers, data parser, logger,
  and signaling; `conftest.py`, `requirements-dev.txt`.
- CI workflow (`.github/workflows/ci.yml`): ruff lint + pytest on Python
  3.9/3.11/3.12. Ruff config in `pyproject.toml`.
- `docs/ARCHITECTURE.md` contributor overview; class/method docstrings across
  `core/`.
- `constraints.txt` with the exact tested dependency versions for reproducible
  installs (`pip install -r requirements.txt -c constraints.txt`).
- **Internationalization (i18n)** — lightweight JSON-catalog translations
  (`core/i18n.py`, `translations/`), a Ukrainian catalog, a language picker in
  Preferences, and `ISODAQ_LANG` / system-locale auto-detection. Menu bar is the
  first translated slice; coverage grows incrementally.
- **`ui/main_window.py` decomposition** — split the 2069-line god-object into 7
  focused controllers under `ui/controllers/` (down to 1003 lines); added an
  offscreen construction smoke test and a settings round-trip test.

### Changed
- **Relay hardening**: session cap, per-IP session cap, per-tunnel viewer cap,
  request body-size limit, and per-IP sliding-window rate-limiting on
  `/register` + `/lookup`. Real client IP taken from `X-Forwarded-For`. All caps
  env-tunable for self-hosts; `/health` exposes `sessions`/`limit`.
- Dependencies now have upper bounds (`requirements.txt`) to avoid surprise
  breakage from a future major release.
- `.gitignore` explicitly keeps `ui/resources/**` tracked so bundled assets
  can't be accidentally excluded by the screenshot ignore rules.

### Security
- **Blocked arbitrary code execution from shared trigger configs.** `[python]`
  triggers loaded from an untrusted file now load disabled and are never
  `eval()`'d until explicitly trusted; the load dialog warns and offers
  *Load & enable / Load disabled / Cancel*.

### Fixed
- **macOS release was mislabelled** — the `macos-latest` runner is Apple Silicon
  but the artifact was named `…-x64`, so Intel users got an incompatible binary.
  Releases now build both `macos-arm64` and `macos-x64` (on macos-14 / macos-13)
  with honest names. The `.app` is ad-hoc signed so arm64 builds run without the
  "damaged" error; README documents the Gatekeeper/SmartScreen workarounds.
- **Logger dropped the last buffered batch on session stop** — `Logger.stop()`
  closed the sinks before the writer thread flushed its sub-interval buffer, so
  the last ≤0.2 s of lines were silently lost. Added a flush handshake.
- Cleaned up lint findings (unused imports/loop variables).

## [0.2.1] — 2026-06-21
### Changed
- Right-panel layout polish; added design/dev tools (element inspector, live
  theme hot-reload, annotated UI map).

## [0.2.0] — 2026-06-20
### Added
- In-log search with highlight (Ctrl+F) and trigger→log-line jump (F1/F2).
- Internet session sharing: relay mode through NAT, multi-viewer support, viewer
  presence, host-close notification, relay latency indicator; received shared
  lines are logged.
- Application icon; macOS `.app` bundle build.

### Fixed
- Connect/disconnect indicator not always updating (B1).
- Signaling URL normalisation: strip stray paths, upgrade http→https for public
  hosts (fixed a join-failure 404).
- Find-bar visibility/layout, tab strip, and clipped buttons.

## [0.1.0] — 2026-04-21
### Added
- Initial release: serial terminal, real-time charts, trigger engine, custom
  parser snippets, indicator thresholds, analytics, Telegram/webhook
  notifications, chart/CSV export, dual CSV+SQLite logger, CLI/headless mode,
  auto-update check, Simple/Advanced mode, LAN session sharing with STUN +
  signaling server.

[Unreleased]: https://github.com/AlexShateljuk/isodaq/compare/v0.2.2...HEAD
[0.2.2]: https://github.com/AlexShateljuk/isodaq/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/AlexShateljuk/isodaq/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/AlexShateljuk/isodaq/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/AlexShateljuk/isodaq/releases/tag/v0.1.0
