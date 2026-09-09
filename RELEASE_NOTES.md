A bug-fix release. If you script against the CLI, this one matters.

> Unofficial project. Not affiliated with, endorsed by, or supported by Anthropic.

## What changed since v1.0.0

**`set --rebuild` could leave the config and the installed app disagreeing.**
If the profile was open, it saved the new name or icon to `config.json`, skipped
the rebuild, and still exited `0`. A script saw success while the config
described a profile the `.app` on disk didn't match.

It's now all-or-nothing. Values are validated first, the "is it running" check
happens *before* the config is touched, and if the build fails for any other
reason the config is restored. That last part is only safe because builds are
staged and swapped into place, so a failed build never modifies the installed
bundle. Pass `--force` to replace a running profile anyway.

**Commands now exit non-zero when they fail.** `build --id nope`, `remove`,
`launch`, and `add` with an invalid ID all used to exit `0`. You can branch on
the exit status now instead of parsing stdout.

**`list --json` reports `pending_rename`.** `set` without `--rebuild` still
defers on purpose — the config is the source of truth and the bundle is derived
from it — so this flag tells you a rename is recorded but not yet built.

Nothing else changed. If you don't script against the CLI, upgrading is
optional.

## Install

**From source (recommended).** Compiles on your machine, so nothing is
quarantined and there's no Gatekeeper prompt to work around:

```bash
git clone https://github.com/sarogamedev/MoreClaude.git
cd MoreClaude
./install.sh
```

Already installed? `git pull && ./install.sh` — your profiles and logins are
untouched.

**From the prebuilt download.** Universal (Apple Silicon + Intel). It is ad-hoc
signed and **not notarized**, so macOS blocks it until you clear the quarantine
flag:

```bash
unzip MoreClaude-v1.0.1-macos-universal.zip -d /Applications
xattr -dr com.apple.quarantine "/Applications/More Claude.app"
open "/Applications/More Claude.app"
```

Or double-click, let macOS block it, then **System Settings → Privacy &
Security → Open Anyway**.

Verify the download if you like:

```bash
shasum -a 256 -c SHA256SUMS
```

The prebuilt app does not install the background watcher that rebuilds profiles
after a Claude Desktop update. Run `./install.sh` from the repo for that.

## Requirements

- macOS 14 (Sonoma) or later
- Xcode Command Line Tools (`xcode-select --install`)
- Python 3
- Claude Desktop at `/Applications/Claude.app`

## Known limitations

- Profile apps are ad-hoc signed, so they can't carry Claude.app's original
  Developer ID signature.
- A profile's menu bar still says "Claude". Electron locates its helper
  processes via `CFBundleName`, so renaming it stops the app from starting.
  The Dock, app switcher and Finder do show the profile name.
- Rebuilds take a couple of minutes — each copies and re-signs a multi-gigabyte
  bundle. The watcher skips open profiles and retries once you quit them.
- Two accounts editing the same file at once can still overwrite each other.
