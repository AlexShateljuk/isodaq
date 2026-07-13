# Translations

IsoDAQ Studio uses a lightweight JSON-catalog i18n (no Qt Linguist toolchain
required — see `core/i18n.py`). Each language is one flat JSON file mapping the
**English source string** to its translation.

## Adding / improving a language

1. Copy an existing catalog to `translations/<code>.json`, where `<code>` is the
   2-letter language code (e.g. `de.json`, `fr.json`, `es.json`). Region suffixes
   are stripped at load time (`uk_UA` → `uk`).
2. Translate the **values** only — keep the **keys** exactly as the English
   source (including punctuation and the `…` ellipsis), or the lookup won't match.
3. Missing keys fall back to English automatically, so a partial translation is
   fine — translate what you can.
4. Interpolated strings use `{name}` placeholders (Python `str.format`). Keep the
   placeholders intact, e.g. `"Connected: {port} @ {baud}"`.

## Selecting a language

At startup the app picks: `ISODAQ_LANG` env var → system locale → English.

```bash
ISODAQ_LANG=uk python main.py      # force Ukrainian
```

A change takes effect on the next launch.

## Status

This is an incremental effort. The menu bar is wrapped as the first slice;
coverage grows as strings are wrapped in `core.i18n.tr(...)`. Contributions
(both new languages and wrapping more strings) are welcome.
