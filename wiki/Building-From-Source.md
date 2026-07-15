# Building From Source

## Run without building

```bash
git clone https://github.com/AlexShateljuk/isodaq.git
cd isodaq
pip install -r requirements.txt
python main.py
```

Requires **Python 3.10+**. For development extras (tests, linter), also install
`requirements-dev.txt`.

## Build a standalone binary

IsoDAQ Studio bundles with **PyInstaller**:

```bash
pip install pyinstaller>=6.0
pyinstaller isodaq.spec --noconfirm
```

The result lands in `dist/IsoDAQ Studio/`.

## Automated release builds (GitHub Actions)

Pushing a version tag triggers the CI release workflow, which builds for all
supported platforms and publishes a GitHub Release with the binaries attached:

```bash
git tag v0.1.0
git push origin v0.1.0
```

The workflow is
[`.github/workflows/release.yml`](https://github.com/AlexShateljuk/isodaq/blob/master/.github/workflows/release.yml).

> **macOS Intel:** GitHub's Intel runners are being retired and queue for hours,
> so releases ship an **Apple-Silicon (arm64)** build only. On an Intel Mac, run
> from source — it's pure Python.

> **Unsigned builds:** releases are not code-signed/notarized, so the OS shows a
> first-run warning. See [Getting Started](Getting-Started#option-a--pre-built-binary-no-python-needed).

## Running the checks

CI runs the same two checks you can run locally:

```bash
ruff check .
pytest
```

Please keep both green in a PR — see
[CONTRIBUTING.md](https://github.com/AlexShateljuk/isodaq/blob/master/CONTRIBUTING.md).

## Regenerating the documentation screenshots

The images under `docs/images/` (used by the README and this wiki) are generated,
not hand-captured:

```bash
python tools/gen_screenshots.py
```

It builds the real `MainWindow`, feeds it synthetic serial data, and saves
`widget.grab()` PNGs — so re-running it after a UI change keeps the docs current.
It uses a throwaway config path and never touches `~/.isodaq_studio/config.json`.
Run it on a machine with a display.

## Project layout

See the **Project structure** section of the
[README](https://github.com/AlexShateljuk/isodaq#project-structure) and
[docs/ARCHITECTURE.md](https://github.com/AlexShateljuk/isodaq/blob/master/docs/ARCHITECTURE.md)
for the module map, threading model, and a "where do I add…?" guide.
