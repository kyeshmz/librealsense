# License: Apache 2.0. See LICENSE file in root directory.
# Copyright(c) 2026 RealSense, Inc. All Rights Reserved.

from pathlib import Path
import re
import plistlib
import stat
import struct
import subprocess
import sys
import tempfile
import textwrap
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = (ROOT / ".github/workflows/macos-viewer.yml").read_text(encoding="utf-8")
RELEASE = (ROOT / ".github/workflows/macos-viewer-release.yml").read_text(encoding="utf-8")
GUIDE = (ROOT / "doc/installation_osx.md").read_text(encoding="utf-8")


class MacOSViewerReleaseWorkflowTests(unittest.TestCase):
    """Workflow source contracts; native app checks run on macOS Actions."""

    def test_package_workflow_remains_directly_runnable_and_reusable(self):
        for trigger in ("push:", "pull_request:", "workflow_dispatch:", "workflow_call:"):
            self.assertIn(trigger, PACKAGE)
        self.assertIn("contents: read", PACKAGE)
        self.assertIn("actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a", PACKAGE)
        self.assertIn('name: realsense-viewer-macos-${{ matrix.architecture }}', PACKAGE)

    def test_release_is_tagged_and_reuses_only_the_validated_package_workflow(self):
        self.assertIn("tags: ['macos-viewer-v*']", RELEASE)
        self.assertIn("uses: ./.github/workflows/macos-viewer.yml", RELEASE)
        self.assertIn("needs: package", RELEASE)
        self.assertIn('test "$GITHUB_REPOSITORY" = "kyeshmz/librealsense"', RELEASE)
        self.assertIn('test "$(git rev-parse HEAD)" = "$SOURCE_SHA"', RELEASE)

    def test_permissions_are_read_only_except_for_the_publish_job(self):
        self.assertRegex(RELEASE, r"(?m)^permissions:\n  contents: read$")
        self.assertRegex(RELEASE, r"(?ms)^  package:.*?permissions:\n      contents: read")
        self.assertRegex(RELEASE, r"(?ms)^  publish:.*?permissions:\n      contents: write")
        self.assertEqual(RELEASE.count("contents: write"), 1)

    def test_only_exact_current_run_architecture_artifacts_are_downloaded(self):
        for architecture in ("arm64", "x86_64"):
            self.assertIn("name: realsense-viewer-macos-" + architecture, RELEASE)
            self.assertIn("realsense-viewer-" + architecture + ".zip", RELEASE)
            self.assertIn("0x0100000C" if architecture == "arm64" else "0x01000007", RELEASE)
        self.assertEqual(RELEASE.count("actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333"), 2)
        self.assertNotIn("run-id:", RELEASE)
        self.assertIn("bundle_zip.testzip()", RELEASE)
        self.assertIn("path.is_absolute() or \"..\" in path.parts", RELEASE)
        self.assertIn("stat.S_ISLNK(mode)", RELEASE)
        self.assertIn('prefix + "Frameworks/"', RELEASE)
        self.assertIn("Resources/Presets/", RELEASE)
        self.assertIn("CFBundleIdentifier", RELEASE)
        self.assertIn("CFBundleShortVersionString", RELEASE)
        self.assertIn('plist.get("CFBundleVersion") != version', RELEASE)
        self.assertIn("executable_mode & 0o111", RELEASE)

    def test_publisher_creates_non_latest_prerelease_with_provenance_and_hashes(self):
        self.assertIn("gh release create \"$SOURCE_TAG\"", RELEASE)
        for flag in ("--verify-tag", "--prerelease", "--latest=false", "--notes-file"):
            self.assertIn(flag, RELEASE)
        for asset in ("realsense-viewer-arm64.zip", "realsense-viewer-x86_64.zip", "SHA256SUMS", "SOURCE.txt"):
            self.assertIn(asset, RELEASE)
        self.assertIn("shasum -a 256 -c SHA256SUMS", RELEASE)
        self.assertNotIn("--clobber", RELEASE)
        self.assertNotRegex(RELEASE, re.compile(r"(?m)^\s*workflow_dispatch:"))

    def test_package_checks_architecture_and_docs_describe_release_limits(self):
        self.assertIn('lipo -archs "${{ steps.package.outputs.app }}/Contents/MacOS/realsense-viewer"', PACKAGE)
        self.assertIn('test "$architecture" = "${{ matrix.architecture }}"', PACKAGE)
        self.assertIn("macOS Viewer prerelease", GUIDE)
        self.assertIn("not developer-signed or notarized", GUIDE)
        self.assertIn("arm64", GUIDE)
        self.assertIn("x86_64", GUIDE)
        self.assertIn("USB", GUIDE)

    @staticmethod
    def archive_validator_source():
        match = re.search(
            r"(?m)^ {10}python3 - \"\$release_dir\" <<'PY'\n(.*?)^ {10}PY$",
            RELEASE,
            re.DOTALL,
        )
        if not match:
            raise AssertionError("release archive validator heredoc was not found")
        return textwrap.dedent(match.group(1))

    @staticmethod
    def write_archive(path, architecture, extra_entries=()):
        cpu_types = {"arm64": 0x0100000C, "x86_64": 0x01000007}
        plist = plistlib.dumps({
            "CFBundleExecutable": "realsense-viewer",
            "CFBundleIdentifier": "com.intel.realsense.viewer",
            "CFBundleDisplayName": "RealSense Viewer",
            "CFBundleName": "RealSense Viewer",
            "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": "2.59.0",
            "CFBundleVersion": "2.59.0",
        })
        executable = struct.pack("<I", 0xFEEDFACF) + struct.pack("<i", cpu_types[architecture]) + bytes(28)
        entries = [
            ("realsense-viewer.app/Contents/Info.plist", plist, stat.S_IFREG | 0o644),
            ("realsense-viewer.app/Contents/MacOS/realsense-viewer", executable, stat.S_IFREG | 0o755),
            ("realsense-viewer.app/Contents/Frameworks/libsample.dylib", b"Mach-O fixture", stat.S_IFREG | 0o644),
            ("realsense-viewer.app/Contents/Resources/LICENSE", b"license", stat.S_IFREG | 0o644),
            ("realsense-viewer.app/Contents/Resources/NOTICE.md", b"notice", stat.S_IFREG | 0o644),
            ("realsense-viewer.app/Contents/Resources/Presets/sample.preset", b"preset", stat.S_IFREG | 0o644),
        ] + list(extra_entries)
        with zipfile.ZipFile(path, "w") as archive:
            for name, contents, mode in entries:
                entry = zipfile.ZipInfo(name)
                entry.create_system = 3
                entry.external_attr = mode << 16
                archive.writestr(entry, contents)

    def run_archive_validator(self, directory):
        return subprocess.run(
            [sys.executable, "-c", self.archive_validator_source(), str(directory)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def test_archive_validator_accepts_both_expected_thin_macho_architectures(self):
        with tempfile.TemporaryDirectory(prefix="viewer release archives ") as temp:
            directory = Path(temp)
            for architecture in ("arm64", "x86_64"):
                self.write_archive(directory / ("realsense-viewer-" + architecture + ".zip"), architecture)
            result = self.run_archive_validator(directory)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("validated realsense-viewer-arm64.zip", result.stdout)
            self.assertIn("validated realsense-viewer-x86_64.zip", result.stdout)

    def test_archive_validator_rejects_wrong_architecture_and_escaping_paths(self):
        with tempfile.TemporaryDirectory(prefix="viewer release archives ") as temp:
            directory = Path(temp)
            self.write_archive(directory / "realsense-viewer-arm64.zip", "x86_64")
            self.write_archive(directory / "realsense-viewer-x86_64.zip", "x86_64")
            result = self.run_archive_validator(directory)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("wrong Mach-O architecture", result.stderr)

            self.write_archive(
                directory / "realsense-viewer-arm64.zip",
                "arm64",
                [("../outside", b"escape", stat.S_IFREG | 0o644)],
            )
            result = self.run_archive_validator(directory)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unsafe path", result.stderr)

    def test_archive_validator_rejects_escaping_symlinks(self):
        with tempfile.TemporaryDirectory(prefix="viewer release archives ") as temp:
            directory = Path(temp)
            link_name = "realsense-viewer.app/Contents/Frameworks/libescape.dylib"
            self.write_archive(
                directory / "realsense-viewer-arm64.zip",
                "arm64",
                [(link_name, b"../../../../outside.dylib", stat.S_IFLNK | 0o777)],
            )
            self.write_archive(directory / "realsense-viewer-x86_64.zip", "x86_64")
            result = self.run_archive_validator(directory)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("escaping symlink", result.stderr)


if __name__ == "__main__":
    unittest.main()
