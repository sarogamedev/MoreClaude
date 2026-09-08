#!/bin/bash
# Removes More Claude.
#
#   ./uninstall.sh              # remove the app, profiles and watcher
#   ./uninstall.sh --purge      # also delete every profile's login data
#   ./uninstall.sh --dry-run    # show what would be removed, change nothing
#   ./uninstall.sh --yes        # skip the confirmation prompt
set -e

PURGE=0
DRY_RUN=0
ASSUME_YES=0
for arg in "$@"; do
    case "$arg" in
        --purge)   PURGE=1 ;;
        --dry-run) DRY_RUN=1 ;;
        --yes|-y)  ASSUME_YES=1 ;;
        -h|--help) sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $arg (try --help)"; exit 1 ;;
    esac
done

APP_SUPPORT="$HOME/Library/Application Support/MoreClaude"
PROFILE_DATA="$HOME/Library/Application Support/MoreClaudeProfiles"
PROFILE_BUNDLES="$HOME/Applications/MoreClaude"
LOG_DIR="$HOME/Library/Logs/MoreClaude"
PLIST="$HOME/Library/LaunchAgents/com.moreclaude.watcher.plist"
LSREGISTER="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"

# Collect what actually exists, so the summary reflects this machine.
TARGETS=()
for path in "/Applications/More Claude.app" "$HOME/Applications/More Claude.app" \
            "$PROFILE_BUNDLES" "$APP_SUPPORT" "$LOG_DIR" "$PLIST"; do
    [ -e "$path" ] && TARGETS+=("$path")
done
if [ "$PURGE" -eq 1 ] && [ -e "$PROFILE_DATA" ]; then
    TARGETS+=("$PROFILE_DATA")
fi

if [ ${#TARGETS[@]} -eq 0 ]; then
    echo "Nothing to remove — More Claude doesn't appear to be installed."
    exit 0
fi

echo "This will remove:"
for path in "${TARGETS[@]}"; do
    printf '    %s\n' "$path"
done
echo ""
if [ "$PURGE" -eq 1 ]; then
    echo "  --purge given: your profile logins WILL be deleted."
elif [ -e "$PROFILE_DATA" ]; then
    echo "  Login data is kept at:"
    printf '    %s\n' "$PROFILE_DATA"
    echo "  Re-adding a profile with the same ID restores that session."
    echo "  Pass --purge to delete it too."
fi
echo ""

if [ "$DRY_RUN" -eq 1 ]; then
    echo "(--dry-run: nothing was changed)"
    exit 0
fi

if [ "$ASSUME_YES" -eq 0 ]; then
    printf 'Continue? [y/N] '
    read -r reply
    case "$reply" in
        [yY]|[yY][eE][sS]) ;;
        *) echo "Cancelled."; exit 0 ;;
    esac
fi

echo "==> Quitting More Claude and any open profiles"
osascript -e 'quit app "More Claude"' 2>/dev/null || true
pkill -f "More Claude.app/Contents/MacOS/MoreClaude" 2>/dev/null || true
pkill -f "$PROFILE_BUNDLES/.*\.app/Contents/MacOS/" 2>/dev/null || true
sleep 1

if [ -e "$PLIST" ]; then
    echo "==> Unloading the background watcher"
    launchctl unload "$PLIST" 2>/dev/null || true
fi

# Unregister the bundles before deleting them, so Finder and the Dock don't
# keep showing stale entries.
if [ -x "$LSREGISTER" ]; then
    echo "==> Unregistering app bundles from Launch Services"
    for app in "/Applications/More Claude.app" "$HOME/Applications/More Claude.app"; do
        [ -e "$app" ] && "$LSREGISTER" -u "$app" 2>/dev/null || true
    done
    if [ -d "$PROFILE_BUNDLES" ]; then
        for app in "$PROFILE_BUNDLES"/*.app; do
            [ -e "$app" ] && "$LSREGISTER" -u "$app" 2>/dev/null || true
        done
    fi
fi

echo "==> Removing files"
for path in "${TARGETS[@]}"; do
    printf '    %s\n' "$path"
    rm -rf "$path"
done

echo ""
echo "More Claude has been removed."
if [ "$PURGE" -eq 0 ] && [ -e "$PROFILE_DATA" ]; then
    echo "Your profile logins are still at:"
    echo "    $PROFILE_DATA"
    echo "Delete that directory if you want them gone too."
fi
