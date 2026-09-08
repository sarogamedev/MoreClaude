#!/usr/bin/env python3
"""Tests for more_claude.py.

Run from the repo root:

    python3 -m unittest discover -s tests -v

Everything happens against a synthetic Electron-shaped app bundle in a temp
directory — the real /Applications/Claude.app is never read, no profile is
installed, and the user's own config.json is never touched. `codesign`,
`PlistBuddy`, `sips` and `iconutil` are exercised for real, so these are macOS
only and genuinely cover the signing path.
"""

import contextlib
import importlib.util
import io
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_engine():
    spec = importlib.util.spec_from_file_location(
        "more_claude", os.path.join(REPO_ROOT, "more_claude.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mc = load_engine()

EXEC_NAME = "FakeClaude"
BUNDLE_NAME = "Fake Claude"


MAIN_SOURCE = """
#include <stdio.h>
int main(int argc, char **argv) {
    for (int i = 1; i < argc; i++) printf("%s\\n", argv[i]);
    return 0;
}
"""

LIB_SOURCE = "int fake_answer(void) { return 42; }\n"


def clang_available():
    return subprocess.run(["xcrun", "--find", "clang"],
                          capture_output=True).returncode == 0


def compile_binary(source, output, dylib=False):
    """Real Mach-O binaries — codesign refuses to sign anything else, so a
    fixture made of text files wouldn't exercise the signing path at all."""
    src = output + ".c"
    with open(src, "w") as f:
        f.write(source)
    cmd = ["clang", "-o", output, src]
    if dylib:
        cmd.insert(1, "-dynamiclib")
    subprocess.run(cmd, check=True, capture_output=True)
    os.remove(src)


def make_master_app(root):
    """A miniature stand-in for Claude.app: a real executable, a helper app
    named after CFBundleName, and a properly laid out versioned framework —
    enough to exercise the inside-out signing walk for real."""
    app = os.path.join(root, "master", f"{BUNDLE_NAME}.app")
    macos = os.path.join(app, "Contents", "MacOS")
    helper_macos = os.path.join(app, "Contents", "Frameworks",
                                f"{BUNDLE_NAME} Helper.app", "Contents", "MacOS")
    os.makedirs(macos)
    os.makedirs(helper_macos)

    compile_binary(MAIN_SOURCE, os.path.join(macos, EXEC_NAME))
    compile_binary(MAIN_SOURCE, os.path.join(helper_macos, f"{BUNDLE_NAME} Helper"))

    with open(os.path.join(app, "Contents", "Frameworks",
                           f"{BUNDLE_NAME} Helper.app",
                           "Contents", "Info.plist"), "wb") as f:
        plistlib.dump({
            "CFBundleExecutable": f"{BUNDLE_NAME} Helper",
            "CFBundleIdentifier": "com.example.fakeclaude.helper",
            "CFBundlePackageType": "APPL",
        }, f)

    # A versioned framework, laid out the way real ones are: signing the
    # wrapper path rather than Versions/A is a classic codesign trap.
    framework = os.path.join(app, "Contents", "Frameworks", "Fake.framework")
    versions_a = os.path.join(framework, "Versions", "A")
    os.makedirs(os.path.join(versions_a, "Resources"))
    compile_binary(LIB_SOURCE, os.path.join(versions_a, "Fake"), dylib=True)
    with open(os.path.join(versions_a, "Resources", "Info.plist"), "wb") as f:
        plistlib.dump({
            "CFBundleExecutable": "Fake",
            "CFBundleIdentifier": "com.example.fake.framework",
            "CFBundlePackageType": "FMWK",
        }, f)
    os.symlink("A", os.path.join(framework, "Versions", "Current"))
    os.symlink(os.path.join("Versions", "Current", "Fake"),
               os.path.join(framework, "Fake"))
    os.symlink(os.path.join("Versions", "Current", "Resources"),
               os.path.join(framework, "Resources"))

    with open(os.path.join(app, "Contents", "Info.plist"), "wb") as f:
        plistlib.dump({
            "CFBundleExecutable": EXEC_NAME,
            "CFBundleIdentifier": "com.example.fakeclaude",
            "CFBundleName": BUNDLE_NAME,
            "CFBundleIconName": "AppIcon",
            "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": "1.0.0",
        }, f)

    # Sign it as shipped, so the profile build starts from a signed master
    # exactly as it does with the real Claude.app.
    subprocess.run(["codesign", "--force", "--sign", "-", "--deep", app],
                   check=True, capture_output=True)
    return app


class EngineTestCase(unittest.TestCase):
    """Gives each test its own config, profiles dir, data dir and master app."""

    @classmethod
    def setUpClass(cls):
        if not clang_available():
            raise unittest.SkipTest("clang is required to build the test fixture")

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="moreclaude-test-")
        self.addCleanup(shutil.rmtree, self.root, True)

        self.master = make_master_app(self.root)
        self.profiles_dir = os.path.join(self.root, "bundles")
        self.data_dir = os.path.join(self.root, "data")
        self.config_path = os.path.join(self.root, "config.json")

        original_config, original_lsregister = mc.CONFIG_PATH, mc.LSREGISTER
        mc.CONFIG_PATH = self.config_path
        mc.LSREGISTER = "/usr/bin/true"   # don't touch the real Launch Services db

        def restore():
            mc.CONFIG_PATH, mc.LSREGISTER = original_config, original_lsregister
        self.addCleanup(restore)

        self.write_config({
            "master_app": self.master,
            "profiles_dir": self.profiles_dir,
            "data_dir": self.data_dir,
            "state_dir": os.path.join(self.root, "state"),
            "profiles": [],
        })

    # -- helpers ----------------------------------------------------------

    def write_config(self, config):
        with open(self.config_path, "w") as f:
            json.dump(config, f, indent=2)

    def read_config(self):
        with open(self.config_path) as f:
            return json.load(f)

    def engine(self, *argv):
        """Run the CLI the way a user would; return (stdout, exit_code)."""
        buffer = io.StringIO()
        original_argv = sys.argv
        sys.argv = ["more_claude.py", *argv]
        code = 0
        try:
            with contextlib.redirect_stdout(buffer):
                mc.main()
        except SystemExit as exit_error:
            code = exit_error.code or 0
        finally:
            sys.argv = original_argv
        return buffer.getvalue(), code

    def bundle(self, name):
        return os.path.join(self.profiles_dir, f"{name}.app")

    def assert_signature_valid(self, path):
        result = subprocess.run(
            ["codesign", "--verify", "--strict", "--deep", path],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0,
                         f"signature invalid for {path}: {result.stderr}")

    def plist_value(self, bundle, key):
        with open(os.path.join(bundle, "Contents", "Info.plist"), "rb") as f:
            return plistlib.load(f).get(key)


class BuildTests(EngineTestCase):

    def test_build_produces_a_valid_signature(self):
        self.engine("add", "--id", "work", "--name", "Claude Work")
        bundle = self.bundle("Claude Work")
        self.assertTrue(os.path.isdir(bundle))
        self.assert_signature_valid(bundle)

    def test_launcher_pins_the_profiles_data_directory(self):
        self.engine("add", "--id", "work", "--name", "Claude Work")
        bundle = self.bundle("Claude Work")

        launcher_name = self.plist_value(bundle, "CFBundleExecutable")
        self.assertEqual(launcher_name, f"{EXEC_NAME}-profile")

        with open(os.path.join(bundle, "Contents", "MacOS", launcher_name)) as f:
            launcher = f.read()
        self.assertIn(f'--user-data-dir="{os.path.join(self.data_dir, "work")}"',
                      launcher)

        # Running the launcher must reach the real binary with the flag added.
        result = subprocess.run([os.path.join(bundle, "Contents", "MacOS", launcher_name)],
                                capture_output=True, text=True)
        self.assertIn("--user-data-dir=", result.stdout)

    def test_real_binary_keeps_its_name(self):
        """Regression: renaming it to <exec>-bin made Electron fail to find its
        helper processes ("Unable to find helper app")."""
        self.engine("add", "--id", "work", "--name", "Claude Work")
        macos = os.path.join(self.bundle("Claude Work"), "Contents", "MacOS")
        self.assertTrue(os.path.exists(os.path.join(macos, EXEC_NAME)))
        self.assertFalse(os.path.exists(os.path.join(macos, f"{EXEC_NAME}-bin")))

    def test_cfbundlename_is_preserved(self):
        """Regression: Electron builds helper paths from CFBundleName, so it
        must survive; only CFBundleDisplayName carries the profile name."""
        self.engine("add", "--id", "work", "--name", "Claude Work")
        bundle = self.bundle("Claude Work")
        self.assertEqual(self.plist_value(bundle, "CFBundleName"), BUNDLE_NAME)
        self.assertEqual(self.plist_value(bundle, "CFBundleDisplayName"), "Claude Work")
        self.assertEqual(self.plist_value(bundle, "CFBundleIdentifier"),
                         "com.moreclaude.profile.work")
        helper = os.path.join(bundle, "Contents", "Frameworks",
                              f"{self.plist_value(bundle, 'CFBundleName')} Helper.app")
        self.assertTrue(os.path.isdir(helper), "helper app path no longer resolves")

    def test_icon_is_converted_and_installed(self):
        icon = os.path.join(self.root, "icon.png")
        subprocess.run(["sips", "-s", "format", "png", "--resampleHeightWidth", "64", "64",
                        "/System/Library/CoreServices/DefaultDesktop.heic", "--out", icon],
                       capture_output=True)
        if not os.path.exists(icon):
            self.skipTest("no source image available to build a test icon")
        self.engine("add", "--id", "work", "--name", "Claude Work", "--icon", icon)
        bundle = self.bundle("Claude Work")
        self.assertTrue(os.path.exists(
            os.path.join(bundle, "Contents", "Resources", "ProfileIcon.icns")))
        self.assertEqual(self.plist_value(bundle, "CFBundleIconFile"), "ProfileIcon")
        self.assertIsNone(self.plist_value(bundle, "CFBundleIconName"))

    def test_rename_moves_the_bundle_and_removes_the_old_one(self):
        self.engine("add", "--id", "work", "--name", "Claude Work")
        self.engine("set", "--id", "work", "--name", "Claude Renamed", "--rebuild")
        self.assertFalse(os.path.exists(self.bundle("Claude Work")))
        self.assertTrue(os.path.isdir(self.bundle("Claude Renamed")))

    def test_purge_removes_the_login_data(self):
        self.engine("add", "--id", "work", "--name", "Claude Work")
        data = os.path.join(self.data_dir, "work")
        self.assertTrue(os.path.isdir(data))
        self.engine("remove", "--id", "work", "--purge")
        self.assertFalse(os.path.exists(data))

    def test_remove_without_purge_keeps_the_login_data(self):
        self.engine("add", "--id", "work", "--name", "Claude Work")
        self.engine("remove", "--id", "work")
        self.assertTrue(os.path.isdir(os.path.join(self.data_dir, "work")))


class AtomicRebuildTests(EngineTestCase):

    def test_failed_rebuild_leaves_the_working_profile_intact(self):
        """A rebuild copies and re-signs gigabytes. If it dies partway, the
        profile that was already installed must still be there and valid."""
        self.engine("add", "--id", "work", "--name", "Claude Work")
        bundle = self.bundle("Claude Work")
        self.assert_signature_valid(bundle)
        before = sorted(os.listdir(os.path.join(bundle, "Contents", "MacOS")))

        original = mc.sign_bundle
        mc.sign_bundle = lambda *a, **k: (_ for _ in ()).throw(
            mc.BuildError("simulated failure partway through the build"))
        self.addCleanup(lambda: setattr(mc, "sign_bundle", original))

        output, _ = self.engine("build", "--id", "work")
        mc.sign_bundle = original

        self.assertIn("simulated failure", output)
        self.assertTrue(os.path.isdir(bundle), "previous profile was destroyed")
        self.assertEqual(sorted(os.listdir(os.path.join(bundle, "Contents", "MacOS"))),
                         before)
        self.assert_signature_valid(bundle)

    def test_no_staging_directories_are_left_behind(self):
        self.engine("add", "--id", "work", "--name", "Claude Work")
        self.engine("build", "--id", "work")
        leftovers = [n for n in os.listdir(self.profiles_dir)
                     if n.startswith(mc.STAGING_PREFIX) or n.startswith(mc.REPLACED_PREFIX)]
        self.assertEqual(leftovers, [])

    def test_leftovers_from_an_interrupted_run_are_swept(self):
        self.engine("add", "--id", "work", "--name", "Claude Work")
        stale = os.path.join(self.profiles_dir, mc.STAGING_PREFIX + "Claude Work.app-999")
        os.makedirs(stale)
        self.engine("build", "--id", "work")
        self.assertFalse(os.path.exists(stale))

    def test_running_profile_is_not_replaced(self):
        self.engine("add", "--id", "work", "--name", "Claude Work")
        bundle = self.bundle("Claude Work")

        original = mc.is_app_running
        mc.is_app_running = lambda path: True
        self.addCleanup(lambda: setattr(mc, "is_app_running", original))

        output, _ = self.engine("build", "--id", "work")
        self.assertIn("is running", output)
        self.assertTrue(os.path.isdir(bundle))

        output, _ = self.engine("build", "--id", "work", "--force")
        mc.is_app_running = original
        self.assertIn("Done", output)
        self.assert_signature_valid(bundle)


class ValidationTests(EngineTestCase):

    def test_rejects_an_id_carrying_shell_metacharacters(self):
        """The id is interpolated into the generated launcher script."""
        marker = os.path.join(self.root, "PWNED")
        output, _ = self.engine(
            "add", "--id", f'work";touch {marker};"', "--name", "Work")
        self.assertIn("invalid profile id", output)
        self.assertFalse(os.path.exists(marker))
        self.assertEqual(self.read_config()["profiles"], [])

    def test_rejects_a_name_containing_path_separators(self):
        output, _ = self.engine("add", "--id", "ok", "--name", "../escape")
        self.assertIn("invalid profile name", output)

    def test_rejects_an_absolute_name(self):
        output, _ = self.engine("add", "--id", "ok", "--name", "/tmp/escape")
        self.assertIn("invalid profile name", output)

    def test_rejects_a_traversing_id(self):
        output, _ = self.engine("add", "--id", "../../escape", "--name", "Escape")
        self.assertIn("invalid profile id", output)

    def test_rejects_a_rename_that_traverses(self):
        self.engine("add", "--id", "work", "--name", "Claude Work")
        output, _ = self.engine("set", "--id", "work", "--name", "../escape", "--rebuild")
        self.assertIn("invalid profile name", output)
        self.assertTrue(os.path.isdir(self.bundle("Claude Work")))

    def test_hand_edited_config_with_a_bad_id_is_rejected(self):
        config = self.read_config()
        config["profiles"] = [{"id": 'x";rm -rf /;"', "name": "Bad"}]
        self.write_config(config)
        output, code = self.engine("list")
        self.assertNotEqual(code, 0)

    def test_bundle_path_outside_the_profiles_dir_is_ignored(self):
        """config.json is hand-editable; a bundle_path pointing elsewhere must
        never be handed to rmtree."""
        self.engine("add", "--id", "work", "--name", "Claude Work")
        victim = os.path.join(self.root, "victim")
        os.makedirs(victim)
        with open(os.path.join(victim, "keep.txt"), "w") as f:
            f.write("precious")

        config = self.read_config()
        config["profiles"][0]["bundle_path"] = victim
        self.write_config(config)

        output, _ = self.engine("build", "--id", "work")
        self.assertIn("ignoring bundle_path", output)
        self.assertTrue(os.path.exists(os.path.join(victim, "keep.txt")))

    def test_ensure_inside_rejects_escaping_paths(self):
        with self.assertRaises(mc.BuildError):
            mc.ensure_inside(os.path.join(self.profiles_dir, "..", "elsewhere"),
                             self.profiles_dir, "bundle")

    def test_ere_escape_neutralises_regex_metacharacters(self):
        """is_app_running feeds the bundle path to `pgrep -f`, which takes an
        extended regular expression."""
        escaped = mc.ere_escape("/Apps/Claude (v2) [beta].app/Contents/MacOS/")
        self.assertEqual(escaped,
                         r"/Apps/Claude \(v2\) \[beta\]\.app/Contents/MacOS/")

    def test_running_check_survives_a_name_with_metacharacters(self):
        self.engine("add", "--id", "rx", "--name", "Claude (v2) [beta]")
        self.assertFalse(mc.is_app_running(self.bundle("Claude (v2) [beta]")))


class ConfigTests(EngineTestCase):

    def test_config_is_never_visible_half_written(self):
        """The watcher rebuilds in the background while the app or the CLI
        writes config.json. Writing in place truncates the file first, so a
        concurrent reader sees an empty config and crashes — which is exactly
        what happened in practice. Slow the write down and read the file from
        another thread while it is in flight."""
        self.engine("add", "--id", "work", "--name", "Claude Work")
        config = self.read_config()

        observed = []

        def reader():
            time.sleep(0.15)          # land inside the write
            try:
                with open(self.config_path) as f:
                    observed.append(f.read())
            except FileNotFoundError:
                observed.append("")

        original_dump = json.dump

        def slow_dump(obj, fp, **kwargs):
            time.sleep(0.4)
            return original_dump(obj, fp, **kwargs)

        json.dump = slow_dump
        self.addCleanup(lambda: setattr(json, "dump", original_dump))
        watcher = threading.Thread(target=reader)
        watcher.start()
        try:
            mc.save_config(config)
        finally:
            json.dump = original_dump
            watcher.join()

        self.assertEqual(len(observed), 1)
        self.assertTrue(observed[0].strip(),
                        "a reader saw config.json empty mid-write")
        json.loads(observed[0])       # and it was complete, parseable JSON

    def test_saving_leaves_no_temp_files_behind(self):
        self.engine("add", "--id", "work", "--name", "Claude Work")
        self.engine("set", "--id", "work", "--name", "Claude Renamed")
        leftovers = [n for n in os.listdir(os.path.dirname(self.config_path))
                     if n.startswith(".config-")]
        self.assertEqual(leftovers, [])

    def test_corrupt_config_gives_a_clear_error_not_a_traceback(self):
        with open(self.config_path, "w") as f:
            f.write("{ not json")
        output, code = self.engine("list")
        self.assertNotEqual(code, 0)
        self.assertIn("not valid JSON", str(code))


class ListingTests(EngineTestCase):

    def test_json_listing_shape(self):
        self.engine("add", "--id", "work", "--name", "Claude Work")
        output, _ = self.engine("list", "--json")
        data = json.loads(output)
        self.assertTrue(data["master_installed"])
        self.assertEqual(data["master_version"], "1.0.0")
        self.assertEqual(len(data["profiles"]), 1)

        profile = data["profiles"][0]
        for key in ("id", "name", "icon", "bundle_path", "data_path", "status"):
            self.assertIn(key, profile)
        self.assertEqual(profile["status"], "built")

    def test_status_reports_not_built_when_the_bundle_is_gone(self):
        self.engine("add", "--id", "work", "--name", "Claude Work")
        shutil.rmtree(self.bundle("Claude Work"))
        output, _ = self.engine("list", "--json")
        self.assertEqual(json.loads(output)["profiles"][0]["status"], "not_built")

    def test_duplicate_ids_are_refused(self):
        self.engine("add", "--id", "work", "--name", "Claude Work")
        output, _ = self.engine("add", "--id", "work", "--name", "Another")
        self.assertIn("already exists", output)
        self.assertEqual(len(self.read_config()["profiles"]), 1)


if __name__ == "__main__":
    if sys.platform != "darwin":
        sys.exit("These tests only run on macOS.")
    unittest.main(verbosity=2)
