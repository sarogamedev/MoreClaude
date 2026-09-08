Run multiple Claude Desktop accounts on one Mac at the same time — each with its
own Dock icon and its own login — without losing a session when Claude Desktop
updates.

> Unofficial project. Not affiliated with, endorsed by, or supported by Anthropic.

## Install

**From source (recommended).** This compiles on your machine, so macOS doesn't
quarantine anything and there's no Gatekeeper prompt to work around:

```bash
git clone https://github.com/sarogamedev/MoreClaude.git
cd MoreClaude
./install.sh
```

**From the prebuilt download.** `MoreClaude-v1.0.0-macos-universal.zip` below is
a universal (Apple Silicon + Intel) build. It is ad-hoc signed and **not
notarized**, so macOS will refuse to open it until you clear the quarantine flag:

```bash
unzip MoreClaude-v1.0.0-macos-universal.zip -d /Applications
xattr -dr com.apple.quarantine "/Applications/More Claude.app"
open "/Applications/More Claude.app"
```

Or, without the terminal: double-click, let macOS block it, then go to
**System Settings → Privacy & Security** and click **Open Anyway**.

Verify the download first if you like:

```bash
shasum -a 256 -c SHA256SUMS
```

Note that the prebuilt app does not install the background watcher that rebuilds
your profiles after a Claude Desktop update. Run `./install.sh` from the repo for
that.

## Requirements

- macOS 14 (Sonoma) or later
- Xcode Command Line Tools (`xcode-select --install`)
- Python 3
- Claude Desktop at `/Applications/Claude.app`

## What's in it

- **Profiles.** Each is a separate copy of Claude.app with its own bundle
  identifier, Dock icon, display name and `--user-data-dir`. They run
  simultaneously and stay logged in independently.
- **Survives updates.** Login data lives outside the app bundle, so the bundle
  is disposable. A background LaunchAgent watches Claude.app's version and
  rebuilds every profile when it changes — your sessions are never touched.
- **Atomic rebuilds.** A rebuild is staged beside the installed profile and
  swapped in with two renames, so a profile is never left half-written and a
  failed rebuild leaves the previous one working.
- **An app and a CLI.** A SwiftUI window plus a menu bar item, over a Python
  engine that does the real work and remains usable on its own.
- **Uninstaller.** `./uninstall.sh` removes everything and keeps your logins by
  default (`--purge` if you want them gone, `--dry-run` to look first).

## Known limitations

- **Profile apps are ad-hoc signed.** They can't carry Claude.app's original
  Developer ID signature, because the bundle is modified. Team-bound
  entitlements are dropped and library validation is disabled so the app can
  still run.
- **A profile's menu bar still says "Claude"**, not the profile name. Electron
  locates its helper processes via `CFBundleName`, so renaming it stops the app
  from starting. The Dock, app switcher and Finder do show the profile name.
- **Rebuilds take a couple of minutes** — each one copies and re-signs a
  multi-gigabyte app bundle. The watcher skips profiles that are open and
  retries once you quit them.
- **Two accounts editing the same file at once** can still overwrite each
  other. The isolation is about accounts and data, not file locking.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

28 tests against a synthetic Electron-shaped bundle, exercising the real signing
path.
