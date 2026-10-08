# License: Apache 2.0. See LICENSE file in root directory.
# Copyright(c) 2026 RealSense, Inc. All Rights Reserved.

from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[2]


class DarwinCaptureErrorTests(unittest.TestCase):
    def test_cli_build_includes_enumerator_used_by_troubleshooting(self):
        instructions = (ROOT / "doc/installation_osx.md").read_text(encoding="utf-8")
        self.assertIn(
            "./build-viewer-cli/Release/rs-enumerate-devices --debug", instructions
        )
        build_command = re.search(
            r"(?m)^cmake --build build-viewer-cli --target ([^\n]+)$", instructions
        )
        self.assertIsNotNone(build_command, "CLI build command must be documented")
        targets = build_command.group(1).split()
        self.assertIn("realsense-viewer", targets)
        self.assertIn("rs-enumerate-devices", targets)

    def test_elevated_viewer_guidance_is_conditional_and_unqualified(self):
        instructions = (ROOT / "doc/installation_osx.md").read_text(encoding="utf-8")
        self.assertIn("sudo ./build-viewer-cli/Release/realsense-viewer", instructions)
        self.assertIn("After successful authorized enumeration", instructions)
        self.assertIn(
            "on some systems the root process may not connect to the user's WindowServer",
            instructions,
        )
        self.assertIn("Do not use `sudo open`", instructions)
        self.assertIn("enable the Stereo Module stream", instructions)
        self.assertIn(
            "Normal-user USB support and Finder launch with live cameras remain unqualified.",
            instructions,
        )
        self.assertNotIn("Do not run the GUI with `sudo`", instructions)

    def test_production_formatter_reports_status_operation_interface_and_access_guidance(self):
        compiler = Path("/usr/bin/clang++")
        if not compiler.is_file():
            compiler = shutil.which("clang++") or shutil.which("c++") or shutil.which("g++")
        if not compiler:
            self.skipTest("No C++ compiler found; production formatter was not executed")

        stub_header = textwrap.dedent(
            """\
            #pragma once
            #define LIBUSB_ERROR_ACCESS -3
            #define LIBUSB_ERROR_NO_DEVICE -4
            #define LIBUSB_ERROR_BUSY -6
            #define LIBUSB_ERROR_OTHER -99
            static inline const char* libusb_error_name(int status)
            {
                switch(status)
                {
                case LIBUSB_ERROR_ACCESS: return "LIBUSB_ERROR_ACCESS";
                case LIBUSB_ERROR_NO_DEVICE: return "LIBUSB_ERROR_NO_DEVICE";
                case LIBUSB_ERROR_BUSY: return "LIBUSB_ERROR_BUSY";
                case LIBUSB_ERROR_OTHER: return "LIBUSB_ERROR_OTHER";
                default: return "LIBUSB_ERROR_UNKNOWN";
                }
            }
            """
        )
        harness = textwrap.dedent(
            """\
            #include "darwin-capture-error.h"
            #include <string>

            int main()
            {
                using librealsense::platform::detail::format_darwin_capture_error;
                if(format_darwin_capture_error("libusb_open", LIBUSB_ERROR_ACCESS, 5)
                    != "libusb_open failed for interface 5: LIBUSB_ERROR_ACCESS (-3). On macOS, capture may require local authorization in an active desktop session; "
                       "elevated capture may not resolve every restriction.")
                    return 1;
                if(format_darwin_capture_error("libusb_set_auto_detach_kernel_driver(false)", LIBUSB_ERROR_BUSY, 10)
                    != "libusb_set_auto_detach_kernel_driver(false) failed for interface 10: LIBUSB_ERROR_BUSY (-6)")
                    return 2;
                if(format_darwin_capture_error("libusb_detach_kernel_driver", LIBUSB_ERROR_NO_DEVICE, 11)
                    != "libusb_detach_kernel_driver failed for interface 11: LIBUSB_ERROR_NO_DEVICE (-4)")
                    return 3;
                if(format_darwin_capture_error("libusb_detach_kernel_driver", LIBUSB_ERROR_OTHER, 12)
                    != "libusb_detach_kernel_driver failed for interface 12: LIBUSB_ERROR_OTHER (-99)")
                    return 4;
                return 0;
            }
            """
        )

        build_root = ROOT / "build-macos-detection"
        build_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="darwin-capture-errors-", dir=build_root) as temp:
            temp_dir = Path(temp)
            fake_include = temp_dir / "include"
            fake_include.mkdir()
            (fake_include / "libusb.h").write_text(stub_header, encoding="utf-8")
            source = temp_dir / "formatter-test.cpp"
            source.write_text(harness, encoding="utf-8")
            executable = temp_dir / "formatter-test"

            compile_result = subprocess.run(
                [
                    str(compiler),
                    "-std=c++11",
                    "-I",
                    str(fake_include),
                    "-I",
                    str(ROOT / "src/libusb"),
                    str(source),
                    "-o",
                    str(executable),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            run_result = subprocess.run(
                [str(executable)], check=False, capture_output=True, text=True
            )
            self.assertEqual(run_result.returncode, 0, run_result.stderr)

    def test_enumerator_preserves_exception_and_streams_numeric_index(self):
        source = (ROOT / "src/libusb/enumerator-libusb.cpp").read_text(encoding="utf-8")
        match = re.search(
            r"catch\s*\(const std::exception& e\)\s*\{([^{}]*)\}", source, re.DOTALL
        )
        self.assertIsNotNone(match, "device-construction exception must be caught by const reference")
        catch_body = match.group(1)
        self.assertIn('"failed to create usb device at index: "', catch_body)
        self.assertRegex(catch_body, r"static_cast<unsigned>\(idx\)")
        self.assertIn("e.what()", catch_body)
        self.assertNotIn("%d", catch_body)


if __name__ == "__main__":
    unittest.main()
