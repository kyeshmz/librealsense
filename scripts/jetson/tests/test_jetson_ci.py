import copy
import hashlib
import io
import json
import re
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts" / "jetson"))

import jetson_ci  # noqa: E402


class TargetMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.targets = jetson_ci.read_targets()

    def test_four_reference_targets_validate(self):
        self.assertEqual(tuple(self.targets), ("jp4", "jp5", "jp6", "jp7"))
        self.assertEqual(
            [(target["ubuntu"], target["kernel_family"]) for target in self.targets.values()],
            [("18.04", "4.9"), ("20.04", "5.10"), ("22.04", "5.15"), ("24.04", "6.8")],
        )
        for target in self.targets.values():
            self.assertRegex(target["image_digest"], jetson_ci.IMAGE_DIGEST_RE)
            self.assertTrue(target["source_url"].startswith("https://"))

    def test_rejects_missing_target_and_malformed_image_digest(self):
        data = json.loads(jetson_ci.TARGETS_PATH.read_text(encoding="utf-8"))
        missing = copy.deepcopy(data)
        missing["targets"].pop()
        with self.assertRaisesRegex(jetson_ci.JetsonCIError, "exactly jp4"):
            jetson_ci.validate_targets(missing)

        malformed = copy.deepcopy(data)
        malformed["targets"][0]["image_digest"] = "sha256:abc"
        with self.assertRaisesRegex(jetson_ci.JetsonCIError, "complete sha256 digest"):
            jetson_ci.validate_targets(malformed)

    def test_rejects_duplicate_ids_and_malformed_versions(self):
        data = json.loads(jetson_ci.TARGETS_PATH.read_text(encoding="utf-8"))
        duplicate = copy.deepcopy(data)
        duplicate["targets"][1]["id"] = "jp4"
        with self.assertRaisesRegex(jetson_ci.JetsonCIError, "duplicate target id"):
            jetson_ci.validate_targets(duplicate)

        invalid_version = copy.deepcopy(data)
        invalid_version["targets"][0]["ubuntu"] = "bionic"
        with self.assertRaisesRegex(jetson_ci.JetsonCIError, "invalid Ubuntu version"):
            jetson_ci.validate_targets(invalid_version)


class MatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.targets = jetson_ci.read_targets()

    def test_all_targets_and_backends_produce_eight_entries(self):
        matrix = jetson_ci.select_matrix(self.targets, "all", "both")
        self.assertEqual(len(matrix), 8)
        self.assertEqual(
            [(entry["target"], entry["backend"]) for entry in matrix],
            [
                (target, backend)
                for target in ("jp4", "jp5", "jp6", "jp7")
                for backend in ("rsusb", "native")
            ],
        )
        self.assertTrue(all(entry["image_digest"].startswith("sha256:") for entry in matrix))

    def test_single_target_backend_choices_and_rejections(self):
        self.assertEqual(
            [(entry["target"], entry["backend"]) for entry in jetson_ci.select_matrix(self.targets, "jp6", "native")],
            [("jp6", "native")],
        )
        self.assertEqual(len(jetson_ci.select_matrix(self.targets, "jp5", "rsusb")), 1)
        with self.assertRaisesRegex(jetson_ci.JetsonCIError, "unknown target"):
            jetson_ci.select_matrix(self.targets, "jp8", "both")
        with self.assertRaisesRegex(jetson_ci.JetsonCIError, "unknown backend"):
            jetson_ci.select_matrix(self.targets, "jp5", "cuda")


class ImageIdentityTests(unittest.TestCase):
    def test_registry_content_hash_is_verified(self):
        body = b"verified manifest bytes"
        digest = "sha256:" + hashlib.sha256(body).hexdigest()
        jetson_ci.DockerHubClient.verify_digest(body, digest, "test manifest")
        with self.assertRaisesRegex(jetson_ci.JetsonCIError, "content hash"):
            jetson_ci.DockerHubClient.verify_digest(body, "sha256:" + "0" * 64, "test manifest")

    def test_parses_os_release_and_accepts_expected_arm64_userland(self):
        release = jetson_ci.parse_os_release(
            'NAME="Ubuntu"\nID=ubuntu\nVERSION_ID="22.04"\nVERSION_CODENAME=jammy\n'
        )
        self.assertEqual(release["NAME"], "Ubuntu")
        jetson_ci.validate_image_identity(
            {"architecture": "arm64", "os": "linux"}, release, "22.04"
        )

    def test_rejects_wrong_os_architecture_and_distro(self):
        release = {"ID": "ubuntu", "VERSION_ID": "22.04"}
        with self.assertRaisesRegex(jetson_ci.JetsonCIError, "architecture is not arm64"):
            jetson_ci.validate_image_identity(
                {"architecture": "amd64", "os": "linux"}, release, "22.04"
            )
        with self.assertRaisesRegex(jetson_ci.JetsonCIError, "OS is not linux"):
            jetson_ci.validate_image_identity(
                {"architecture": "arm64", "os": "windows"}, release, "22.04"
            )
        with self.assertRaisesRegex(jetson_ci.JetsonCIError, "not Ubuntu"):
            jetson_ci.validate_image_identity(
                {"architecture": "arm64", "os": "linux"}, {"ID": "debian", "VERSION_ID": "22.04"}, "22.04"
            )
        with self.assertRaisesRegex(jetson_ci.JetsonCIError, "expected 20.04"):
            jetson_ci.validate_image_identity(
                {"architecture": "arm64", "os": "linux"}, release, "20.04"
            )


class DeviceCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.targets = jetson_ci.read_targets()

    def test_kernel_family_boundaries_and_stock_release_shape(self):
        self.assertEqual(jetson_ci.classify_jetson_kernel("5.15.148-tegra"), "5.15")
        self.assertEqual(jetson_ci.classify_jetson_kernel("5.10.120-tegra"), "5.10")
        self.assertEqual(jetson_ci.classify_jetson_kernel("5.150.1-tegra"), "5.150")
        for ambiguous in ("5.15.148-rt-tegra", "5.15.148-tegra-custom", "5.15.148"):
            with self.subTest(kernel=ambiguous), self.assertRaises(jetson_ci.JetsonCIError):
                jetson_ci.classify_jetson_kernel(ambiguous)

    def test_l4t_parse_and_nonreference_warning(self):
        self.assertEqual(
            jetson_ci.parse_nv_tegra_release("# R36 (release), REVISION: 4.4, GCID: 1, BOARD: t234ref"),
            "36.4.4",
        )
        warnings = jetson_ci.evaluate_device(
            self.targets["jp6"],
            "rsusb",
            architecture="arm64",
            machine="aarch64",
            os_release={"ID": "ubuntu", "VERSION_ID": "22.04"},
            nv_tegra_release="# R36 (release), REVISION: 4.3, GCID: 1, BOARD: t234ref",
            kernel_release="5.15.148-tegra",
        )
        self.assertEqual(len(warnings), 1)
        self.assertIn("nonreference L4T 36.4.3", warnings[0])
        self.assertIn("candidate", warnings[0])

    def test_device_rejects_unknown_releases_wrong_family_and_invalid_architecture(self):
        common = {
            "architecture": "arm64",
            "machine": "aarch64",
            "os_release": {"ID": "ubuntu", "VERSION_ID": "22.04"},
            "nv_tegra_release": "# R36 (release), REVISION: 4.4, BOARD: t234ref",
            "kernel_release": "5.15.148-tegra",
        }
        wrong_family = dict(common, os_release={"ID": "ubuntu", "VERSION_ID": "20.04"})
        with self.assertRaisesRegex(jetson_ci.JetsonCIError, "kernel family 5.15 does not match"):
            jetson_ci.evaluate_device(self.targets["jp5"], "rsusb", **wrong_family)
        invalid_arch = dict(common, architecture="amd64")
        with self.assertRaisesRegex(jetson_ci.JetsonCIError, "dpkg architecture"):
            jetson_ci.evaluate_device(self.targets["jp6"], "rsusb", **invalid_arch)
        unknown_release = dict(common, nv_tegra_release="not an NVIDIA release")
        with self.assertRaisesRegex(jetson_ci.JetsonCIError, "cannot parse an NVIDIA L4T"):
            jetson_ci.evaluate_device(self.targets["jp6"], "rsusb", **unknown_release)
        custom_kernel = dict(common, kernel_release="5.15.148-rt-tegra")
        with self.assertRaisesRegex(jetson_ci.JetsonCIError, "RT kernel"):
            jetson_ci.evaluate_device(self.targets["jp6"], "rsusb", **custom_kernel)

    def test_backend_labels_and_native_patch_warning(self):
        rsusb = jetson_ci.backend_cmake_options("rsusb")
        native = jetson_ci.backend_cmake_options("native")
        self.assertEqual(rsusb["FORCE_RSUSB_BACKEND"], "ON")
        self.assertEqual(native["FORCE_RSUSB_BACKEND"], "OFF")
        for options in (rsusb, native):
            self.assertEqual(options["BUILD_EXAMPLES"], "OFF")
            self.assertEqual(options["BUILD_TOOLS"], "ON")
            self.assertEqual(options["BUILD_ROSBAG2"], "OFF")
            self.assertEqual(options["BUILD_WITH_DDS"], "OFF")
            self.assertEqual(options["BUILD_WITH_CPU_EXTENSIONS"], "OFF")
            self.assertEqual(options["BUILD_WITH_NEON"], "ON")
        warnings = jetson_ci.evaluate_device(
            self.targets["jp6"],
            "native",
            architecture="arm64",
            machine="aarch64",
            os_release={"ID": "ubuntu", "VERSION_ID": "22.04"},
            nv_tegra_release="# R36 (release), REVISION: 4.4, BOARD: t234ref",
            kernel_release="5.15.148-tegra",
        )
        self.assertEqual(len(warnings), 1)
        self.assertIn("Module.symvers", warnings[0])


class ManifestAndChecksumTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.targets = jetson_ci.read_targets()

    def test_manifest_records_actual_backend_toolchain_image_and_host_kernel(self):
        manifest = jetson_ci.build_manifest(
            self.targets,
            target_id="jp6",
            backend="native",
            source_sha="a" * 40,
            ubuntu_version="22.04",
            architecture="arm64",
            host_kernel="6.8.0-runner",
            image_launcher="workflow",
            compiler="g++ 13.3.0",
            cmake="cmake version 3.28.3",
            fetched_json_commit="b" * 40,
            dependency_versions={"cmake": "3.28.3-0ubuntu1", "build-essential": "12.10"},
        )
        self.assertEqual(manifest["backend"], "native")
        self.assertEqual(manifest["cmake_options"]["FORCE_RSUSB_BACKEND"], "OFF")
        self.assertEqual(manifest["cmake_options"]["BUILD_EXAMPLES"], "OFF")
        self.assertEqual(manifest["cmake_options"]["BUILD_TOOLS"], "ON")
        image = manifest["container_image"]
        self.assertEqual(
            image["launcher_selection"]["configured_arm64_manifest_digest"],
            self.targets["jp6"]["image_digest"],
        )
        self.assertEqual(image["launcher_selection"]["launcher"], "workflow")
        self.assertEqual(image["in_container_observation"]["architecture"], "arm64")
        self.assertIsNone(image["in_container_observation"]["image_digest"])
        self.assertEqual(manifest["build_environment"]["userland"]["architecture"], "arm64")
        self.assertEqual(manifest["build_environment"]["host_kernel"]["release"], "6.8.0-runner")
        self.assertIn("shares this host kernel", manifest["build_environment"]["host_kernel"]["note"])
        self.assertEqual(manifest["fetched_dependencies"]["nlohmann_json"]["git_commit"], "b" * 40)
        self.assertIn("not locked", manifest["reproducibility"])
        self.assertEqual(manifest["hardware_validation"], "not performed")

    def test_manifest_rejects_wrong_architecture_and_malformed_fetched_commit(self):
        values = {
            "target_id": "jp4",
            "backend": "rsusb",
            "source_sha": "a" * 40,
            "ubuntu_version": "18.04",
            "architecture": "arm64",
            "host_kernel": "6.8.0-runner",
            "image_launcher": "workflow",
            "compiler": "g++",
            "cmake": "cmake",
            "fetched_json_commit": "b" * 40,
            "dependency_versions": {"cmake": "3.10.2"},
        }
        invalid_arch = dict(values, architecture="amd64")
        with self.assertRaisesRegex(jetson_ci.JetsonCIError, "architecture must be arm64"):
            jetson_ci.build_manifest(self.targets, **invalid_arch)
        invalid_commit = dict(values, fetched_json_commit="bad")
        with self.assertRaisesRegex(jetson_ci.JetsonCIError, "fetched nlohmann/json commit"):
            jetson_ci.build_manifest(self.targets, **invalid_commit)

    def test_checksum_matches_archive_content_and_sidecar_format(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive = Path(temporary_directory) / "sdk.tar.gz"
            archive.write_bytes(b"small test archive\n")
            checksum_path = jetson_ci.write_checksum(archive)
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            self.assertEqual(checksum_path.read_text(encoding="utf-8"), f"{digest}  sdk.tar.gz\n")
            self.assertTrue(checksum_path.name.endswith(".tar.gz.sha256"))


class WorkflowContractTests(unittest.TestCase):
    def test_workflow_is_isolated_read_only_pinned_and_arm64(self):
        workflow = (ROOT / ".github/workflows/jetson-binaries.yml").read_text(encoding="utf-8")
        self.assertIn("branches: [jetson-binary-builds]", workflow)
        for path in (
            ".github/workflows/jetson-binaries.yml",
            "scripts/jetson/**",
            "doc/installation_jetson.md",
            "doc/jetson_binary_builds.md",
        ):
            self.assertIn(path, workflow)
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("runs-on: ubuntu-24.04-arm", workflow)
        self.assertIn("fail-fast: false", workflow)
        self.assertIn("timeout-minutes:", workflow)
        self.assertIn("docker pull --platform linux/arm64", workflow)
        self.assertIn("docker run --platform linux/arm64", workflow)
        self.assertIn("if-no-files-found: error", workflow)
        self.assertIn("Run offline regression tests", workflow)
        self.assertIn("if: always()", workflow)
        self.assertIn("-pull.log", workflow)
        self.assertIn("-build.log", workflow)
        self.assertIn("JETSON_EXPECTED_UBUNTU_VERSION", workflow)
        self.assertIn("JETSON_IMAGE_LAUNCHER=workflow", workflow)
        self.assertIn("JETSON_TARGET: ${{ github.event_name == 'workflow_dispatch' && inputs.target || 'all' }}", workflow)
        self.assertIn("JETSON_BACKEND: ${{ github.event_name == 'workflow_dispatch' && inputs.backend || 'both' }}", workflow)
        self.assertNotIn("--privileged", workflow)
        self.assertNotRegex(workflow, r"permissions:\s*\n\s+\w+:\s+write")
        action_refs = re.findall(r"uses:\s+[^@\s]+@([0-9a-f]+)", workflow)
        self.assertEqual(len(action_refs), 4)
        self.assertTrue(all(len(ref) == 40 for ref in action_refs))

    def test_build_script_is_tools_only_and_cmake_310_compatible(self):
        build_script = (ROOT / "scripts/jetson/build.sh").read_text(encoding="utf-8")
        self.assertIn('"-DBUILD_EXAMPLES=OFF"', build_script)
        self.assertIn('"-DBUILD_TOOLS=ON"', build_script)
        self.assertIn('"-DBUILD_ROSBAG2=OFF"', build_script)
        self.assertIn('"-DBUILD_WITH_DDS=OFF"', build_script)
        self.assertIn('"-DBUILD_WITH_CPU_EXTENSIONS=OFF"', build_script)
        self.assertIn('"libdir=${prefix}/lib"', build_script)
        self.assertIn('"$PAYLOAD/lib/librsutils.a"', build_script)
        self.assertIn('cmake --build . --target install', build_script)
        self.assertNotIn("cmake -S ", build_script)
        self.assertNotIn("--parallel", build_script)
        self.assertIn('rm -rf -- "$BUILD_DIR" "$STAGE_DIR"', build_script)
        self.assertIn('"$RELOCATED_PREFIX/bin/rs-enumerate-devices" --help', build_script)
        self.assertIn('realsense2::realsense2', build_script)
        self.assertIn('pkg-config --exists realsense2', build_script)
        self.assertIn('sha256', (ROOT / "scripts/jetson/jetson_ci.py").read_text(encoding="utf-8"))


class ElfArchitectureValidationTests(unittest.TestCase):
    def validate_header(self, header, filename="librsutils.a"):
        build_script = (ROOT / "scripts/jetson/build.sh").read_text(encoding="utf-8")
        match = re.search(
            r"(?ms)^validate_arm64_elf_header\(\) \{\n.*?^\}", build_script
        )
        self.assertIsNotNone(match, "build script ELF parser function was not found")
        command = match.group(0) + '\nvalidate_arm64_elf_header "$1" "$2"'
        return subprocess.run(
            ["bash", "-c", command, "test", filename, header],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )

    def test_old_readelf_archive_whitespace_and_multiple_members(self):
        header = """\
File: librsutils.a(rs_context.o)

ELF Header:
  Magic:   7f 45 4c 46 02 01 01 00
  Class:                             ELF64
  Data:                              2's complement, little endian
  Type:                              REL (Relocatable file)
  Machine:                           AArch64

File: librsutils.a(rs_device.o)

ELF Header:
  Magic:   7f 45 4c 46 02 01 01 00
  Class:                             ELF64
  Data:                              2's complement, little endian
  Type:                              REL (Relocatable file)
  Machine:                           AArch64   \t
"""
        result = self.validate_header(header)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_mixed_architecture_archive_is_rejected(self):
        header = """\
File: librsutils.a(rs_context.o)
ELF Header:
  Type:                              REL (Relocatable file)
  Machine:                           AArch64
File: librsutils.a(x86_helper.o)
ELF Header:
  Type:                              REL (Relocatable file)
  Machine:                           Advanced Micro Devices X86-64
"""
        result = self.validate_header(header)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("non-ARM64 ELF object", result.stderr)
        self.assertIn("Advanced Micro Devices X86-64", result.stderr)

    def test_missing_or_empty_machine_field_is_rejected(self):
        headers = (
            "ELF Header:\n  Type: REL (Relocatable file)\n",
            "ELF Header:\n  Machine:     \n",
        )
        for header in headers:
            with self.subTest(header=header):
                result = self.validate_header(header)
                self.assertNotEqual(result.returncode, 0)
                self.assertRegex(result.stderr, "Machine")


class BootstrapAndLocalBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.targets = jetson_ci.read_targets()

    def test_build_bootstrap_checks_shell_identity_before_apt_python_or_git(self):
        script = (ROOT / "scripts/jetson/build.sh").read_text(encoding="utf-8")
        self.assertLess(script.index('MACHINE="$(uname -m)"'), script.index("apt-get update"))
        self.assertLess(script.index("dpkg --print-architecture"), script.index("apt-get update"))
        self.assertLess(script.index(". /etc/os-release"), script.index("apt-get update"))
        self.assertLess(script.index("JETSON_IMAGE_DIGEST"), script.index("apt-get update"))
        self.assertLess(script.index("apt-get install"), script.index('python3 "$CI_TOOL" preflight'))
        self.assertLess(
            script.index('python3 "$CI_TOOL" preflight'),
            script.index('git config --global --replace-all safe.directory "$REPO_ROOT"'),
        )
        start = script.index("APT_PACKAGES=(")
        packages = script[start:script.index(")", start)]
        self.assertIn("python3", packages)
        self.assertIn("ca-certificates", packages)
        self.assertIn("git", packages)
        self.assertIn("--expected-image-digest", script)

    def test_build_git_trust_is_exact_scoped_and_precedes_source_git_checks(self):
        script = (ROOT / "scripts/jetson/build.sh").read_text(encoding="utf-8")
        install = script.index("apt-get install")
        preflight = script.index('python3 "$CI_TOOL" preflight')
        digest_check = script.index('[[ "$IMAGE_DIGEST" == "$JETSON_IMAGE_DIGEST" ]]')
        architecture_check = script.index(
            '[[ "$ARCHITECTURE" == "arm64" ]] ||', preflight
        )
        trust = script.index(
            'git config --global --replace-all safe.directory "$REPO_ROOT"'
        )
        head = script.index('git -C "$REPO_ROOT" rev-parse HEAD')
        status = script.index(
            'git -C "$REPO_ROOT" status --porcelain --untracked-files=all'
        )

        self.assertLess(install, preflight)
        self.assertLess(preflight, digest_check)
        self.assertLess(digest_check, architecture_check)
        self.assertLess(architecture_check, trust)
        self.assertLess(trust, head)
        self.assertLess(head, status)
        self.assertEqual(script.count("git config --global"), 1)
        self.assertNotIn('safe.directory "*"', script)
        self.assertNotIn("git -c safe.directory", script)

    def test_preflight_requires_exact_configured_launcher_identity(self):
        target = self.targets["jp6"]
        with mock.patch.object(jetson_ci, "_read_system_file", return_value='ID=ubuntu\nVERSION_ID="22.04"\n'), \
                mock.patch.object(jetson_ci.platform, "machine", return_value="aarch64"), \
                mock.patch.object(jetson_ci, "_dpkg_architecture", return_value="arm64"):
            result = jetson_ci.preflight_build(
                target,
                expected_ubuntu="22.04",
                expected_image_digest=target["image_digest"],
                image_launcher="workflow",
            )
        self.assertEqual(result["image_digest"], target["image_digest"])
        self.assertEqual(result["image_launcher"], "workflow")
        with self.assertRaisesRegex(jetson_ci.JetsonCIError, "does not match configured target digest"):
            jetson_ci.preflight_build(
                target,
                expected_ubuntu="22.04",
                expected_image_digest="sha256:" + "0" * 64,
                image_launcher="workflow",
            )
        with self.assertRaisesRegex(jetson_ci.JetsonCIError, "Ubuntu version does not match"):
            jetson_ci.preflight_build(
                target,
                expected_ubuntu="20.04",
                expected_image_digest=target["image_digest"],
                image_launcher="workflow",
            )
        with self.assertRaisesRegex(jetson_ci.JetsonCIError, "image digest is missing or malformed"):
            jetson_ci.preflight_build(
                target,
                expected_ubuntu="22.04",
                expected_image_digest="",
                image_launcher="workflow",
            )
        with self.assertRaisesRegex(jetson_ci.JetsonCIError, "Ubuntu version is missing or malformed"):
            jetson_ci.preflight_build(
                target,
                expected_ubuntu="",
                expected_image_digest=target["image_digest"],
                image_launcher="workflow",
            )

    def test_local_build_selects_verified_digest_and_passes_exact_identity(self):
        source_sha = "a" * 40
        completed = [
            mock.Mock(stdout=source_sha),
            mock.Mock(stdout=""),
            mock.Mock(),
            mock.Mock(stdout="linux arm64\n"),
            mock.Mock(),
        ]
        with mock.patch.object(jetson_ci.platform, "machine", return_value="aarch64"), \
                mock.patch.object(jetson_ci, "_dpkg_architecture", return_value="arm64"), \
                mock.patch.object(jetson_ci, "DockerHubClient") as docker_hub, \
                mock.patch.object(jetson_ci.subprocess, "run", side_effect=completed) as run_command, \
                mock.patch("builtins.print"):
            self.assertEqual(jetson_ci.local_build("jp6", "rsusb", self.targets), 0)

        target = self.targets["jp6"]
        docker_hub.assert_called_once_with(target["image_repository"])
        docker_hub.return_value.verify_target.assert_called_once_with(target)
        calls = [call[0][0] for call in run_command.call_args_list]
        image_reference = f"{target['image_repository']}@{target['image_digest']}"
        self.assertEqual(calls[2], ["docker", "pull", "--platform", "linux/arm64", image_reference])
        self.assertEqual(calls[3][:4], ["docker", "image", "inspect", "--format"])
        docker_run = calls[4]
        self.assertIn(f"JETSON_EXPECTED_UBUNTU_VERSION={target['ubuntu']}", docker_run)
        self.assertIn(f"JETSON_IMAGE_DIGEST={target['image_digest']}", docker_run)
        self.assertIn("JETSON_IMAGE_LAUNCHER=local-build", docker_run)
        self.assertIn(f"SOURCE_SHA={source_sha}", docker_run)
        self.assertNotIn("--privileged", docker_run)

    def test_local_build_rejects_non_native_architecture_before_docker(self):
        with mock.patch.object(jetson_ci.platform, "machine", return_value="x86_64"), \
                mock.patch.object(jetson_ci, "_dpkg_architecture", return_value="amd64"), \
                mock.patch.object(jetson_ci.subprocess, "run") as run_command:
            with self.assertRaisesRegex(jetson_ci.JetsonCIError, "requires native ARM64"):
                jetson_ci.local_build("jp6", "rsusb", self.targets)
        run_command.assert_not_called()

    def test_local_build_rejects_dirty_source_before_docker(self):
        completed = [mock.Mock(stdout="b" * 40), mock.Mock(stdout=" M tracked.c\n")]
        with mock.patch.object(jetson_ci.platform, "machine", return_value="aarch64"), \
                mock.patch.object(jetson_ci, "_dpkg_architecture", return_value="arm64"), \
                mock.patch.object(jetson_ci.subprocess, "run", side_effect=completed) as run_command:
            with self.assertRaisesRegex(jetson_ci.JetsonCIError, "clean checkout"):
                jetson_ci.local_build("jp6", "rsusb", self.targets)
        self.assertEqual(run_command.call_count, 2)

    def test_local_build_rejects_pulled_non_arm64_image_before_running_it(self):
        completed = [
            mock.Mock(stdout="b" * 40),
            mock.Mock(stdout=""),
            mock.Mock(),
            mock.Mock(stdout="linux amd64\n"),
        ]
        with mock.patch.object(jetson_ci.platform, "machine", return_value="aarch64"), \
                mock.patch.object(jetson_ci, "_dpkg_architecture", return_value="arm64"), \
                mock.patch.object(jetson_ci, "DockerHubClient"), \
                mock.patch.object(jetson_ci.subprocess, "run", side_effect=completed) as run_command, \
                mock.patch("builtins.print"):
            with self.assertRaisesRegex(jetson_ci.JetsonCIError, "expected 'linux arm64'"):
                jetson_ci.local_build("jp6", "rsusb", self.targets)
        self.assertEqual(run_command.call_count, 4)


class ArchiveVerifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.targets = jetson_ci.read_targets()

    def manifest(self, target="jp6", backend="rsusb", image_digest=None):
        metadata = self.targets[target]
        return {
            "schema_version": 1,
            "source_sha": "c" * 40,
            "target": {"id": target},
            "backend": backend,
            "container_image": {
                "reference": f"{metadata['image']}:{metadata['image_tag']}",
                "launcher_selection": {
                    "launcher": "workflow",
                    "configured_arm64_manifest_digest": image_digest or metadata["image_digest"],
                },
                "in_container_observation": {
                    "os": "linux",
                    "architecture": "arm64",
                    "ubuntu_userland": metadata["ubuntu"],
                    "image_digest": None,
                },
            },
        }

    def archive_entries(self, manifest=None, extra=None):
        entries = [
            ("dir", ".", 0o755, ""),
            ("dir", "./usr", 0o755, ""),
            ("dir", "./usr/local", 0o755, ""),
            ("dir", "./usr/local/lib", 0o755, ""),
            ("dir", "./usr/local/share", 0o755, ""),
            ("dir", "./usr/local/share/doc", 0o755, ""),
            ("dir", "./usr/local/share/doc/librealsense2", 0o755, ""),
            ("file", "./usr/local/lib/librealsense2.so.2.56.4", 0o644, "library"),
            ("symlink", "./usr/local/lib/librealsense2.so.2", 0o777, "librealsense2.so.2.56.4"),
            ("symlink", "./usr/local/lib/librealsense2.so", 0o777, "librealsense2.so.2"),
            (
                "file",
                "./usr/local/share/doc/librealsense2/jetson-build-manifest.json",
                0o644,
                json.dumps(manifest or self.manifest()),
            ),
        ]
        return entries + list(extra or [])

    def write_archive(self, path, entries):
        with tarfile.open(str(path), mode="w:gz") as archive:
            for kind, name, mode, data in entries:
                info = tarfile.TarInfo(name)
                info.mode = mode
                if kind == "dir":
                    info.type = tarfile.DIRTYPE
                    archive.addfile(info)
                elif kind == "symlink":
                    info.type = tarfile.SYMTYPE
                    info.linkname = data
                    archive.addfile(info)
                elif kind == "hardlink":
                    info.type = tarfile.LNKTYPE
                    info.linkname = data
                    archive.addfile(info)
                elif kind == "fifo":
                    info.type = tarfile.FIFOTYPE
                    archive.addfile(info)
                else:
                    payload = data.encode("utf-8") if isinstance(data, str) else data
                    info.type = tarfile.REGTYPE
                    info.size = len(payload)
                    archive.addfile(info, io.BytesIO(payload))

    def make_verified_file(self, directory, entries=None, manifest=None):
        archive_path = Path(directory) / "librealsense-jetson-jp6-rsusb-test.tar.gz"
        checksum_path = Path(str(archive_path) + ".sha256")
        self.write_archive(archive_path, self.archive_entries(manifest=manifest, extra=entries))
        jetson_ci.write_checksum(archive_path)
        return archive_path, checksum_path

    def test_verifies_checksum_manifest_and_benign_library_symlink_chain_without_extracting(self):
        with tempfile.TemporaryDirectory() as directory:
            archive_path, checksum_path = self.make_verified_file(directory)
            manifest = jetson_ci.verify_archive(
                archive_path, checksum_path, "jp6", "rsusb", self.targets
            )
            self.assertEqual(manifest["source_sha"], "c" * 40)
            self.assertFalse((Path(directory) / "usr").exists())

    def test_checksum_must_bind_exact_archive_and_match_content(self):
        with tempfile.TemporaryDirectory() as directory:
            archive_path, checksum_path = self.make_verified_file(directory)
            checksum_path.write_text(
                checksum_path.read_text(encoding="utf-8").replace(archive_path.name, "other.tar.gz"),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(jetson_ci.JetsonCIError, "not requested archive"):
                jetson_ci.verify_archive(archive_path, checksum_path, "jp6", "rsusb", self.targets)
            jetson_ci.write_checksum(archive_path)
            archive_path.write_bytes(archive_path.read_bytes() + b"tampered")
            with self.assertRaisesRegex(jetson_ci.JetsonCIError, "does not match"):
                jetson_ci.verify_archive(archive_path, checksum_path, "jp6", "rsusb", self.targets)

    def test_manifest_must_match_target_backend_and_image_digest(self):
        cases = [
            (self.manifest(target="jp5"), "target"),
            (self.manifest(backend="native"), "backend"),
            (self.manifest(image_digest="sha256:" + "0" * 64), "image digest"),
        ]
        for manifest, message in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as directory:
                archive_path, checksum_path = self.make_verified_file(directory, manifest=manifest)
                with self.assertRaisesRegex(jetson_ci.JetsonCIError, message):
                    jetson_ci.verify_archive(archive_path, checksum_path, "jp6", "rsusb", self.targets)

    def test_rejects_traversal_absolute_duplicate_hardlink_special_and_setid_members(self):
        unsafe = [
            (("file", "./usr/local/../../etc/passwd", 0o644, "x"), "traversing"),
            (("file", "/etc/passwd", 0o644, "x"), "absolute"),
            (("file", "./usr/local/lib/librealsense2.so", 0o644, "duplicate"), "duplicate"),
            (("hardlink", "./usr/local/lib/hard", 0o644, "./usr/local/lib/librealsense2.so"), "hardlink"),
            (("fifo", "./usr/local/lib/pipe", 0o644, ""), "special type"),
            (("file", "./usr/local/lib/setid", 0o4755, "x"), "set-ID"),
        ]
        for entry, message in unsafe:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as directory:
                archive_path, checksum_path = self.make_verified_file(directory, [entry])
                with self.assertRaisesRegex(jetson_ci.JetsonCIError, message):
                    jetson_ci.verify_archive(archive_path, checksum_path, "jp6", "rsusb", self.targets)

    def test_rejects_escaping_symlinks_and_members_beneath_symlink_parents(self):
        cases = [
            ([ ("symlink", "./usr/local/lib/escape", 0o777, "../../../etc/passwd") ], "escapes"),
            ([
                ("symlink", "./usr/local/linkdir", 0o777, "lib"),
                ("file", "./usr/local/linkdir/payload", 0o644, "x"),
            ], "beneath a non-directory"),
        ]
        for extra, message in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as directory:
                archive_path, checksum_path = self.make_verified_file(directory, entries=extra)
                with self.assertRaisesRegex(jetson_ci.JetsonCIError, message):
                    jetson_ci.verify_archive(archive_path, checksum_path, "jp6", "rsusb", self.targets)


if __name__ == "__main__":
    unittest.main()
