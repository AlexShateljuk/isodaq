cask "isodaq-studio" do
  version "0.3.0"
  sha256 "af3d3abaa97958a03f2770099eb124a0e993089ceb3c7cbb8aca9138da9b0b0e"

  url "https://github.com/AlexShateljuk/isodaq/releases/download/v#{version}/IsoDAQ-Studio-macos-arm64.zip",
      verified: "github.com/AlexShateljuk/isodaq/"
  name "IsoDAQ Studio"
  desc "Serial terminal and real-time data-acquisition frontend for embedded dev"
  homepage "https://github.com/AlexShateljuk/isodaq"

  livecheck do
    url :url
    strategy :github_latest
  end

  # Apple-Silicon build only; Intel Macs run from source (pure-Python app).
  depends_on arch: :arm64

  app "IsoDAQ Studio.app"

  # The release is ad-hoc signed but not notarised, so Gatekeeper quarantines it.
  # Strip the flag on install so it launches without the right-click → Open dance.
  postflight do
    system_command "/usr/bin/xattr",
                   args: ["-dr", "com.apple.quarantine", "#{appdir}/IsoDAQ Studio.app"]
  end

  zap trash: "~/.isodaq_studio"
end
