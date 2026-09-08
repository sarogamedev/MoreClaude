# More Claude

Run any number of Claude Desktop accounts on one Mac at the same time, each
with its own Dock icon and its own login — without breaking when Claude
Desktop updates, and without switching macOS user profiles.

> **Unofficial project. Not affiliated with, endorsed by, or supported by
> Anthropic.** "Claude" and "Anthropic" are trademarks of Anthropic PBC, used
> here only to describe what this tool operates on. It works by duplicating
> and re-signing your own installed copy of Claude Desktop; it ships no
> Anthropic code, branding, or assets. Anthropic may add multi-account
> support to Claude Desktop itself one day, which would be the more robust
> answer — worth checking `support.claude.com` occasionally. Use at your own
> risk.

## How it survives updates

Each "profile" is a duplicate of `/Applications/Claude.app` with a unique
bundle identifier, name, and icon, and its executable wrapped so it always
launches with its own `--user-data-dir`. That data directory — where the
actual login session and chat history live — is stored **outside** the app
bundle, under `~/Library/Application Support/MoreClaudeProfiles/<profile-id>`.

That separation is what makes this update-safe: a background watcher checks
the real Claude.app's version periodically, and whenever it changes, it
deletes and rebuilds every profile's `.app` bundle from scratch. Since the
data directory is never touched by a rebuild, **you stay logged in through
every update.**

## Requirements

- macOS 14 (Sonoma) or later for the app; the CLI engine runs on older macOS
- Xcode Command Line Tools: `xcode-select --install` (provides `swiftc`,
  `codesign`, `PlistBuddy`, `sips`, `iconutil`)
- Python 3 (already on most Macs; `brew install python3` if not)
- Claude Desktop installed at `/Applications/Claude.app`

## Setup

```bash
./install.sh
```

There are prebuilt universal builds on the
[releases page](https://github.com/sarogamedev/MoreClaude/releases), but
installing from source is the better path: the download is ad-hoc signed and
not notarized, so macOS quarantines it and you have to clear that by hand,
and the prebuilt app doesn't set up the background update watcher. Building
locally avoids both.

This builds **More Claude.app** into `/Applications` (falling back to
`~/Applications` if that isn't writable) and installs a LaunchAgent that
watches for Claude Desktop updates in the background.

Then open the app and click **+** to add a profile. Give it a name — the ID
fills itself in — and optionally pick an icon (`.icns`, `.png` or `.jpg`;
anything but `.icns` is converted for you). Hit **Add** and it builds
straight away. Click **Launch** and log in, same as a fresh Claude Desktop
install.

To keep the menu bar item around permanently, add More Claude under
**System Settings → General → Login Items**.

## Everyday use

**The app** is the main way in:

- A sidebar of profiles, each showing whether it's *Not built*, *Ready*, or
  *Running*.
- **Launch** / **Rebuild** per profile, **Rebuild All** in the toolbar.
- **Edit…** to rename a profile or change its icon (it rebuilds so the change
  takes effect, and cleans up the old bundle).
- **Remove…**, which keeps the login data by default so re-adding the same ID
  restores the session — there's an option to delete it too.
- A **Log** pane showing exactly what the engine is doing, since a rebuild
  copies a multi-gigabyte app bundle and isn't instant.
- A menu bar item for launching a profile without opening the window.

**The CLI** does everything the app does, and the app is a front end for it:

```bash
MC="/Applications/More Claude.app/Contents/Resources/more_claude.py"

python3 "$MC" add --id work --name "Claude Work" --icon ~/icons/work.png
python3 "$MC" list                    # or: list --json
python3 "$MC" set --id work --name "Claude Acme" --rebuild
python3 "$MC" build --all [--force]   # --force rebuilds even if running
python3 "$MC" launch --id work
python3 "$MC" remove --id work [--purge]
python3 "$MC" watch                   # what the LaunchAgent runs
```

Profiles can point at the **same** working folder if you configure a
filesystem connector to the same path in each instance, so two accounts can
work on the same files — just avoid both editing the same file at once.

## Known limitations / things to check on your machine

- **Gatekeeper / first run**: ad-hoc signed apps sometimes need a right-click
  → Open the very first time, even after `xattr -cr`. If macOS refuses to
  open a profile, try that, or check `system.log` for a Gatekeeper denial.
- **Entitlements**: a rebuilt profile is re-signed ad-hoc, carrying over the
  master app's entitlements. Ad-hoc signatures can't carry entitlements that
  require a provisioning profile or a team identity, so if a profile launches
  but misbehaves in a way the real Claude.app doesn't, compare
  `codesign -d --entitlements - /Applications/Claude.app` against the same
  command on the profile bundle.
- **Rebuilding a running profile** is refused unless you force it, since
  replacing a bundle out from under a running app corrupts it. The watcher
  skips open profiles and retries on its next poll rather than forcing.
- **Profile IDs and names are validated**: an ID must be 1-64 characters of
  `A-Za-z0-9_-` (it names the login-data folder and is interpolated into the
  launcher script), and a name can't contain `/` or `\` (it becomes the
  `.app` filename). The engine enforces both, so a hand-edited `config.json`
  is re-checked at load rather than trusted.
- **Two accounts editing the same file at the same time** can still cause
  one to overwrite the other's changes — the isolation is about accounts and
  data, not file locking.
- **The watcher polls rather than using FSEvents** for simplicity (default:
  every 30s) — cheap on battery, but you can lower `--interval` in the
  LaunchAgent plist if you want faster detection after an update.
- The build pipeline has been run end to end against the real
  `/Applications/Claude.app`: the rebuilt profile passes
  `codesign --verify --strict --deep`, launches, and writes to its own data
  directory. If a background rebuild doesn't fire, check
  `~/Library/Logs/MoreClaude/watcher.log`.
- **A profile's menu bar still says "Claude"**, not the profile name.
  `CFBundleName` has to keep its original value or Electron can't find its
  helper processes; the Dock, app switcher and Finder all show the profile
  name via `CFBundleDisplayName` and the `.app` filename.

## Development

```bash
python3 -m unittest discover -s tests -v   # engine tests (macOS, needs clang)
./build_app.sh                             # -> ./build/More Claude.app
./build_app.sh --universal                 # arm64 + x86_64
./make_release.sh                          # -> ./dist/ zip + SHA256SUMS
```

`make_release.sh` runs the tests, builds universal, packages with `ditto` (a
plain `zip` mangles the code signature), and verifies the signature survives a
round trip through the archive.

## Building without installing

```bash
./build_app.sh            # -> ./build/More Claude.app
./build_app.sh ~/Desktop  # or anywhere else
```

## Uninstall

```bash
./uninstall.sh
```

It quits the app and any open profiles, unloads the watcher, unregisters the
bundles from Launch Services, and removes everything it installed. **Your
profile logins are kept** — re-adding a profile with the same ID restores that
session. Options:

| Flag | Effect |
|---|---|
| `--purge` | Also delete every profile's login data |
| `--dry-run` | List what would be removed, change nothing |
| `--yes` | Skip the confirmation prompt |
