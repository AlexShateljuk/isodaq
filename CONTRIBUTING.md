# Contributing to IsoDAQ Studio

Thanks for your interest! IsoDAQ Studio is an open-source serial terminal + data
acquisition tool for embedded development. Contributions of all kinds are
welcome — bug reports, features, docs, tests.

## Getting started

```bash
git clone https://github.com/AlexShateljuk/isodaq.git
cd isodaq
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
python main.py
```

Requires **Python 3.9+**.

New to the codebase? Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for a map
of how the pieces fit together and where to add things.

## Before you open a PR

Run the same checks CI runs:

```bash
ruff check .     # lint
pytest           # unit tests
```

Both must pass. If you change behaviour in `core/`, add or update a test in
`tests/` — those modules (triggers, parser, logger, signaling) are covered and
we want to keep them that way.

## Coding style

- The codebase uses a deliberately **compact style** (e.g. `if not x: return`,
  aligned assignment blocks). Match the surrounding code; `ruff` is configured to
  allow it (see `pyproject.toml`).
- Keep GUI (`ui/`) and logic (`core/`) separated. New panel logic should live
  with its panel, not pile further into `ui/main_window.py`.
- Public functions and classes should carry a short docstring (args, return,
  side-effects, and thread-safety where relevant).

## Commit / PR conventions

- Branch off `master`. Keep PRs focused — one logical change.
- Use clear commit subjects; the history uses `type: summary` loosely
  (`feat:`, `fix:`, `ui:`, `docs:`, `build:`, `chore:`).
- Describe **what** and **why** in the PR body. Link any related issue.
- Add a line under `## [Unreleased]` in `CHANGELOG.md` for user-visible changes.

## Security

Found a vulnerability? Please **don't** open a public issue — see
[SECURITY.md](SECURITY.md) for private reporting. Note that `[python]` triggers
and parser snippets execute arbitrary code by design.

## Licensing

By contributing, you agree that your contributions are licensed under the
project's [Apache-2.0 License](LICENSE).
