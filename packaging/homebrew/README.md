# Homebrew cask (macOS, Apple Silicon)

[`Casks/isodaq-studio.rb`](Casks/isodaq-studio.rb) is a ready-to-use Homebrew
cask. A Homebrew **tap** must live in its own repo named `homebrew-<name>`, so
this file is the source you copy there — it is not a tap by itself.

## Publish the tap (one-time)

1. Create a new GitHub repo named **`homebrew-isodaq`** (under your account).
2. Add the cask at `Casks/isodaq-studio.rb` (copy this file):
   ```bash
   git clone https://github.com/AlexShateljuk/homebrew-isodaq.git
   mkdir -p homebrew-isodaq/Casks
   cp packaging/homebrew/Casks/isodaq-studio.rb homebrew-isodaq/Casks/
   cd homebrew-isodaq && git add -A && git commit -m "isodaq-studio 0.3.0" && git push
   ```

Users then install with:

```bash
brew install --cask AlexShateljuk/isodaq/isodaq-studio
# or:  brew tap AlexShateljuk/isodaq && brew install --cask isodaq-studio
```

## Updating on a new release

Bump `version` and refresh `sha256`:

```bash
V=0.3.1   # new version
curl -sL -o /tmp/isodaq.zip \
  "https://github.com/AlexShateljuk/isodaq/releases/download/v$V/IsoDAQ-Studio-macos-arm64.zip"
shasum -a 256 /tmp/isodaq.zip
```

Put the new version + hash in the cask and push to `homebrew-isodaq`.
(`brew livecheck isodaq-studio` reports when a newer GitHub release exists.)

## Notes

- **Apple Silicon only** (`depends_on arch: :arm64`) — the release ships an arm64
  build; Intel Macs run from source.
- The build is **ad-hoc signed, not notarised**. The `postflight` step removes the
  `com.apple.quarantine` flag so the app opens normally. (This is fine for a
  personal tap; the official `homebrew-cask` repo does not accept quarantine
  removal, and would also require notarisation.)
- `brew audit --cask Casks/isodaq-studio.rb` before publishing to catch issues.
