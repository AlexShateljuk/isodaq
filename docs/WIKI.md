# Publishing the wiki

The Markdown sources for the GitHub Wiki live in [`../wiki/`](../wiki) and are
version-controlled with the code. GitHub serves the wiki from a **separate git
repository** (`<repo>.wiki.git`); publishing = pushing these files there.

Page images are **not** duplicated into the wiki repo — the pages reference the
screenshots in this repo by absolute raw URL
(`https://raw.githubusercontent.com/AlexShateljuk/isodaq/master/docs/images/…`),
so a single `tools/gen_screenshots.py` run keeps both the README and the wiki
current.

## File → page mapping

GitHub turns each `*.md` filename into a wiki page title (hyphens become spaces).
Special pages:

- `Home.md` → the wiki landing page
- `_Sidebar.md` → the navigation sidebar shown on every page
- `_Footer.md` → the footer shown on every page

## First-time publish

The wiki repo only exists once you've created **at least one page** through the
GitHub UI (open the repo's **Wiki** tab → **Create the first page** → Save). Then:

```bash
# from the repo root
git clone https://github.com/AlexShateljuk/isodaq.wiki.git /tmp/isodaq-wiki
cp wiki/*.md wiki/_Sidebar.md wiki/_Footer.md /tmp/isodaq-wiki/
cd /tmp/isodaq-wiki
git add -A
git commit -m "docs: publish wiki pages"
git push
```

## Updating later

Re-copy the changed files and push again:

```bash
cp wiki/*.md /tmp/isodaq-wiki/ && cd /tmp/isodaq-wiki && git commit -am "docs: update wiki" && git push
```

## Keeping README links valid

The README links to wiki pages as
`https://github.com/AlexShateljuk/isodaq/wiki/<Page-Name>`. If you rename a
`wiki/*.md` file, update the matching link in `README.md`.
