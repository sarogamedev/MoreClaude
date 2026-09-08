#!/bin/bash
# Builds "More Claude.app" from source. No Xcode project needed — swiftc plus
# a hand-assembled bundle, which is the same trick more_claude.py uses on
# Claude.app itself.
#
#   ./build_app.sh [destination-dir]     (default: ./build)
set -e

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

UNIVERSAL=0
DEST_DIR=""
for arg in "$@"; do
    case "$arg" in
        --universal) UNIVERSAL=1 ;;
        -h|--help)
            echo "usage: build_app.sh [--universal] [destination-dir]"
            echo "  --universal  build arm64 + x86_64 (for a release; slower)"
            exit 0 ;;
        *) DEST_DIR="$arg" ;;
    esac
done
DEST_DIR="${DEST_DIR:-$HERE/build}"
APP="$DEST_DIR/More Claude.app"
VERSION="$(tr -d "[:space:]" < "$HERE/VERSION")"
# ContentUnavailableView and the two-parameter onChange are macOS 14 APIs.
# Pin the deployment target so the built binary's minimum matches what
# Info.plist advertises, rather than defaulting to whatever the build
# machine happens to run.
MIN_MACOS="14.0"
# swiftc ignores MACOSX_DEPLOYMENT_TARGET, so the triple has to be explicit.
HOST_TARGET="$(uname -m)-apple-macos$MIN_MACOS"

if ! xcode-select -p >/dev/null 2>&1; then
    echo "Xcode Command Line Tools are required: xcode-select --install"
    exit 1
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# A local build only has to run on this Mac; a release build has to run on
# both Apple Silicon and Intel, so build each slice and lipo them together.
if [ "$UNIVERSAL" -eq 1 ]; then
    ARCHES="arm64 x86_64"
    echo "==> Compiling More Claude (universal: $ARCHES)"
else
    ARCHES="$(uname -m)"
    echo "==> Compiling More Claude ($ARCHES)"
fi

compile_slice() {
    swiftc -O -parse-as-library -target "$1-apple-macos$MIN_MACOS" \
        "$HERE/MoreClaudeApp/Engine.swift" \
        "$HERE/MoreClaudeApp/Views.swift" \
        "$HERE/MoreClaudeApp/MoreClaudeApp.swift" \
        -o "$2"
}

BUILD_OK=1
SLICES=""
for arch in $ARCHES; do
    if ! compile_slice "$arch" "$WORK/MoreClaude-$arch"; then
        BUILD_OK=0
        break
    fi
    SLICES="$SLICES $WORK/MoreClaude-$arch"
done

if [ "$BUILD_OK" -eq 1 ]; then
    # shellcheck disable=SC2086
    lipo -create -output "$WORK/MoreClaude" $SLICES
fi

if [ "$BUILD_OK" -eq 0 ]; then
    echo ""
    echo "swiftc failed. If the errors mention 'redefinition of module SwiftBridging'"
    echo "or 'could not build module Foundation', the Command Line Tools install is"
    echo "broken rather than anything in this project — a bare 'import Foundation'"
    echo "fails too. Reinstall them:"
    echo ""
    echo "    sudo rm -rf /Library/Developer/CommandLineTools"
    echo "    xcode-select --install"
    echo ""
    exit 1
fi

echo "==> Assembling the bundle"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
mv "$WORK/MoreClaude" "$APP/Contents/MacOS/MoreClaude"
chmod +x "$APP/Contents/MacOS/MoreClaude"

# The engine ships inside the app, so the GUI and its build logic can't drift.
cp "$HERE/more_claude.py" "$APP/Contents/Resources/more_claude.py"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>More Claude</string>
    <key>CFBundleDisplayName</key><string>More Claude</string>
    <key>CFBundleIdentifier</key><string>com.moreclaude.app</string>
    <key>CFBundleExecutable</key><string>MoreClaude</string>
    <key>CFBundleIconFile</key><string>AppIcon</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>$VERSION</string>
    <key>CFBundleVersion</key><string>$VERSION</string>
    <key>LSMinimumSystemVersion</key><string>$MIN_MACOS</string>
    <key>NSHighResolutionCapable</key><true/>
    <key>NSHumanReadableCopyright</key><string>MIT licensed</string>
</dict>
</plist>
PLIST

echo "==> Drawing the app icon"
# make_icon runs on this machine, so it only needs the host arch.
swiftc -O -target "$HOST_TARGET" "$HERE/MoreClaudeApp/make_icon.swift" -o "$WORK/make_icon"
"$WORK/make_icon" "$WORK/icon-master.png" >/dev/null

ICONSET="$WORK/AppIcon.iconset"
mkdir -p "$ICONSET"
for size in 16 32 128 256 512; do
    sips -z $size $size "$WORK/icon-master.png" \
        --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
    sips -z $((size * 2)) $((size * 2)) "$WORK/icon-master.png" \
        --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"

echo "==> Signing"
xattr -cr "$APP" || true
codesign --force --sign - "$APP"
codesign --verify "$APP"

# Make sure Finder/Dock pick up the icon and name straight away.
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
    -f "$APP" || true

echo ""
echo "Built: $APP"
