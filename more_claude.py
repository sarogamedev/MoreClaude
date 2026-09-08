#!/usr/bin/env python3
"""
more_claude.py — run any number of isolated Claude Desktop accounts on one Mac,
without breaking when Claude Desktop updates.

HOW IT WORKS
------------
For each "profile" (account) you define, this tool creates its own copy of
Claude.app under ~/Applications/MoreClaude/, with:
  - a unique bundle identifier + display name + icon (so macOS treats it as a
    distinct app: its own Dock icon, its own entry in Login Items, etc.)
  - its executable wrapped so it always launches with
    --user-data-dir=<profile-specific folder>

That --user-data-dir folder (under ~/Library/Application Support/MoreClaudeProfiles/<id>)
is where the actual login session, chat history, and settings live. The .app
bundle itself is disposable — it's just a signed shell that points at that
folder. That's what makes this update-safe: every time Claude Desktop updates,
this tool deletes the old per-profile .app bundles and rebuilds fresh copies
from the new master app, but never touches the data folders. Your logins
survive every rebuild.

USAGE
-----
  python3 more_claude.py add --id personal --name "Claude Personal" [--icon path.png]
  python3 more_claude.py add --id work     --name "Claude Work"     [--icon path.png]
  python3 more_claude.py list [--json]
  python3 more_claude.py set --id work --name "New Name" [--icon p.png] [--rebuild]
  python3 more_claude.py build --id work        # rebuild one profile
  python3 more_claude.py build --all            # rebuild every profile
  python3 more_claude.py build --all --force    # rebuild even if running
  python3 more_claude.py launch --id work       # build-if-needed, then open
  python3 more_claude.py remove --id work [--purge]   # --purge also deletes its data
  python3 more_claude.py watch                  # foreground loop; run via LaunchAgent

Requires: macOS, Xcode Command Line Tools (`xcode-select --install`) for
codesign/PlistBuddy/sips/iconutil, and Python 3.
"""

import argparse
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import time

CONFIG_PATH = os.path.expanduser("~/Library/Application Support/MoreClaude/config.json")
PLISTBUDDY = "/usr/libexec/PlistBuddy"
LSREGISTER = (
    "/System/Library/Frameworks/CoreServices.framework/Frameworks/"
    "LaunchServices.framework/Support/lsregister"
)

DEFAULT_CONFIG = {
    "master_app": "/Applications/Claude.app",
    "profiles_dir": "~/Applications/MoreClaude",
    "data_dir": "~/Library/Application Support/MoreClaudeProfiles",
    "state_dir": "~/Library/Application Support/MoreClaude",
    "profiles": [],
}


class BuildError(Exception):
    """A build step failed. Callers catch this so one bad profile doesn't
    abort a `build --all` (or kill the background watcher)."""


class InvalidProfile(Exception):
    """A profile's id or name isn't safe to turn into a path."""


# ------------------------------------------------------------- validation

# The id names the login-data folder and is interpolated into the launcher
# script, so keep it to characters that are inert in both a path and a shell.
VALID_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def validate_id(profile_id):
    if not isinstance(profile_id, str) or not VALID_ID.match(profile_id):
        raise InvalidProfile(
            f"invalid profile id {profile_id!r}: use 1-64 characters from "
            "A-Z a-z 0-9 _ -"
        )
    return profile_id


def validate_name(name):
    """The name becomes the .app filename, so it must be a single path
    component — no separators, and nothing that resolves to a parent."""
    if not isinstance(name, str) or not name.strip():
        raise InvalidProfile("a profile name can't be empty")
    if len(name) > 128:
        raise InvalidProfile("a profile name must be 128 characters or fewer")
    for bad in ("/", "\\", "\0"):
        if bad in name:
            raise InvalidProfile(
                f"invalid profile name {name!r}: it can't contain / or \\"
            )
    if name.strip() in (".", ".."):
        raise InvalidProfile(f"invalid profile name {name!r}")
    return name


def ensure_inside(path, parent, what):
    """Guard every destructive filesystem operation: refuse to touch anything
    that doesn't resolve to somewhere under `parent`."""
    real_parent = os.path.realpath(parent)
    real_path = os.path.realpath(path)
    if real_path != real_parent and not real_path.startswith(real_parent + os.sep):
        raise BuildError(
            f"refusing to touch a {what} outside {real_parent}: {real_path}"
        )
    return real_path


# pgrep -f takes an extended regular expression, so a profile name containing
# . + * ( or [ would otherwise change what the pattern matches.
_ERE_SPECIAL = re.compile(r"([.\[\]{}()*+?^$|\\])")


def ere_escape(text):
    return _ERE_SPECIAL.sub(r"\\\1", text)


# ---------------------------------------------------------------- utilities

def expand(path):
    return os.path.expanduser(path)


def run(cmd, check=True, **kwargs):
    print("  $ " + " ".join(cmd))
    return subprocess.run(cmd, check=check, **kwargs)


def _pb_quote(value):
    """Quote a value for a PlistBuddy -c command string, which is parsed as a
    shell-ish token list rather than passed through literally."""
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def plist_get(plist_path, key):
    r = subprocess.run(
        [PLISTBUDDY, "-c", f"Print :{key}", plist_path],
        capture_output=True, text=True,
    )
    return r.stdout.strip() if r.returncode == 0 else None


def plist_set(plist_path, key, value):
    quoted = _pb_quote(value)
    r = subprocess.run(
        [PLISTBUDDY, "-c", f"Set :{key} {quoted}", plist_path],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        r = subprocess.run(
            [PLISTBUDDY, "-c", f"Add :{key} string {quoted}", plist_path],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise BuildError(f"could not set {key} in {plist_path}: {r.stderr.strip()}")


def plist_delete(plist_path, key):
    subprocess.run([PLISTBUDDY, "-c", f"Delete :{key}", plist_path],
                    capture_output=True, text=True)


# ------------------------------------------------------------------ config

def load_config():
    if not os.path.exists(CONFIG_PATH):
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)

    # config.json is hand-editable, so re-check it rather than trusting that
    # whatever is in there came from this tool.
    for prof in cfg.get("profiles", []):
        try:
            validate_id(prof.get("id"))
            validate_name(prof.get("name"))
        except InvalidProfile as e:
            sys.exit(f"{CONFIG_PATH}: {e}")
    return cfg


def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


def find_profile(cfg, profile_id):
    for p in cfg["profiles"]:
        if p["id"] == profile_id:
            return p
    return None


def bundle_path(cfg, prof):
    """Where this profile's .app should live, given its current name."""
    return os.path.join(expand(cfg["profiles_dir"]), f'{prof["name"]}.app')


def built_bundle_path(cfg, prof):
    """Where this profile's .app actually is. Differs from bundle_path() when
    the profile was renamed in config.json since it was last built."""
    stored = prof.get("bundle_path")
    if stored:
        try:
            ensure_inside(stored, expand(cfg["profiles_dir"]), "bundle")
            return stored
        except BuildError:
            # A hand-edited bundle_path is not a licence to delete whatever it
            # points at; fall back to the path this profile's name implies.
            print(f"!! ignoring bundle_path outside the profiles directory: {stored}")
    return bundle_path(cfg, prof)


def is_app_running(app_path):
    """True if any process is executing out of this bundle — Electron helpers
    carry the bundle path in argv, so a single pgrep covers them all."""
    needle = os.path.join(app_path, "Contents", "MacOS") + os.sep
    r = subprocess.run(["pgrep", "-f", ere_escape(needle)],
                       capture_output=True, text=True)
    return r.returncode == 0


# -------------------------------------------------------------------- icon

def ensure_icns(path, workdir):
    """Return a path to a .icns file, converting from .png/.jpg if needed."""
    if not os.path.isfile(path):
        raise BuildError(f"icon not found: {path}")
    if path.lower().endswith(".icns"):
        return path

    iconset = os.path.join(workdir, "icon.iconset")
    os.makedirs(iconset)

    sizes = [16, 32, 64, 128, 256, 512, 1024]
    for size in sizes:
        out1x = f"{iconset}/icon_{size}x{size}.png"
        run(["sips", "-z", str(size), str(size), path, "--out", out1x],
            capture_output=True)
        if size <= 512:
            out2x = f"{iconset}/icon_{size}x{size}@2x.png"
            run(["sips", "-z", str(size * 2), str(size * 2), path, "--out", out2x],
                capture_output=True)

    out_icns = os.path.join(workdir, "icon.icns")
    run(["iconutil", "-c", "icns", iconset, "-o", out_icns])
    return out_icns


# ----------------------------------------------------------------- signing

# Entitlements that name a specific Apple Developer team. An ad-hoc signature
# has no team, so keeping these produces a binary macOS refuses to launch.
TEAM_BOUND_ENTITLEMENTS = (
    "com.apple.application-identifier",
    "com.apple.developer.team-identifier",
    "keychain-access-groups",
    "com.apple.developer.associated-domains",
)


def read_entitlements(path):
    """This item's entitlements as a dict, or None if it has none."""
    for cmd in (["codesign", "-d", "--entitlements", "-", "--xml", path],
                ["codesign", "-d", "--entitlements", ":-", path]):
        r = subprocess.run(cmd, capture_output=True, text=True)
        xml = r.stdout.strip()
        if r.returncode == 0 and xml.startswith("<?xml"):
            try:
                return plistlib.loads(xml.encode())
            except Exception:
                return None
    return None


def entitlements_for(path, workdir, tag):
    """Entitlements suitable for an ad-hoc signature: the item's own, minus
    the team-bound ones it can no longer satisfy, plus permission to load the
    rest of our ad-hoc code. (The hardened runtime otherwise enforces library
    validation, which demands every loaded library share a team identity we
    don't have.) Returns None for items that had no entitlements to begin
    with — a plain library doesn't need any."""
    ents = read_entitlements(path)
    if ents is None:
        return None
    for key in TEAM_BOUND_ENTITLEMENTS:
        ents.pop(key, None)
    ents["com.apple.security.cs.disable-library-validation"] = True
    out = os.path.join(workdir, f"entitlements-{tag}.plist")
    with open(out, "wb") as f:
        plistlib.dump(ents, f)
    return out


def nested_code(dest, main_executable):
    """Every nested signable item in the bundle, deepest first, so containers
    get signed after everything inside them."""
    found = []
    for sub in ("Frameworks", "Library", "Helpers", "XPCServices", "PlugIns", "Resources"):
        root = os.path.join(dest, "Contents", sub)
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            for name in dirnames:
                if name.endswith((".app", ".framework", ".xpc", ".bundle", ".appex")):
                    found.append(os.path.join(dirpath, name))
            for name in filenames:
                if name.endswith((".dylib", ".so")):
                    found.append(os.path.join(dirpath, name))

    # Contents/MacOS holds the app's real binary alongside our launcher. The
    # bundle signature only covers its *main* executable (the launcher), so
    # the real binary has to be signed in its own right — otherwise codesign
    # rejects the bundle with "code object is not signed at all". It is also
    # the image the launcher execs into, so it is what actually carries the
    # entitlements the running process gets.
    macos_dir = os.path.join(dest, "Contents", "MacOS")
    if os.path.isdir(macos_dir):
        for name in sorted(os.listdir(macos_dir)):
            path = os.path.join(macos_dir, name)
            if name != main_executable and os.path.isfile(path):
                found.append(path)

    found.sort(key=lambda p: p.count(os.sep), reverse=True)
    return found


def codesign_item(path, entitlements):
    cmd = ["codesign", "--force", "--sign", "-",
           # Keep the hardened-runtime flag, but never `requirements`: the
           # designated requirement names the original team's certificate,
           # which an ad-hoc signature can never satisfy, and preserving it
           # makes every nested item verify as "modified or invalid".
           "--preserve-metadata=flags,runtime"]
    if entitlements:
        cmd += ["--entitlements", entitlements]
    cmd.append(path)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise BuildError(f"codesign failed on {path}: {r.stderr.strip()}")


def master_counterpart(item, dest, master, main_executable):
    """The same item back in the untouched master app. Entitlements have to be
    read from there: by this point our copy's Info.plist has been rewritten,
    so the bundle's own signature is no longer readable."""
    return os.path.join(master, os.path.relpath(item, dest))


def sign_bundle(dest, master, main_executable, workdir):
    """Sign inside-out. `codesign --deep` is deprecated by Apple and applies
    one set of entitlements to every nested binary; instead each item is
    signed with its own entitlements, rewritten for an ad-hoc identity."""
    items = nested_code(dest, main_executable)
    print(f"  signing {len(items)} nested items, then the bundle itself")
    for index, item in enumerate(items):
        source = master_counterpart(item, dest, master, main_executable)
        codesign_item(item, entitlements_for(source, workdir, str(index)))
    codesign_item(dest, entitlements_for(master, workdir, "app"))


# --------------------------------------------------------------- versioning

def get_master_version(master_app):
    info = os.path.join(master_app, "Contents", "Info.plist")
    return (plist_get(info, "CFBundleShortVersionString")
            or plist_get(info, "CFBundleVersion"))


# ------------------------------------------------------------------- build

def build_profile(cfg, prof, force=False):
    """Build (or rebuild) one profile. Returns True on success; never raises,
    so a failure here can't abort build_all() or the watcher loop."""
    try:
        return _build_profile(cfg, prof, force)
    except (BuildError, subprocess.CalledProcessError, OSError) as e:
        print(f"!! Build of '{prof['id']}' failed: {e}")
        return False


STAGING_PREFIX = ".moreclaude-staging-"
REPLACED_PREFIX = ".moreclaude-replaced-"


def sweep_leftovers(profiles_dir):
    """Remove staging/replaced directories a previous run left behind (it was
    killed mid-build, or the machine slept and never came back)."""
    if not os.path.isdir(profiles_dir):
        return
    for name in os.listdir(profiles_dir):
        if name.startswith(STAGING_PREFIX) or name.startswith(REPLACED_PREFIX):
            stale = os.path.join(profiles_dir, name)
            print(f"  clearing leftover {name}")
            shutil.rmtree(stale, ignore_errors=True)


def swap_into_place(staging, dest):
    """Put the freshly built bundle at `dest`, replacing whatever is there.

    A rebuild copies and re-signs gigabytes, which takes minutes. Building
    straight into `dest` leaves the profile broken for that whole window — and
    permanently broken if the build is interrupted. So the build happens in a
    staging directory beside it (same filesystem, so the renames are atomic)
    and only the two renames below touch `dest`."""
    replaced = os.path.join(
        os.path.dirname(dest),
        REPLACED_PREFIX + os.path.basename(dest) + f"-{os.getpid()}",
    )
    had_previous = os.path.exists(dest)
    if had_previous:
        os.rename(dest, replaced)
    try:
        os.rename(staging, dest)
    except OSError:
        if had_previous:          # put the old one back rather than leave a hole
            os.rename(replaced, dest)
        raise
    if had_previous:
        shutil.rmtree(replaced, ignore_errors=True)


def _build_profile(cfg, prof, force):
    master = expand(cfg["master_app"])
    if not os.path.isdir(master):
        raise BuildError(f"master app not found at {master}")

    profiles_dir = expand(cfg["profiles_dir"])
    os.makedirs(profiles_dir, exist_ok=True)
    dest = bundle_path(cfg, prof)
    previous = built_bundle_path(cfg, prof)

    ensure_inside(dest, profiles_dir, "bundle")
    ensure_inside(previous, profiles_dir, "bundle")

    for path in {dest, previous}:
        if os.path.exists(path) and is_app_running(path) and not force:
            print(f"!! '{prof['id']}' is running — skipping rebuild. Quit it and "
                  f"try again, or pass --force to replace it anyway.")
            return False

    print(f"Building profile '{prof['id']}' -> {dest}")

    sweep_leftovers(profiles_dir)

    # Build beside the destination, not on top of it: the existing profile
    # keeps working (and stays launchable) until the new one is complete.
    staging = os.path.join(
        profiles_dir,
        STAGING_PREFIX + os.path.basename(dest) + f"-{os.getpid()}",
    )
    ensure_inside(staging, profiles_dir, "staging directory")
    shutil.rmtree(staging, ignore_errors=True)

    workdir = tempfile.mkdtemp(prefix="moreclaude-")
    built = False
    try:
        # Fast copy-on-write duplicate on APFS; fall back to a normal copy.
        try:
            run(["cp", "-Rc", master, staging], stderr=subprocess.PIPE)
        except subprocess.CalledProcessError:
            run(["cp", "-R", master, staging])

        info_plist = os.path.join(staging, "Contents", "Info.plist")
        bundle_id = f'com.moreclaude.profile.{prof["id"]}'
        plist_set(info_plist, "CFBundleIdentifier", bundle_id)
        # CFBundleDisplayName is what the Dock, app switcher and Finder show.
        # CFBundleName is deliberately left alone: Electron builds the path to
        # its helper apps from it ("<CFBundleName> Helper.app"), so renaming it
        # makes the app die with "Unable to find helper app". The distinct Dock
        # entry comes from the bundle identifier and the .app filename anyway.
        plist_set(info_plist, "CFBundleDisplayName", prof["name"])

        if prof.get("icon"):
            icns_path = ensure_icns(expand(prof["icon"]), workdir)
            resources = os.path.join(staging, "Contents", "Resources")
            os.makedirs(resources, exist_ok=True)
            target_icon = os.path.join(resources, "ProfileIcon.icns")
            shutil.copyfile(icns_path, target_icon)
            plist_set(info_plist, "CFBundleIconFile", "ProfileIcon")
            # Remove any newer icon-name key so it doesn't override our file.
            plist_delete(info_plist, "CFBundleIconName")

        # Pin every launch to this profile's own data directory by adding a
        # launcher alongside the real binary and pointing CFBundleExecutable
        # at it. The real binary keeps its original name on purpose: Electron
        # derives the helper-app path from the running executable's filename,
        # so renaming it makes the app die with "Unable to find helper app".
        real_name = plist_get(info_plist, "CFBundleExecutable")
        if not real_name:
            raise BuildError("master app has no CFBundleExecutable")
        macos_dir = os.path.join(staging, "Contents", "MacOS")
        real_path = os.path.join(macos_dir, real_name)
        if not os.path.exists(real_path):
            raise BuildError(f"executable {real_path} missing from the copied bundle")

        launcher_name = real_name + "-profile"
        launcher_path = os.path.join(macos_dir, launcher_name)

        data_dir = os.path.join(expand(cfg["data_dir"]), prof["id"])
        os.makedirs(data_dir, exist_ok=True)

        launcher = f'''#!/bin/bash
DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
exec "$DIR/{real_name}" --user-data-dir="{data_dir}" "$@"
'''
        with open(launcher_path, "w") as f:
            f.write(launcher)
        os.chmod(launcher_path, 0o755)
        plist_set(info_plist, "CFBundleExecutable", launcher_name)
        exec_name = launcher_name

        # Duplicated bundles can inherit a quarantine flag; strip it, then
        # re-sign ad-hoc since we've modified the bundle's contents.
        run(["xattr", "-cr", staging], check=False)
        sign_bundle(staging, master, exec_name, workdir)

        # The build took minutes; make sure the profile wasn't opened while
        # it ran, since swapping the bundle under a live app corrupts it.
        if os.path.exists(dest) and is_app_running(dest) and not force:
            print(f"!! '{prof['id']}' was opened during the rebuild — keeping the "
                  f"existing app. Quit it and rebuild.")
            return False

        # A rename in config.json would otherwise leave the old bundle orphaned.
        if previous != dest and os.path.exists(previous):
            print(f"  profile was renamed; removing stale bundle {previous}")
            shutil.rmtree(previous)

        swap_into_place(staging, dest)

        # Tell Launch Services / Finder / Dock about the (re)built app.
        run([LSREGISTER, "-f", dest], check=False)
        built = True
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        # Discard the staging copy on any failure. Because the build never
        # wrote to `dest`, a failed rebuild leaves the previous working
        # profile exactly as it was.
        if not built and os.path.exists(staging):
            print("  discarding incomplete build")
            shutil.rmtree(staging, ignore_errors=True)

    if prof.get("bundle_path") != dest:
        prof["bundle_path"] = dest
        save_config(cfg)

    print(f"Done: {prof['id']}")
    return True


def build_all(cfg, force=False):
    ok = True
    for prof in cfg["profiles"]:
        ok = build_profile(cfg, prof, force) and ok
    return ok


# ------------------------------------------------------------------ launch

def launch_profile(cfg, profile_id):
    prof = find_profile(cfg, profile_id)
    if not prof:
        print(f"No such profile: {profile_id}")
        return
    dest = built_bundle_path(cfg, prof)
    if not os.path.exists(dest):
        if not build_profile(cfg, prof):
            print("Build failed — not launching.")
            return
        dest = built_bundle_path(cfg, prof)
    run(["open", "-n", dest], check=False)


# -------------------------------------------------------------- crud verbs

def cmd_add(cfg, args):
    try:
        validate_id(args.id)
        validate_name(args.name)
    except InvalidProfile as e:
        print(f"!! {e}")
        return
    if find_profile(cfg, args.id):
        print(f"Profile '{args.id}' already exists.")
        return
    # Store an absolute icon path: rebuilds run from the watcher's working
    # directory, not the one you typed the command in.
    icon = os.path.abspath(expand(args.icon)) if args.icon else None
    if icon and not os.path.isfile(icon):
        print(f"Icon not found: {icon}")
        return
    cfg["profiles"].append({"id": args.id, "name": args.name, "icon": icon})
    save_config(cfg)
    print(f"Added profile '{args.id}'. Building it now...")
    if build_profile(cfg, find_profile(cfg, args.id)):
        print("You'll be prompted to log in the first time you launch this profile.")


def cmd_remove(cfg, args):
    prof = find_profile(cfg, args.id)
    if not prof:
        print(f"No such profile: {args.id}")
        return
    dest = built_bundle_path(cfg, prof)
    try:
        ensure_inside(dest, expand(cfg["profiles_dir"]), "bundle")
    except BuildError as e:
        print(f"!! {e}")
        return
    if os.path.exists(dest):
        if is_app_running(dest):
            print(f"!! '{args.id}' is running — quit it first.")
            return
        shutil.rmtree(dest)
    cfg["profiles"] = [p for p in cfg["profiles"] if p["id"] != args.id]
    save_config(cfg)
    if args.purge:
        data_dir = os.path.join(expand(cfg["data_dir"]), args.id)
        ensure_inside(data_dir, expand(cfg["data_dir"]), "data directory")
        if os.path.exists(data_dir):
            shutil.rmtree(data_dir)
        print(f"Removed profile '{args.id}' and deleted its login data.")
    else:
        print(f"Removed profile '{args.id}' (login data kept, in case you re-add it).")


def profile_status(cfg, prof):
    dest = built_bundle_path(cfg, prof)
    if not os.path.exists(dest):
        return "not_built"
    return "running" if is_app_running(dest) else "built"


STATUS_LABEL = {"not_built": "not built", "built": "built", "running": "built, running"}


def cmd_list(cfg, args):
    if args.json:
        # The GUI reads this. Nothing else on this path may print to stdout.
        master = expand(cfg["master_app"])
        print(json.dumps({
            "master_app": master,
            "master_installed": os.path.isdir(master),
            "master_version": get_master_version(master) if os.path.isdir(master) else None,
            "profiles_dir": expand(cfg["profiles_dir"]),
            "data_dir": expand(cfg["data_dir"]),
            "profiles": [
                {
                    "id": p["id"],
                    "name": p["name"],
                    "icon": p.get("icon"),
                    "bundle_path": built_bundle_path(cfg, p),
                    "data_path": os.path.join(expand(cfg["data_dir"]), p["id"]),
                    "status": profile_status(cfg, p),
                }
                for p in cfg["profiles"]
            ],
        }, indent=2))
        return

    if not cfg["profiles"]:
        print("No profiles yet. Add one with: more_claude.py add --id ... --name ...")
        return
    for p in cfg["profiles"]:
        print(f"  {p['id']:<15} {p['name']:<25} [{STATUS_LABEL[profile_status(cfg, p)]}]")


def cmd_set(cfg, args):
    """Change a profile's name or icon. The rename only takes effect on the
    next build, which is also what moves the .app bundle to its new path."""
    prof = find_profile(cfg, args.id)
    if not prof:
        print(f"No such profile: {args.id}")
        return
    if args.name:
        try:
            validate_name(args.name)
        except InvalidProfile as e:
            print(f"!! {e}")
            return
        prof["name"] = args.name
    if args.icon is not None:
        if args.icon == "":
            prof["icon"] = None
        else:
            icon = os.path.abspath(expand(args.icon))
            if not os.path.isfile(icon):
                print(f"Icon not found: {icon}")
                return
            prof["icon"] = icon
    save_config(cfg)
    print(f"Updated profile '{args.id}'.")
    if args.rebuild:
        build_profile(cfg, prof, args.force)


def cmd_build(cfg, args):
    if args.all:
        build_all(cfg, args.force)
    elif args.id:
        prof = find_profile(cfg, args.id)
        if not prof:
            print(f"No such profile: {args.id}")
            return
        build_profile(cfg, prof, args.force)
    else:
        print("Specify --id <profile> or --all")


def cmd_launch(cfg, args):
    launch_profile(cfg, args.id)


def cmd_watch(cfg, args):
    state_dir = expand(cfg["state_dir"])
    os.makedirs(state_dir, exist_ok=True)
    state_file = os.path.join(state_dir, "last_version.txt")
    last = open(state_file).read().strip() if os.path.exists(state_file) else None

    print(f"Watching {expand(cfg['master_app'])} for version changes "
          f"(checking every {args.interval}s)...")
    announced = None
    while True:
        cur = get_master_version(expand(cfg["master_app"]))
        if cur and cur != last:
            if cur != announced:
                print(f"Master app version changed: {last!r} -> {cur!r}")
                announced = cur
                # Grace period in case the update is still writing files.
                time.sleep(10)
            # Only record the new version once every profile actually rebuilt,
            # so a profile that was open at the time is retried next poll.
            if build_all(cfg):
                last = cur
                announced = None
                with open(state_file, "w") as f:
                    f.write(cur)
            else:
                print("Some profiles didn't rebuild; retrying on the next poll.")
        sys.stdout.flush()
        time.sleep(args.interval)


# --------------------------------------------------------------------- cli

def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="Add a new profile and build it")
    p_add.add_argument("--id", required=True)
    p_add.add_argument("--name", required=True)
    p_add.add_argument("--icon", default=None, help="Path to a .icns/.png/.jpg icon")
    p_add.set_defaults(func=cmd_add)

    p_rm = sub.add_parser("remove", help="Remove a profile")
    p_rm.add_argument("--id", required=True)
    p_rm.add_argument("--purge", action="store_true", help="Also delete its login data")
    p_rm.set_defaults(func=cmd_remove)

    p_list = sub.add_parser("list", help="List profiles")
    p_list.add_argument("--json", action="store_true",
                        help="Machine-readable output (used by the app)")
    p_list.set_defaults(func=cmd_list)

    p_set = sub.add_parser("set", help="Change a profile's name or icon")
    p_set.add_argument("--id", required=True)
    p_set.add_argument("--name", default=None)
    p_set.add_argument("--icon", default=None,
                       help="New icon path, or \"\" to clear it")
    p_set.add_argument("--rebuild", action="store_true",
                       help="Rebuild immediately so the change takes effect")
    p_set.add_argument("--force", action="store_true",
                       help="With --rebuild, rebuild even if the profile is running")
    p_set.set_defaults(func=cmd_set)

    p_build = sub.add_parser("build", help="(Re)build one or all profiles")
    p_build.add_argument("--id", default=None)
    p_build.add_argument("--all", action="store_true")
    p_build.add_argument("--force", action="store_true",
                         help="Rebuild even if the profile is currently running")
    p_build.set_defaults(func=cmd_build)

    p_launch = sub.add_parser("launch", help="Launch a profile (building it first if needed)")
    p_launch.add_argument("--id", required=True)
    p_launch.set_defaults(func=cmd_launch)

    p_watch = sub.add_parser("watch", help="Watch the master app for updates and auto-rebuild")
    p_watch.add_argument("--interval", type=int, default=30)
    p_watch.set_defaults(func=cmd_watch)

    args = parser.parse_args()
    cfg = load_config()
    args.func(cfg, args)


if __name__ == "__main__":
    if sys.platform != "darwin":
        sys.exit("This tool only runs on macOS.")
    main()
