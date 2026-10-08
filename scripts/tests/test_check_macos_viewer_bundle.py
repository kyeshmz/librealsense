# License: Apache 2.0. See LICENSE file in root directory.
# Copyright(c) 2026 RealSense, Inc. All Rights Reserved.

import importlib.util
import os
import plistlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "check-macos-viewer-bundle.py"
SPEC = importlib.util.spec_from_file_location("check_macos_viewer_bundle", SCRIPT)
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


class MockInspector:
    def __init__(self):
        self.images = {}
        self.system_images = set()

    def add_image(self, path, dependencies=(), rpaths=(), arch="arm64", filetype=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"mock Mach-O image")
        self.images.setdefault(path.resolve(), {})[arch] = {
            "dependencies": list(dependencies),
            "rpaths": list(rpaths),
            "filetype": filetype or ("EXECUTE" if path.name == "realsense-viewer" else "DYLIB"),
        }

    def is_macho(self, path):
        return path.resolve() in self.images

    def slices(self, path):
        return self.images[path.resolve()]

    def system_image_available(self, path):
        return path in self.system_images


class CheckMacOSViewerBundleTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory(prefix="viewer package with spaces ")
        self.temp = Path(self.temporary_directory.name)
        self.bundle = self.temp / "realsense-viewer.app"
        self.contents = self.bundle / "Contents"
        self.executable = self.contents / "MacOS" / "realsense-viewer"
        self.resources = self.contents / "Resources"
        self.frameworks = self.contents / "Frameworks"
        self.resources.mkdir(parents=True)
        self.frameworks.mkdir(parents=True)
        (self.resources / "LICENSE").write_text("license\n", encoding="utf-8")
        (self.resources / "NOTICE.md").write_text("notice\n", encoding="utf-8")
        presets = self.resources / "Presets"
        presets.mkdir()
        (presets / "sample.preset").write_text("preset\n", encoding="utf-8")
        self._write_plist()
        self.inspector = MockInspector()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _write_plist(self, overrides=None):
        values = {
            "CFBundleExecutable": "realsense-viewer",
            "CFBundleIdentifier": "com.intel.realsense.viewer",
            "CFBundleDisplayName": "RealSense Viewer",
            "CFBundleName": "RealSense Viewer",
            "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": "2.58.4",
            "CFBundleVersion": "2.58.4",
        }
        values.update(overrides or {})
        (self.contents / "Info.plist").write_bytes(plistlib.dumps(values))

    def _add_executable(self, dependencies=(), rpaths=()):
        self.inspector.add_image(self.executable, dependencies, rpaths)

    def test_missing_bundle_and_metadata_are_rejected(self):
        missing = self.temp / "missing.app"
        with self.assertRaisesRegex(checker.BundleCheckError, r"not a macOS \.app"):
            checker.validate_bundle(missing, inspector=self.inspector, platform="linux")

        self._write_plist({"CFBundleDisplayName": "Wrong Name"})
        self._add_executable()
        with self.assertRaisesRegex(checker.BundleCheckError, "invalid bundle metadata"):
            checker.validate_bundle(self.bundle, inspector=self.inspector, platform="linux")

    def test_cli_inspection_fails_honestly_off_darwin(self):
        with self.assertRaisesRegex(checker.BundleCheckError, "requires macOS and otool"):
            checker.validate_bundle(self.bundle, platform="linux")

    def test_otool_inspector_parses_mocked_tool_output(self):
        outputs = {
            "file -b": "Mach-O 64-bit executable arm64\n",
            "lipo -archs": "arm64 x86_64\n",
            "otool -arch arm64 -hv -l": (
                "Mach header\n"
                "magic cputype cpusubtype caps filetype ncmds sizeofcmds flags\n"
                "MH_MAGIC_64 ARM64 ALL 0x00 DYLIB 3 200 NOUNDEFS\n"
                "Load command 0\n"
                "          cmd LC_ID_DYLIB\n"
                "      cmdsize 64\n"
                "         name @rpath/lib self.dylib (offset 24)\n"
                "Load command 1\n"
                "          cmd LC_LOAD_DYLIB\n"
                "      cmdsize 64\n"
                "         name @rpath/lib sample.dylib (offset 24)\n"
                "Load command 2\n"
                "          cmd LC_RPATH\n"
                "      cmdsize 40\n"
                "         path @loader_path/../Frameworks (offset 12)\n"
            ),
            "otool -arch x86_64 -hv -l": (
                "Mach header\n"
                "magic cputype cpusubtype caps filetype ncmds sizeofcmds flags\n"
                "MH_MAGIC_64 X86_64 ALL 0x00 DYLIB 1 100 NOUNDEFS\n"
                "Load command 0\n"
                "          cmd LC_REEXPORT_DYLIB\n"
                "      cmdsize 64\n"
                "         name /opt/homebrew/lib/external.dylib (offset 24)\n"
            ),
        }

        def run(arguments, **kwargs):
            key = " ".join(arguments[:-1])
            return subprocess.CompletedProcess(arguments, 0, outputs[key], "")

        inspector = checker.OtoolInspector()
        image = Path("/tmp/viewer app/realsense-viewer")
        with mock.patch.object(checker.subprocess, "run", side_effect=run):
            self.assertTrue(inspector.is_macho(image))
            slices = inspector.slices(image)
            self.assertEqual(slices["arm64"]["dependencies"], ["@rpath/lib sample.dylib"])
            self.assertEqual(slices["arm64"]["rpaths"], ["@loader_path/../Frameworks"])
            self.assertEqual(slices["arm64"]["filetype"], "DYLIB")
            self.assertEqual(slices["x86_64"]["dependencies"], ["/opt/homebrew/lib/external.dylib"])
            self.assertEqual(slices["x86_64"]["rpaths"], [])

    def test_system_only_dependencies_are_allowed(self):
        self.inspector.system_images.add(Path("/usr/lib/libSystem.B.dylib"))
        self._add_executable(["/usr/lib/libSystem.B.dylib"])
        checker.validate_bundle(self.bundle, inspector=self.inspector, platform="linux")

    def test_bundled_dependencies_resolve_with_spaces_and_image_context(self):
        self._add_executable(
            ["@rpath/lib sample.dylib"],
            ["@executable_path/../Frameworks"],
        )
        library = self.frameworks / "lib sample.dylib"
        self.inspector.add_image(
            library,
            [
                "@loader_path/libchild.dylib",
                "@executable_path/../Frameworks/libchild.dylib",
            ],
            ["@loader_path"],
        )
        self.inspector.add_image(
            self.frameworks / "libchild.dylib",
            ["/System/Library/Frameworks/Foundation.framework/Versions/A/Foundation"],
        )
        self.inspector.system_images.add(Path("/System/Library/Frameworks/Foundation.framework/Versions/A/Foundation"))
        checker.validate_bundle(self.bundle, inspector=self.inspector, platform="linux")

    def test_missing_system_runpath_candidate_continues_to_bundle(self):
        self._add_executable(["@rpath/libsample.dylib"], ["/usr/lib/swift", "@executable_path/../Frameworks"])
        self.inspector.add_image(self.frameworks / "libsample.dylib")
        checker.validate_bundle(self.bundle, inspector=self.inspector)
        # Prove that the later bundle image, not the missing system image,
        # was actually selected and inspected.
        self.inspector.add_image(self.frameworks / "libsample.dylib", ["/opt/homebrew/lib/external.dylib"])
        with self.assertRaisesRegex(checker.BundleCheckError, "external non-system dependency"):
            checker.validate_bundle(self.bundle, inspector=self.inspector)

    def test_missing_system_runpath_candidate_everywhere_is_rejected(self):
        self._add_executable(
            ["@rpath/libDEFINITELY_MISSING_viewer_recheck_289532.dylib"],
            ["/usr/lib/swift", "@executable_path/../Frameworks"],
        )
        with self.assertRaisesRegex(checker.BundleCheckError, "unresolved @rpath dependency"):
            checker.validate_bundle(self.bundle, inspector=self.inspector)

    def test_missing_direct_system_dependency_is_rejected(self):
        self._add_executable(["/usr/lib/libDEFINITELY_MISSING_viewer_recheck_289532.dylib"])
        with self.assertRaisesRegex(checker.BundleCheckError, "unresolved system dependency"):
            checker.validate_bundle(self.bundle, inspector=self.inspector)

    def test_cache_only_system_candidate_is_allowed(self):
        self.inspector.system_images.add(Path("/usr/lib/libSystem.B.dylib"))
        self._add_executable(["@rpath/libSystem.B.dylib"], ["/usr/lib"])
        checker.validate_bundle(self.bundle, inspector=self.inspector)

    def test_native_cache_query_uses_only_the_loaded_runtime(self):
        inspector = checker.OtoolInspector()
        runtime = mock.Mock()
        query = runtime._dyld_shared_cache_contains_path
        query.side_effect = lambda path: path == b"/usr/lib/libSystem.B.dylib"
        with mock.patch.object(checker.ctypes, "CDLL", return_value=runtime) as cdll:
            with mock.patch.object(Path, "is_file", return_value=False):
                self.assertTrue(inspector.system_image_available(Path("/usr/lib/libSystem.B.dylib")))
                self.assertFalse(inspector.system_image_available(Path("/usr/lib/libmissing.dylib")))
            cdll.assert_called_once_with(None)
            self.assertEqual(query.argtypes, [checker.ctypes.c_char_p])
            self.assertIs(query.restype, checker.ctypes.c_bool)

    def test_system_file_on_disk_does_not_need_a_cache_query(self):
        inspector = checker.OtoolInspector()
        with mock.patch.object(checker.ctypes, "CDLL") as cdll:
            with mock.patch.object(Path, "is_file", return_value=True):
                self.assertTrue(inspector.system_image_available(Path("/usr/lib/dyld")))
            cdll.assert_not_called()

    def test_unavailable_cache_query_fails_closed(self):
        inspector = checker.OtoolInspector()
        with mock.patch.object(checker.ctypes, "CDLL", return_value=object()):
            with mock.patch.object(Path, "is_file", return_value=False):
                with self.assertRaisesRegex(checker.BundleCheckError, "cannot query the active dyld shared cache"):
                    inspector.system_image_available(Path("/usr/lib/libSystem.B.dylib"))

    def test_unresolved_rpath_dependency_is_rejected(self):
        self._add_executable(
            ["@rpath/libmissing.dylib"],
            ["@executable_path/../Frameworks"],
        )
        with self.assertRaisesRegex(checker.BundleCheckError, "unresolved @rpath dependency"):
            checker.validate_bundle(self.bundle, inspector=self.inspector, platform="linux")

    def test_external_loader_path_is_rejected_even_without_a_dependency(self):
        self._add_executable(rpaths=["/opt/homebrew/lib"])
        with self.assertRaisesRegex(checker.BundleCheckError, "external non-system runpath"):
            checker.validate_bundle(self.bundle, inspector=self.inspector, platform="linux")

    def test_absolute_homebrew_and_build_dependencies_are_rejected(self):
        self._add_executable([
            "/opt/homebrew/opt/example/lib/libexample.dylib",
            "/tmp/build tree/lib/libbuild-only.dylib",
        ])
        with self.assertRaisesRegex(checker.BundleCheckError, "external non-system dependency") as error:
            checker.validate_bundle(self.bundle, inspector=self.inspector, platform="linux")
        self.assertIn("/opt/homebrew/", str(error.exception))
        self.assertIn("/tmp/build tree/", str(error.exception))

    def test_external_symlink_is_rejected(self):
        outside = self.temp / "outside.dylib"
        outside.write_bytes(b"outside")
        link = self.frameworks / "libescape.dylib"
        os.symlink(str(outside), str(link))
        self._add_executable()
        with self.assertRaisesRegex(checker.BundleCheckError, "symlink escapes the app bundle"):
            checker.validate_bundle(self.bundle, inspector=self.inspector, platform="linux")

    def test_malformed_load_metadata_fails_closed(self):
        with self.assertRaisesRegex(checker.BundleCheckError, "incomplete or unsupported"):
            checker.OtoolInspector._parse_load_commands("", self.executable, "arm64")
        truncated = ("magic cputype cpusubtype caps filetype ncmds sizeofcmds flags\n"
                     "MH_MAGIC_64 ARM64 ALL 0x00 EXECUTE 2 200 NOUNDEFS\n")
        with self.assertRaisesRegex(checker.BundleCheckError, "incomplete or unsupported"):
            checker.OtoolInspector._parse_load_commands(truncated, self.executable, "arm64")

    def test_dynamic_linker_is_checked_and_embedded_environment_rejected(self):
        header = ("magic cputype cpusubtype caps filetype ncmds sizeofcmds flags\n"
                  "MH_MAGIC_64 ARM64 ALL 0x00 EXECUTE 1 100 NOUNDEFS\n")
        command = "Load command 0\n cmd LC_LOAD_DYLINKER\n cmdsize 40\n name /tmp/external-dyld (offset 12)\n"
        metadata = checker.OtoolInspector._parse_load_commands(header + command, self.executable, "arm64")
        self.assertEqual(metadata["dependencies"], ["/tmp/external-dyld"])
        self._add_executable(metadata["dependencies"])
        with self.assertRaisesRegex(checker.BundleCheckError, "external non-system dependency"):
            checker.validate_bundle(self.bundle, inspector=self.inspector)
        with self.assertRaisesRegex(checker.BundleCheckError, "embedded dyld environment"):
            checker.OtoolInspector._parse_load_commands(
                header + command.replace("LC_LOAD_DYLINKER", "LC_DYLD_ENVIRONMENT"), self.executable, "arm64")

    def test_all_dylib_load_commands_are_dependencies_but_id_is_not(self):
        header = ("magic cputype cpusubtype caps filetype ncmds sizeofcmds flags\n"
                  "MH_MAGIC_64 ARM64 ALL 0x00 DYLIB 6 600 NOUNDEFS\n")
        commands = ["LC_ID_DYLIB"] + sorted(checker._DYLIB_LOADS)
        output = header + "".join(
            "Load command {}\n cmd {}\n cmdsize 64\n name @rpath/lib{}.dylib (offset 24)\n".format(i, cmd, i)
            for i, cmd in enumerate(commands)
        )
        metadata = checker.OtoolInspector._parse_load_commands(output, self.executable, "arm64")
        self.assertEqual(metadata["dependencies"], ["@rpath/lib{}.dylib".format(i) for i in range(1, 6)])

    def test_slice_cannot_borrow_another_slices_runpath(self):
        dependency = "@rpath/libsample.dylib"
        self._add_executable([dependency], ["@executable_path/../Frameworks"])
        self.inspector.add_image(self.executable, [dependency], arch="x86_64")
        self.inspector.add_image(self.frameworks / "libsample.dylib")
        self.inspector.add_image(self.frameworks / "libsample.dylib", arch="x86_64")
        with self.assertRaisesRegex(checker.BundleCheckError, "unresolved @rpath"):
            checker.validate_bundle(self.bundle, inspector=self.inspector)

    def test_dependency_requires_matching_macho_slice(self):
        self._add_executable(["@executable_path/../Frameworks/libsample.dylib"])
        self.inspector.add_image(self.frameworks / "libsample.dylib", arch="x86_64")
        with self.assertRaisesRegex(checker.BundleCheckError, "no matching architecture slice"):
            checker.validate_bundle(self.bundle, inspector=self.inspector)

    def test_dependency_cannot_be_an_ordinary_file(self):
        library = self.frameworks / "not-macho.dylib"
        library.write_text("not a library")
        self._add_executable(["@executable_path/../Frameworks/not-macho.dylib"])
        with self.assertRaisesRegex(checker.BundleCheckError, "is not Mach-O"):
            checker.validate_bundle(self.bundle, inspector=self.inspector)

    def test_dylib_load_cannot_resolve_to_a_macho_executable(self):
        self._add_executable(["@executable_path/../Frameworks/libsample.dylib"])
        self.inspector.add_image(self.frameworks / "libsample.dylib", filetype="EXECUTE")
        with self.assertRaisesRegex(checker.BundleCheckError, "is not MH_DYLIB"):
            checker.validate_bundle(self.bundle, inspector=self.inspector)

    def test_fat_image_external_load_in_secondary_slice_is_rejected(self):
        self._add_executable()
        self.inspector.add_image(self.executable, ["/opt/homebrew/lib/external.dylib"], arch="x86_64")
        with self.assertRaisesRegex(checker.BundleCheckError, "external non-system dependency"):
            checker.validate_bundle(self.bundle, inspector=self.inspector)

    def test_dylib_in_macos_uses_the_real_executable_context(self):
        self._add_executable(["@loader_path/libsample.dylib"], ["@executable_path/../Frameworks"])
        self.inspector.add_image(self.executable.parent / "libsample.dylib", ["@rpath/libchild.dylib"])
        self.inspector.add_image(self.frameworks / "libchild.dylib")
        checker.validate_bundle(self.bundle, inspector=self.inspector)

    def test_helper_executable_does_not_inherit_main_runpaths(self):
        self._add_executable(rpaths=["@executable_path/../Frameworks"])
        self.inspector.add_image(self.executable.parent / "helper", ["@rpath/libchild.dylib"], filetype="EXECUTE")
        self.inspector.add_image(self.frameworks / "libchild.dylib")
        with self.assertRaisesRegex(checker.BundleCheckError, "unresolved @rpath"):
            checker.validate_bundle(self.bundle, inspector=self.inspector)

    def test_every_inherited_loader_context_is_checked(self):
        self._add_executable(["@loader_path/liba.dylib", "@loader_path/libb.dylib"])
        self.inspector.add_image(self.executable.parent / "liba.dylib", ["@loader_path/libshared.dylib"], ["@executable_path/../Frameworks"])
        self.inspector.add_image(self.executable.parent / "libb.dylib", ["@loader_path/libshared.dylib"])
        self.inspector.add_image(self.executable.parent / "libshared.dylib", ["@rpath/libchild.dylib"])
        self.inspector.add_image(self.frameworks / "libchild.dylib")
        with self.assertRaisesRegex(checker.BundleCheckError, "unresolved @rpath"):
            checker.validate_bundle(self.bundle, inspector=self.inspector)

    def test_first_runpath_match_controls_loading(self):
        self._add_executable(["@rpath/libsample.dylib"], ["@loader_path", "@executable_path/../Frameworks"])
        # The first existing match is not a Mach-O image. A valid later match
        # must not conceal the loader failure.
        (self.executable.parent / "libsample.dylib").write_text("not Mach-O")
        self.inspector.add_image(self.frameworks / "libsample.dylib")
        with self.assertRaisesRegex(checker.BundleCheckError, "is not Mach-O"):
            checker.validate_bundle(self.bundle, inspector=self.inspector)

    def test_absolute_paths_inside_bundle_are_not_relocatable(self):
        library = self.frameworks / "libsample.dylib"
        self.inspector.add_image(library)
        self._add_executable([str(library)])
        with self.assertRaisesRegex(checker.BundleCheckError, "absolute in-bundle dependency"):
            checker.validate_bundle(self.bundle, inspector=self.inspector)
        self._add_executable(rpaths=[str(self.frameworks)])
        with self.assertRaisesRegex(checker.BundleCheckError, "absolute in-bundle runpath"):
            checker.validate_bundle(self.bundle, inspector=self.inspector)

    def test_loader_tokens_cannot_reset_the_path_to_an_absolute_suffix(self):
        for token in ("@loader_path", "@executable_path", "@rpath"):
            with self.subTest(token=token):
                self._add_executable([token + "//usr/lib/libSystem.B.dylib"], ["@executable_path/../Frameworks"])
                with self.assertRaisesRegex(checker.BundleCheckError, "absolute suffix after loader token"):
                    checker.validate_bundle(self.bundle, inspector=self.inspector)


class MacOSPackagingSourceTests(unittest.TestCase):
    """Portable source contracts, not native CMake or signing qualification."""

    def test_plist_template_is_readable_by_the_checker(self):
        template = (SCRIPT.parents[1] / "tools/realsense-viewer/macos/Info.plist.in").read_text()
        replacements = {
            "MACOSX_BUNDLE_EXECUTABLE_NAME": "realsense-viewer",
            "MACOSX_BUNDLE_GUI_IDENTIFIER": "com.intel.realsense.viewer",
            "MACOSX_BUNDLE_BUNDLE_NAME": "RealSense Viewer",
            "MACOSX_BUNDLE_BUNDLE_VERSION": "2.59.1",
        }
        for key, value in replacements.items():
            template = template.replace("${" + key + "}", value)
        plist = plistlib.loads(template.encode("utf-8"))
        self.assertEqual(plist["CFBundleExecutable"], "realsense-viewer")
        self.assertEqual(plist["CFBundleVersion"], "2.59.1")

    def test_preset_install_component_precedes_pattern_filter(self):
        source = (SCRIPT.parents[1] / "tools/realsense-viewer/CMakeLists.txt").read_text()
        bundle_rules = source.split("if(BUILD_MACOS_VIEWER_BUNDLE)")[-1].split("else()")[0]
        preset_rule = bundle_rules.split("install(DIRECTORY presets/")[1].split(")")[0]
        self.assertLess(preset_rule.index("COMPONENT macos-viewer"), preset_rule.index("FILES_MATCHING"))

    def test_bundle_prerequisites_are_validated_in_options(self):
        source = (SCRIPT.parents[1] / "CMake/lrs_options.cmake").read_text()
        validation = source.split("if(BUILD_MACOS_VIEWER_BUNDLE)")[1]
        self.assertIn("if(NOT APPLE)", validation)
        self.assertIn("if(NOT BUILD_EXAMPLES OR NOT BUILD_GRAPHICAL_EXAMPLES)", validation)
        self.assertIn("if(NOT BUILD_GLSL_EXTENSIONS)", validation)

    def test_fixup_layout_and_signing_order_remain_explicit(self):
        source = (SCRIPT.parents[1] / "tools/realsense-viewer/macos/install-bundle.cmake.in").read_text()
        self.assertIn("function(gp_item_default_embedded_path_override item path_var)", source)
        self.assertIn('set(${path_var} "@executable_path/../Frameworks" PARENT_SCOPE)', source)
        self.assertLess(source.index('fixup_bundle("'), source.index('function(_viewer_sign path)'))
        self.assertIn("--force --sign - --timestamp=none", source)
        self.assertIn("--verify --deep --strict", source)
        self.assertNotIn("--entitlements", source)

    def test_cli_smoke_paths_match_release_runtime_output(self):
        root = SCRIPT.parents[1]
        workflow = (root / ".github/workflows/macos-viewer.yml").read_text()
        guide = (root / "doc/installation_osx.md").read_text()
        self.assertIn('"$RUNNER_TEMP/build-cli/Release/realsense-viewer" --version', workflow)
        self.assertIn("./build-viewer-cli/Release/realsense-viewer --version", guide)
        self.assertNotIn("build-cli/tools/realsense-viewer/realsense-viewer", workflow)
        self.assertNotIn("build-viewer-cli/tools/realsense-viewer/realsense-viewer", guide)


# Native CI already has these tools. A scratch toolchain can be selected on
# minimal hosts without installing anything or changing the project options.
TEST_CMAKE = os.environ.get("MACOS_VIEWER_TEST_CMAKE") or shutil.which("cmake")
TEST_NINJA = os.environ.get("MACOS_VIEWER_TEST_NINJA") or shutil.which("ninja")
TEST_CC = os.environ.get("MACOS_VIEWER_TEST_CC") or shutil.which("cc")


@unittest.skipUnless(TEST_CMAKE and TEST_NINJA and TEST_CC, "CMake generation fixture requires CMake, Ninja, and a C compiler")
class MacOSBundleCMakeGenerationTests(unittest.TestCase):
    """Real CMake generation/lookup, with mock bundle contents and signing."""

    def test_single_and_multi_config_fixup_uses_actual_target_directories(self):
        root = SCRIPT.parents[1]
        source = (root / "tools/realsense-viewer/CMakeLists.txt").read_text()
        generation_rules = source.split('    set(_viewer_bundle_install_script "', 1)[1].split("else()", 1)[0]
        generation_rules = '    set(_viewer_bundle_install_script "' + generation_rules
        help_output = subprocess.check_output([TEST_CMAKE, "--help"], text=True)
        generators = ["Ninja"]
        if "Ninja Multi-Config" in help_output:
            generators.append("Ninja Multi-Config")
        for generator in generators:
            with self.subTest(generator=generator), tempfile.TemporaryDirectory(prefix="viewer cmake with spaces ") as temp:
                fixture = Path(temp)
                build = fixture / "build"
                (fixture / "macos").mkdir()
                shutil.copyfile(root / "tools/realsense-viewer/macos/install-bundle.cmake.in",
                                fixture / "macos/install-bundle.cmake.in")
                (fixture / "fixture.c").write_text("int fixture(void) { return 0; }\n")
                (fixture / "modules").mkdir()
                # This shim uses CMake's real prerequisite resolver, not a
                # source regex or a hard-coded claim that the files exist.
                (fixture / "modules/BundleUtilities.cmake").write_text('''
include("${CMAKE_ROOT}/Modules/GetPrerequisites.cmake")
function(fixup_bundle executable libs dirs)
    get_filename_component(exepath "${executable}" DIRECTORY)
    foreach(name librealsense2.2.59.dylib librealsense2-gl.2.59.dylib)
        gp_resolve_item("${executable}" "@rpath/${name}" "${exepath}" "${dirs}" resolved)
        if(NOT EXISTS "${resolved}")
            message(FATAL_ERROR "Fixture could not resolve ${name} using ${dirs}")
        endif()
    endforeach()
    file(WRITE "${exepath}/resolved-dirs.txt" "${dirs}")
endfunction()
''')
                (fixture / "bin").mkdir()
                for name, contents in (("file", "#!/bin/sh\necho 'ASCII text'\n"),
                                       ("codesign", "#!/bin/sh\nexit 0\n")):
                    tool = fixture / "bin" / name
                    tool.write_text(contents)
                    tool.chmod(0o755)
                (fixture / "CMakeLists.txt").write_text('''
cmake_minimum_required(VERSION 3.10)
project(viewer_bundle_fixture C)
include("''' + str(root / "CMake/unix_config.cmake") + '''")
os_set_flags()
set(CMAKE_INSTALL_LIBDIR lib)
add_library(realsense2 SHARED fixture.c)
add_library(realsense2-gl SHARED fixture.c)
add_executable(realsense-viewer fixture.c)
file(GENERATE OUTPUT "${CMAKE_BINARY_DIR}/cli-path-$<CONFIG>.txt" CONTENT "$<TARGET_FILE:realsense-viewer>")
# Give GL a distinct output directory too, to catch guessed global paths.
set_target_properties(realsense2-gl PROPERTIES LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/gl output")
install(CODE "set(CMAKE_MODULE_PATH \\"${CMAKE_CURRENT_SOURCE_DIR}/modules\\")" COMPONENT macos-viewer)
''' + generation_rules)
                command = [TEST_CMAKE, "-S", str(fixture), "-B", str(build), "-G", generator,
                           "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_C_COMPILER=" + TEST_CC,
                           "-DCMAKE_MAKE_PROGRAM=" + TEST_NINJA]
                subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                multi = generator == "Ninja Multi-Config"
                configurations = ("Release", "Debug") if multi else ("Release",)
                for config in configurations:
                    library_dir = build / "Release"
                    gl_dir = build / "gl output"
                    if multi:
                        library_dir /= config
                        gl_dir /= config
                    library_dir.mkdir(parents=True, exist_ok=True)
                    gl_dir.mkdir(parents=True, exist_ok=True)
                    (library_dir / "librealsense2.2.59.dylib").write_text("fixture library")
                    (gl_dir / "librealsense2-gl.2.59.dylib").write_text("fixture GL library")
                    prefix = fixture / "override prefix"
                    staging = fixture / ("staging " + config)
                    staged_prefix = Path(str(staging) + str(prefix))
                    executable_dir = staged_prefix / "realsense-viewer.app/Contents/MacOS"
                    executable_dir.mkdir(parents=True)
                    (executable_dir / "realsense-viewer").write_text("fixture executable")
                    env = dict(os.environ, DESTDIR=str(staging), PATH=str(fixture / "bin") + os.pathsep + os.environ["PATH"])
                    subprocess.run([TEST_CMAKE, "--install", str(build), "--config", config,
                                    "--prefix", str(prefix), "--component", "macos-viewer"],
                                   check=True, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    directories = (executable_dir / "resolved-dirs.txt").read_text().split(";")
                    self.assertIn(str(library_dir), directories)
                    self.assertIn(str(gl_dir), directories)
                    self.assertIn(str(prefix / "lib"), directories)
                    self.assertIn(str(staged_prefix / "lib"), directories)
                    self.assertEqual((build / ("cli-path-" + config + ".txt")).read_text(),
                                     str(library_dir / "realsense-viewer"))


if __name__ == "__main__":
    unittest.main()
