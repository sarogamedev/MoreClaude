#!/bin/bash
# Produces the artifacts for a GitHub release:
#
#     ./make_release.sh
#
# Output lands in dist/:
#   MoreClaude-v<version>-macos-universal.zip   the app, arm64 + x86_64
#   SHA256SUMS                                   checksums for the above
#
# The app is ad-hoc signed and NOT notarized, so macOS quarantines it on
# download. The release notes have to tell people how to get past that —
# see the "Installing the download" section in RELEASE_NOTES.md.
set -e

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="$(tr -d '[:space:]' < "$HERE/VERSION")"
DIST="$HERE/dist"
STAGE="$DIST/stage"
ZIP_NAME="MoreClaude-v$VERSION-macos-universal.zip"

echo "==> Running tests"
python3 -m unittest discover -s "$HERE/tests" -q

echo "==> Building universal app"
rm -rf "$DIST"
mkdir -p "$STAGE"
"$HERE/build_app.sh" --universal "$STAGE" >/dev/null

APP="$STAGE/More Claude.app"
echo "==> Verifying the build"
lipo -info "$APP/Contents/MacOS/MoreClaude" | sed 's/^/    /'
codesign --verify --strict --deep "$APP"
echo "    signature valid"

echo "==> Packaging"
# ditto, not zip: it preserves the code signature and resource forks, which a
# plain `zip` mangles badly enough that the unpacked app won't launch.
( cd "$STAGE" && ditto -c -k --sequesterRsrc --keepParent "More Claude.app" "$DIST/$ZIP_NAME" )

echo "==> Checking the packaged app still verifies after a round trip"
ROUNDTRIP="$DIST/roundtrip"
mkdir -p "$ROUNDTRIP"
ditto -x -k "$DIST/$ZIP_NAME" "$ROUNDTRIP"
codesign --verify --strict --deep "$ROUNDTRIP/More Claude.app"
echo "    signature survived packaging"
rm -rf "$ROUNDTRIP" "$STAGE"

( cd "$DIST" && shasum -a 256 "$ZIP_NAME" > SHA256SUMS )

echo ""
echo "Ready in dist/:"
ls -lh "$DIST" | awk 'NR>1 {print "    " $9 "  (" $5 ")"}'
echo ""
cat "$DIST/SHA256SUMS" | sed 's/^/    /'
echo ""
echo "To publish (creates a public release — review the notes first):"
echo "    gh release create v$VERSION \"$DIST/$ZIP_NAME\" \"$DIST/SHA256SUMS\" \\"
echo "        --title \"More Claude v$VERSION\" --notes-file RELEASE_NOTES.md"
