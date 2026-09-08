#!/bin/bash
# Builds and installs More Claude: the app, and the background watcher that
# rebuilds your profiles whenever Claude Desktop updates.
#
# Run from inside the project folder: ./install.sh
set -e

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_SUPPORT="$HOME/Library/Application Support/MoreClaude"
mkdir -p "$APP_SUPPORT"

MACOS_MAJOR="$(sw_vers -productVersion | cut -d. -f1)"
if [ "$MACOS_MAJOR" -lt 14 ]; then
    echo "More Claude needs macOS 14 (Sonoma) or later; this Mac runs $(sw_vers -productVersion)."
    echo "The command-line engine still works on older systems:"
    echo "    python3 \"$HERE/more_claude.py\" --help"
    exit 1
fi

echo "==> Checking for Xcode Command Line Tools..."
if ! xcode-select -p >/dev/null 2>&1; then
    echo "Xcode Command Line Tools not found. Installing (a dialog will pop up)..."
    xcode-select --install
    echo "Re-run this script after the install finishes."
    exit 1
fi

# Prefer /Applications, but don't demand admin rights for it.
if [ -w /Applications ]; then
    INSTALL_DIR="/Applications"
else
    INSTALL_DIR="$HOME/Applications"
    mkdir -p "$INSTALL_DIR"
    echo "==> /Applications isn't writable; installing to $INSTALL_DIR instead"
fi
APP="$INSTALL_DIR/More Claude.app"

# Quit a running copy so we're not rebuilding a bundle that's in use.
if pgrep -f "More Claude.app/Contents/MacOS/MoreClaude" >/dev/null 2>&1; then
    echo "==> Quitting the running More Claude"
    osascript -e 'quit app "More Claude"' 2>/dev/null || true
    sleep 2
fi

echo "==> Building More Claude.app"
"$HERE/build_app.sh" "$INSTALL_DIR"

PYTHON3_PATH="$(command -v python3)"

# Record the interpreter: a GUI app inherits a minimal PATH and can't find a
# Homebrew python3 on its own.
printf '%s\n' "$PYTHON3_PATH" > "$APP_SUPPORT/python3-path"

echo "==> Installing background watcher (LaunchAgent)"
LA_DIR="$HOME/Library/LaunchAgents"
mkdir -p "$LA_DIR"

# launchd creates these with O_CREAT and follows symlinks, so keep them in a
# directory only this user can write rather than in shared /tmp.
LOG_DIR="$HOME/Library/Logs/MoreClaude"
mkdir -p "$LOG_DIR"
chmod 700 "$LOG_DIR"

# The watcher runs the same engine that ships inside the app, so there is
# exactly one copy of the build logic on disk.
PYTHON3_PATH="$PYTHON3_PATH" SCRIPT_PATH="$APP/Contents/Resources/more_claude.py" \
LOG_DIR="$LOG_DIR" \
"$PYTHON3_PATH" - "$HERE/launchd/com.moreclaude.watcher.plist" \
    "$LA_DIR/com.moreclaude.watcher.plist" <<'PY'
import os, sys
src, dst = sys.argv[1], sys.argv[2]
text = open(src).read()
text = text.replace("__PYTHON3__", os.environ["PYTHON3_PATH"])
text = text.replace("__SCRIPT__", os.environ["SCRIPT_PATH"])
text = text.replace("__LOGDIR__", os.environ["LOG_DIR"])
open(dst, "w").write(text)
PY

launchctl unload "$LA_DIR/com.moreclaude.watcher.plist" 2>/dev/null || true
launchctl load "$LA_DIR/com.moreclaude.watcher.plist"

echo ""
echo "Installed: $APP"
echo ""
echo "  1) Open it:            open \"$APP\""
echo "  2) Click + to add a profile, then Launch it and log in."
echo "  3) Optional: System Settings > General > Login Items, add More Claude"
echo "     so its menu bar item is always there."
echo ""
echo "The background watcher notices Claude Desktop updates and rebuilds every"
echo "profile automatically. Your logins live outside the app bundles and are"
echo "never touched by a rebuild."
