#!/usr/bin/env python3
"""Validation and packaging helpers for the isolated Jetson binary workflow."""

import argparse
import hashlib
import io
import json
import platform
import re
import shlex
import subprocess
import sys
import tarfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


SCRIPT_DIR = Path(__file__).resolve().parent
TARGETS_PATH = SCRIPT_DIR / "targets.json"
EXPECTED_TARGETS = ("jp4", "jp5", "jp6", "jp7")
BACKENDS = ("rsusb", "native")
IMAGE_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
VERSION_RE = re.compile(r"^\d+\.\d+(?:\.\d+)?$")
UBUNTU_RE = re.compile(r"^\d{2}\.\d{2}$")
SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")
REGISTRY = "registry-1.docker.io"
DOCKER_AUTH = "https://auth.docker.io/token"
DOCKER_MANIFEST_ACCEPT = ", ".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)
USER_AGENT = "librealsense-jetson-image-verifier/1"


class JetsonCIError(Exception):
    """A validation or registry error suitable for a concise CLI message."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise JetsonCIError(message)


def validate_targets(data: Any) -> Dict[str, Dict[str, Any]]:
    """Validate target metadata and return it indexed by its stable identifier."""
    _require(isinstance(data, dict), "target metadata must be a JSON object")
    _require(set(data) == {"schema_version", "targets"}, "unexpected target metadata fields")
    _require(data.get("schema_version") == 1, "unsupported targets.json schema_version")
    targets = data.get("targets")
    _require(isinstance(targets, list), "targets must be a JSON array")
    indexed: Dict[str, Dict[str, Any]] = {}
    required = {
        "id",
        "jetpack",
        "l4t",
        "ubuntu",
        "kernel_family",
        "boards",
        "source_url",
        "image",
        "image_repository",
        "image_tag",
        "image_digest",
    }
    for position, target in enumerate(targets):
        prefix = f"targets[{position}]"
        _require(isinstance(target, dict), f"{prefix} must be a JSON object")
        _require(set(target) == required, f"{prefix} fields do not match the supported schema")
        target_id = target.get("id")
        _require(isinstance(target_id, str) and target_id, f"{prefix}.id must be a string")
        _require(target_id not in indexed, f"duplicate target id {target_id!r}")
        _require(VERSION_RE.fullmatch(str(target.get("jetpack", ""))) is not None,
                 f"{target_id}: invalid JetPack version")
        _require(re.fullmatch(r"\d+\.\d+\.\d+", str(target.get("l4t", ""))) is not None,
                 f"{target_id}: invalid L4T reference release")
        _require(UBUNTU_RE.fullmatch(str(target.get("ubuntu", ""))) is not None,
                 f"{target_id}: invalid Ubuntu version")
        _require(re.fullmatch(r"\d+\.\d+", str(target.get("kernel_family", ""))) is not None,
                 f"{target_id}: invalid kernel family")
        boards = target.get("boards")
        _require(
            isinstance(boards, list)
            and bool(boards)
            and all(isinstance(board, str) and board.strip() for board in boards)
            and len(set(boards)) == len(boards),
            f"{target_id}: boards must be a nonempty list of unique names",
        )
        source_url = target.get("source_url")
        _require(
            isinstance(source_url, str)
            and source_url.startswith("https://")
            and urllib.parse.urlparse(source_url).netloc,
            f"{target_id}: source_url must be an HTTPS URL",
        )
        _require(target.get("image") == "ubuntu", f"{target_id}: expected the official Ubuntu image")
        _require(
            target.get("image_repository") == "library/ubuntu",
            f"{target_id}: registry repository must be library/ubuntu",
        )
        _require(target.get("image_tag") == target.get("ubuntu"),
                 f"{target_id}: image tag must match Ubuntu userland version")
        _require(
            isinstance(target.get("image_digest"), str)
            and IMAGE_DIGEST_RE.fullmatch(target["image_digest"]) is not None,
            f"{target_id}: image_digest must be a complete sha256 digest",
        )
        indexed[target_id] = target

    _require(
        tuple(indexed) == EXPECTED_TARGETS,
        f"targets must be exactly {', '.join(EXPECTED_TARGETS)} in that order",
    )
    return indexed


def read_targets(path: Path = TARGETS_PATH) -> Dict[str, Dict[str, Any]]:
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as exc:
        raise JetsonCIError(f"cannot read target metadata at {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise JetsonCIError(f"invalid JSON in {path}: {exc}") from exc
    return validate_targets(data)


def select_matrix(
    targets: Dict[str, Dict[str, Any]], target_choice: str, backend_choice: str
) -> List[Dict[str, Any]]:
    if target_choice == "all":
        selected = list(targets)
    elif target_choice in targets:
        selected = [target_choice]
    else:
        raise JetsonCIError(f"unknown target {target_choice!r}; choose all or {', '.join(targets)}")

    if backend_choice == "both":
        backends = BACKENDS
    elif backend_choice in BACKENDS:
        backends = (backend_choice,)
    else:
        raise JetsonCIError(f"unknown backend {backend_choice!r}; choose both, rsusb, or native")

    matrix = []
    for target_id in selected:
        metadata = targets[target_id]
        for backend in backends:
            matrix.append(
                {
                    "target": target_id,
                    "backend": backend,
                    "jetpack": metadata["jetpack"],
                    "l4t": metadata["l4t"],
                    "ubuntu": metadata["ubuntu"],
                    "kernel_family": metadata["kernel_family"],
                    "image": metadata["image"],
                    "image_tag": metadata["image_tag"],
                    "image_digest": metadata["image_digest"],
                }
            )
    return matrix


def parse_os_release(contents: str) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for line in contents.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        if not re.fullmatch(r"[A-Z0-9_]+", key):
            continue
        try:
            parsed = shlex.split(raw_value, posix=True)
        except ValueError as exc:
            raise JetsonCIError(f"malformed /etc/os-release value for {key}: {exc}") from exc
        if len(parsed) == 1:
            values[key] = parsed[0]
        elif not parsed:
            values[key] = ""
        else:
            values[key] = " ".join(parsed)
    return values


def validate_image_identity(
    config: Dict[str, Any], os_release: Dict[str, str], expected_ubuntu: str
) -> None:
    _require(config.get("architecture") == "arm64", "image config architecture is not arm64")
    _require(config.get("os") == "linux", "image config OS is not linux")
    _require(os_release.get("ID") == "ubuntu", "image layer /etc/os-release is not Ubuntu")
    _require(
        os_release.get("VERSION_ID") == expected_ubuntu,
        f"image layer Ubuntu version is {os_release.get('VERSION_ID')!r}, expected {expected_ubuntu}",
    )


def classify_jetson_kernel(release: str) -> str:
    """Return an unambiguous NVIDIA stock kernel family; reject RT/custom names."""
    if re.search(r"(?:^|[-+._])(?:rt|realtime)(?:$|[-+._])", release.lower()):
        raise JetsonCIError(f"RT kernel {release!r} is ambiguous for a binary family check")
    match = re.fullmatch(r"(?P<family>\d+\.\d+)\.\d+-tegra", release)
    if match is None:
        raise JetsonCIError(
            f"kernel release {release!r} is not an unambiguous stock-style <version>-tegra release"
        )
    return match.group("family")


def parse_nv_tegra_release(contents: str) -> str:
    match = re.search(r"\bR\s*(\d+)\b[^\n]*?\bREVISION\s*:\s*([0-9]+(?:\.[0-9]+)*)", contents)
    if match is None:
        raise JetsonCIError("cannot parse an NVIDIA L4T release from /etc/nv_tegra_release")
    return f"{match.group(1)}.{match.group(2)}"


def evaluate_device(
    target: Dict[str, Any],
    backend: str,
    *,
    architecture: str,
    machine: str,
    os_release: Dict[str, str],
    nv_tegra_release: str,
    kernel_release: str,
) -> List[str]:
    if backend not in BACKENDS:
        raise JetsonCIError(f"unknown backend {backend!r}; choose rsusb or native")
    _require(architecture == "arm64", f"dpkg architecture is {architecture!r}, expected 'arm64'")
    _require(machine in ("aarch64", "arm64"), f"machine is {machine!r}, expected AArch64")
    _require(os_release.get("ID") == "ubuntu", "device userland is not Ubuntu")
    _require(
        os_release.get("VERSION_ID") == target["ubuntu"],
        f"device Ubuntu {os_release.get('VERSION_ID')!r} does not match target {target['ubuntu']}",
    )
    l4t = parse_nv_tegra_release(nv_tegra_release)
    kernel_family = classify_jetson_kernel(kernel_release)
    _require(
        kernel_family == target["kernel_family"],
        f"device kernel family {kernel_family} does not match target {target['kernel_family']}",
    )
    warnings = []
    if l4t != target["l4t"]:
        warnings.append(
            f"Detected nonreference L4T {l4t}; {target['id']} references {target['l4t']}. "
            "A matching kernel family is only a candidate, not exact-release certification."
        )
    if backend == "native":
        warnings.append(
            "Native V4L2 use requires manual verification of exact board kernel config, "
            "Module.symvers, vermagic, and module signing; this check does not certify ABI compatibility."
        )
    return warnings


def backend_cmake_options(backend: str) -> Dict[str, str]:
    if backend not in BACKENDS:
        raise JetsonCIError(f"unknown backend {backend!r}; choose rsusb or native")
    return {
        "CMAKE_BUILD_TYPE": "Release",
        "CMAKE_INSTALL_PREFIX": "/usr/local",
        "CMAKE_INSTALL_LIBDIR": "lib",
        "ENABLE_CCACHE": "OFF",
        "BUILD_SHARED_LIBS": "ON",
        "BUILD_EXAMPLES": "OFF",
        "BUILD_TOOLS": "ON",
        "BUILD_GRAPHICAL_EXAMPLES": "OFF",
        "BUILD_WITH_CUDA": "OFF",
        "BUILD_WITH_CUDA_ZEROCOPY": "OFF",
        "BUILD_PYTHON_BINDINGS": "OFF",
        "BUILD_ROSBAG2": "OFF",
        "BUILD_WITH_DDS": "OFF",
        "BUILD_RS2_ALL": "OFF",
        "BUILD_WITH_CPU_EXTENSIONS": "OFF",
        "BUILD_WITH_NEON": "ON",
        "CHECK_FOR_UPDATES": "OFF",
        "FORCE_RSUSB_BACKEND": "ON" if backend == "rsusb" else "OFF",
    }


def build_manifest(
    targets: Dict[str, Dict[str, Any]],
    *,
    target_id: str,
    backend: str,
    source_sha: str,
    ubuntu_version: str,
    architecture: str,
    host_kernel: str,
    image_launcher: str,
    compiler: str,
    cmake: str,
    fetched_json_commit: str,
    dependency_versions: Dict[str, str],
) -> Dict[str, Any]:
    if target_id not in targets:
        raise JetsonCIError(f"unknown target {target_id!r}")
    if not SHA_RE.fullmatch(source_sha):
        raise JetsonCIError("source SHA must be a lowercase 40- to 64-character hexadecimal hash")
    if backend not in BACKENDS:
        raise JetsonCIError(f"unknown backend {backend!r}")
    if not SHA_RE.fullmatch(fetched_json_commit):
        raise JetsonCIError("fetched nlohmann/json commit must be a lowercase 40- to 64-character hash")
    _require(ubuntu_version == targets[target_id]["ubuntu"], "actual build userland does not match target")
    _require(architecture == "arm64", "actual build architecture must be arm64")
    _require(bool(host_kernel), "host kernel release must be recorded")
    _require(image_launcher in ("workflow", "local-build"), "image launcher must be workflow or local-build")
    _require(bool(compiler) and bool(cmake), "compiler and CMake versions are required")
    metadata = targets[target_id]
    return {
        "schema_version": 1,
        "source_sha": source_sha,
        "target": {
            "id": target_id,
            "jetpack": metadata["jetpack"],
            "l4t_reference": metadata["l4t"],
            "ubuntu_userland": metadata["ubuntu"],
            "kernel_family": metadata["kernel_family"],
            "boards": metadata["boards"],
            "source_url": metadata["source_url"],
        },
        "container_image": {
            "reference": f"{metadata['image']}:{metadata['image_tag']}",
            "launcher_selection": {
                "launcher": image_launcher,
                "configured_arm64_manifest_digest": metadata["image_digest"],
                "verification": (
                    "The workflow pulled this exact digest after metadata validation."
                    if image_launcher == "workflow"
                    else "The local-build launcher verified the registry manifest and pulled this exact digest."
                ),
            },
            "in_container_observation": {
                "os": "linux",
                "architecture": architecture,
                "ubuntu_userland": ubuntu_version,
                "image_digest": None,
                "note": (
                    "The container can observe its userland and architecture, but cannot independently "
                    "observe the Docker image digest or the target Jetson BSP/kernel."
                ),
            },
        },
        "build_environment": {
            "userland": {
                "id": "ubuntu",
                "version_id": ubuntu_version,
                "architecture": architecture,
            },
            "host_kernel": {
                "release": host_kernel,
                "note": "The build container shares this host kernel; this is not the target device kernel.",
            },
        },
        "backend": backend,
        "cmake_options": backend_cmake_options(backend),
        "toolchain": {"compiler": compiler, "cmake": cmake},
        "fetched_dependencies": {"nlohmann_json": {"git_commit": fetched_json_commit}},
        "dependency_versions": dict(sorted(dependency_versions.items())),
        "reproducibility": (
            "Source and Ubuntu ARM64 image digests are pinned. Resolved apt dependency versions are "
            "recorded but not locked; byte-for-byte reproducibility is not claimed."
        ),
        "hardware_validation": "not performed",
    }


def _request(url: str, headers: Optional[Dict[str, str]] = None, *, limit: int = 512 * 1024 * 1024) -> Tuple[bytes, Any]:
    request_headers = {"User-Agent": USER_AGENT}
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(url, headers=request_headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > limit:
                raise JetsonCIError(f"registry response at {url} exceeds {limit} bytes")
            body = response.read(limit + 1)
            if len(body) > limit:
                raise JetsonCIError(f"registry response at {url} exceeds {limit} bytes")
            return body, response.headers
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise JetsonCIError(f"registry request failed for {url}: {exc}") from exc


class DockerHubClient:
    def __init__(self, repository: str):
        token_query = urllib.parse.urlencode(
            {"service": "registry.docker.io", "scope": f"repository:{repository}:pull"}
        )
        token_body, _ = _request(f"{DOCKER_AUTH}?{token_query}")
        try:
            token_data = json.loads(token_body)
            self.token = token_data.get("token") or token_data["access_token"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise JetsonCIError("Docker Hub did not return an anonymous pull token") from exc
        self.repository = repository

    def get(self, path: str, accept: Optional[str] = None) -> Tuple[bytes, Any]:
        headers = {"Authorization": f"Bearer {self.token}"}
        if accept:
            headers["Accept"] = accept
        return _request(f"https://{REGISTRY}/v2/{self.repository}/{path}", headers)

    @staticmethod
    def verify_digest(body: bytes, expected: str, description: str) -> None:
        actual = "sha256:" + hashlib.sha256(body).hexdigest()
        _require(actual == expected, f"{description} content hash is {actual}, expected {expected}")

    def read_os_release_from_layers(self, layers: List[Dict[str, Any]]) -> Dict[str, str]:
        for layer in reversed(layers):
            digest = layer.get("digest")
            _require(isinstance(digest, str) and IMAGE_DIGEST_RE.fullmatch(digest) is not None,
                     "image layer has a malformed sha256 digest")
            body, _ = self.get(f"blobs/{digest}")
            self.verify_digest(body, digest, f"image layer {digest}")
            try:
                with tarfile.open(fileobj=io.BytesIO(body), mode="r:*") as archive:
                    names = {member.name.lstrip("./"): member for member in archive.getmembers()}
                    if "etc/.wh.os-release" in names or "etc/.wh..wh..opq" in names:
                        raise JetsonCIError("image layers mask /etc/os-release")
                    member = names.get("etc/os-release")
                    if member is not None:
                        stream = archive.extractfile(member)
                        _require(stream is not None, "cannot read /etc/os-release from image layer")
                        return parse_os_release(stream.read().decode("utf-8"))
            except (tarfile.TarError, UnicodeDecodeError) as exc:
                raise JetsonCIError(f"cannot inspect Ubuntu image layer {digest}: {exc}") from exc
        raise JetsonCIError("image layers do not contain /etc/os-release")

    def verify_target(self, target: Dict[str, Any]) -> str:
        tag_body, tag_headers = self.get(
            f"manifests/{target['image_tag']}", DOCKER_MANIFEST_ACCEPT
        )
        tag_document = self._decode_manifest(tag_body, "tag manifest")
        tag_digest = tag_headers.get("Docker-Content-Digest")
        if tag_digest:
            self.verify_digest(tag_body, tag_digest, f"{target['image_tag']} tag manifest")

        if "manifests" in tag_document:
            descriptors = [
                descriptor
                for descriptor in tag_document["manifests"]
                if descriptor.get("platform", {}).get("os") == "linux"
                and descriptor.get("platform", {}).get("architecture") == "arm64"
                and descriptor.get("platform", {}).get("variant", "v8") in ("", "v8")
            ]
            _require(len(descriptors) == 1, f"{target['image']}:{target['image_tag']} has no unique linux/arm64 manifest")
            resolved_digest = descriptors[0].get("digest")
        else:
            resolved_digest = tag_digest
        _require(
            resolved_digest == target["image_digest"],
            f"{target['id']}: tag resolves to ARM64 digest {resolved_digest!r}, "
            f"not pinned digest {target['image_digest']}",
        )

        manifest_body, manifest_headers = self.get(
            f"manifests/{target['image_digest']}", DOCKER_MANIFEST_ACCEPT
        )
        self.verify_digest(manifest_body, target["image_digest"], f"{target['id']} ARM64 manifest")
        header_digest = manifest_headers.get("Docker-Content-Digest")
        if header_digest:
            _require(header_digest == target["image_digest"], f"{target['id']}: registry digest header mismatch")
        manifest = self._decode_manifest(manifest_body, "ARM64 image manifest")
        _require("config" in manifest and isinstance(manifest.get("layers"), list),
                 f"{target['id']}: digest does not identify a platform image manifest")

        config_digest = manifest["config"].get("digest")
        _require(isinstance(config_digest, str) and IMAGE_DIGEST_RE.fullmatch(config_digest) is not None,
                 f"{target['id']}: config digest is malformed")
        config_body, _ = self.get(f"blobs/{config_digest}")
        self.verify_digest(config_body, config_digest, f"{target['id']} image config")
        try:
            config = json.loads(config_body)
        except json.JSONDecodeError as exc:
            raise JetsonCIError(f"{target['id']}: invalid image config JSON: {exc}") from exc
        os_release = self.read_os_release_from_layers(manifest["layers"])
        validate_image_identity(config, os_release, target["ubuntu"])
        return (
            f"{target['id']}: {target['image']}:{target['image_tag']}@{target['image_digest']} "
            f"config=linux/arm64 userland=Ubuntu {os_release['VERSION_ID']}"
        )

    @staticmethod
    def _decode_manifest(body: bytes, description: str) -> Dict[str, Any]:
        try:
            document = json.loads(body)
        except json.JSONDecodeError as exc:
            raise JetsonCIError(f"invalid {description} JSON: {exc}") from exc
        _require(isinstance(document, dict), f"{description} must be a JSON object")
        return document


def _read_system_file(path: Path, description: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise JetsonCIError(f"cannot read {description} at {path}: {exc}") from exc


def _dpkg_architecture() -> str:
    try:
        result = subprocess.run(
            ["dpkg", "--print-architecture"],
            check=True,
            universal_newlines=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise JetsonCIError(f"cannot determine dpkg architecture: {exc}") from exc
    return result.stdout.strip()


def preflight_build(
    target: Dict[str, Any],
    *,
    expected_ubuntu: str,
    expected_image_digest: str,
    image_launcher: str,
) -> Dict[str, Any]:
    _require(UBUNTU_RE.fullmatch(expected_ubuntu) is not None,
             "launcher Ubuntu version is missing or malformed")
    _require(IMAGE_DIGEST_RE.fullmatch(expected_image_digest) is not None,
             "launcher image digest is missing or malformed")
    _require(expected_ubuntu == target["ubuntu"], "launcher Ubuntu version does not match target metadata")
    _require(
        expected_image_digest == target["image_digest"],
        "launcher image digest does not match configured target digest",
    )
    _require(image_launcher in ("workflow", "local-build"), "image launcher must be workflow or local-build")
    os_release = parse_os_release(_read_system_file(Path("/etc/os-release"), "/etc/os-release"))
    machine = platform.machine().lower()
    architecture = _dpkg_architecture()
    _require(machine in ("aarch64", "arm64"), f"build container machine is {machine!r}, expected AArch64")
    _require(architecture == "arm64", f"build container dpkg architecture is {architecture!r}, expected arm64")
    _require(os_release.get("ID") == "ubuntu", "build container userland is not Ubuntu")
    _require(os_release.get("VERSION_ID") == expected_ubuntu,
             f"build container Ubuntu {os_release.get('VERSION_ID')!r} does not match launcher selection {expected_ubuntu}")
    return {
        "target": target["id"],
        "image_digest": expected_image_digest,
        "image_launcher": image_launcher,
        "os_release": os_release,
        "architecture": architecture,
        "machine": machine,
    }


def _device_check(target: Dict[str, Any], backend: str) -> int:
    os_release = parse_os_release(_read_system_file(Path("/etc/os-release"), "/etc/os-release"))
    architecture = _dpkg_architecture()
    machine = platform.machine().lower()
    l4t_content = _read_system_file(Path("/etc/nv_tegra_release"), "/etc/nv_tegra_release")
    kernel_release = platform.uname().release
    try:
        device_model = Path("/proc/device-tree/model").read_bytes().decode("utf-8").strip("\x00\n")
    except (OSError, UnicodeDecodeError):
        device_model = "unavailable"
    warnings = evaluate_device(
        target,
        backend,
        architecture=architecture,
        machine=machine,
        os_release=os_release,
        nv_tegra_release=l4t_content,
        kernel_release=kernel_release,
    )
    l4t = parse_nv_tegra_release(l4t_content)
    kernel_family = classify_jetson_kernel(kernel_release)
    print(
        f"Detected: model={device_model!r} dpkg_architecture={architecture} machine={machine} "
        f"userland={os_release.get('ID')} {os_release.get('VERSION_ID')} "
        f"L4T={l4t} device_kernel={kernel_release} kernel_family={kernel_family}"
    )
    print(
        f"Selected: target={target['id']} JetPack={target['jetpack']} "
        f"reference_L4T={target['l4t']} target_kernel_family={target['kernel_family']} backend={backend}"
    )
    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    print("This read-only check does not install patches or certify native kernel ABI compatibility.")
    return 0


def _parse_dependency_versions(value: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for line in value.splitlines():
        if not line.strip():
            continue
        if "=" not in line:
            raise JetsonCIError(f"malformed dependency version line {line!r}")
        name, version = line.split("=", 1)
        if not name or not version or name in result:
            raise JetsonCIError(f"invalid or duplicate dependency version {line!r}")
        result[name] = version
    _require(bool(result), "at least one dependency version must be recorded")
    return result


def write_checksum(archive: Path) -> Path:
    if not archive.is_file():
        raise JetsonCIError(f"cannot checksum missing archive {archive}")
    digest = hashlib.sha256()
    try:
        with archive.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        checksum_path = archive.with_name(archive.name + ".sha256")
        checksum_path.write_text(f"{digest.hexdigest()}  {archive.name}\n", encoding="utf-8")
    except OSError as exc:
        raise JetsonCIError(f"cannot write checksum for {archive}: {exc}") from exc
    return checksum_path


def _checked_output(command: List[str], description: str) -> str:
    try:
        result = subprocess.run(
            command,
            check=True,
            universal_newlines=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise JetsonCIError(f"{description} failed: {exc}") from exc
    return result.stdout.strip()


def local_build(target_id: str, backend: str, targets: Dict[str, Dict[str, Any]]) -> int:
    """Build locally only after verifying a clean native ARM64 host and pinned image."""
    if target_id not in targets:
        raise JetsonCIError(f"unknown target {target_id!r}")
    if backend not in BACKENDS:
        raise JetsonCIError(f"unknown backend {backend!r}; choose rsusb or native")

    machine = platform.machine().lower()
    _require(machine in ("aarch64", "arm64"), f"local-build requires native ARM64, found machine {machine!r}")
    architecture = _dpkg_architecture()
    _require(architecture == "arm64", f"local-build requires dpkg architecture arm64, found {architecture!r}")

    repository_root = SCRIPT_DIR.parents[1].resolve()
    source_sha = _checked_output(
        ["git", "-c", f"safe.directory={repository_root}", "-C", str(repository_root), "rev-parse", "HEAD"],
        "source SHA discovery",
    )
    _require(SHA_RE.fullmatch(source_sha) is not None, "Git returned an invalid source SHA")
    working_tree = _checked_output(
        ["git", "-c", f"safe.directory={repository_root}", "-C", str(repository_root),
         "status", "--porcelain", "--untracked-files=all", "--", ".", ":!jetson-dist", ":!jetson-logs"],
        "source working-tree check",
    )
    _require(not working_tree, "local-build requires a clean checkout so the source SHA identifies the built source")

    target = targets[target_id]
    image_reference = f"{target['image_repository']}@{target['image_digest']}"
    print(DockerHubClient(target["image_repository"]).verify_target(target))
    try:
        subprocess.run(["docker", "pull", "--platform", "linux/arm64", image_reference], check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise JetsonCIError(f"pinned ARM64 Docker pull failed: {exc}") from exc

    image_identity = _checked_output(
        ["docker", "image", "inspect", "--format", "{{.Os}} {{.Architecture}}", image_reference],
        "Docker image inspection",
    ).split()
    _require(image_identity == ["linux", "arm64"],
             f"pulled Docker image identity is {' '.join(image_identity)!r}, expected 'linux arm64'")

    docker_run = [
        "docker", "run", "--platform", "linux/arm64", "--rm",
        "--mount", f"type=bind,src={repository_root},dst=/workspace",
        "--workdir", "/workspace",
        "--env", f"JETSON_TARGET={target_id}",
        "--env", f"JETSON_BACKEND={backend}",
        "--env", f"JETSON_EXPECTED_UBUNTU_VERSION={target['ubuntu']}",
        "--env", f"JETSON_IMAGE_DIGEST={target['image_digest']}",
        "--env", "JETSON_IMAGE_LAUNCHER=local-build",
        "--env", f"SOURCE_SHA={source_sha}",
        image_reference,
        "bash", "scripts/jetson/build.sh", target_id, backend,
    ]
    try:
        subprocess.run(docker_run, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise JetsonCIError(f"local ARM64 Docker build failed: {exc}") from exc
    return 0


def _archive_member_path(member: tarfile.TarInfo) -> Tuple[str, ...]:
    name = member.name
    _require(bool(name) and not name.startswith("/"), f"archive contains an empty or absolute path {name!r}")
    _require("\\" not in name and "\x00" not in name, f"archive path is ambiguous: {name!r}")
    if member.isdir():
        name = name.rstrip("/")
    if name in (".", "./"):
        parts: Tuple[str, ...] = ()
    else:
        if name.startswith("./"):
            name = name[2:]
        raw_parts = name.split("/")
        _require(
            all(part not in ("", ".", "..") for part in raw_parts),
            f"archive contains an empty, dot, or traversing path component in {member.name!r}",
        )
        parts = tuple(raw_parts)

    if parts in ((), ("usr",)):
        _require(member.isdir(), f"only real ancestor directories may be outside usr/local: {member.name!r}")
    else:
        _require(parts[:2] == ("usr", "local"), f"archive path is outside usr/local: {member.name!r}")
    _require(not (member.mode & 0o6000), f"archive member has set-ID permissions: {member.name!r}")
    _require(
        member.isdir() or member.isreg() or member.issym(),
        f"archive member has a hardlink or unsupported special type: {member.name!r}",
    )
    return parts


def _resolve_archive_symlink(
    components: List[str], symlinks: Dict[Tuple[str, ...], str], seen: Optional[set] = None
) -> Tuple[str, ...]:
    visited = set() if seen is None else set(seen)
    stack: List[str] = []
    for position, component in enumerate(components):
        if component in ("", "."):
            continue
        if component == "..":
            _require(len(stack) > 2, "archive symlink escapes the usr/local install tree")
            stack.pop()
            continue
        stack.append(component)
        current = tuple(stack)
        if current in symlinks:
            _require(current not in visited, f"archive contains a symlink cycle at {'/'.join(current)}")
            target = symlinks[current]
            _require(bool(target) and not target.startswith("/"),
                     f"archive symlink has an empty or absolute target at {'/'.join(current)}")
            _require("\\" not in target and "\x00" not in target,
                     f"archive symlink target is ambiguous at {'/'.join(current)}")
            target_parts = target.split("/")
            _require(all(part != "" for part in target_parts),
                     f"archive symlink target has an empty path component at {'/'.join(current)}")
            return _resolve_archive_symlink(
                stack[:-1] + target_parts + components[position + 1:],
                symlinks,
                visited | {current},
            )
    resolved = tuple(stack)
    _require(resolved[:2] == ("usr", "local"), "archive symlink resolves outside usr/local")
    return resolved


def _validate_archive_members(archive: tarfile.TarFile) -> Dict[Tuple[str, ...], tarfile.TarInfo]:
    members: Dict[Tuple[str, ...], tarfile.TarInfo] = {}
    symlinks: Dict[Tuple[str, ...], str] = {}
    for member in archive.getmembers():
        path = _archive_member_path(member)
        _require(path not in members, f"archive contains duplicate normalized path {member.name!r}")
        members[path] = member
        if member.issym():
            symlinks[path] = member.linkname

    for path, member in members.items():
        for length in range(1, len(path)):
            parent = members.get(path[:length])
            if parent is not None:
                _require(parent.isdir(), f"archive member {member.name!r} is beneath a non-directory member")
    for path, target in symlinks.items():
        _require(bool(target) and not target.startswith("/"),
                 f"archive symlink has an empty or absolute target at {'/'.join(path)}")
        _require("\\" not in target and "\x00" not in target,
                 f"archive symlink target is ambiguous at {'/'.join(path)}")
        _resolve_archive_symlink(list(path[:-1]) + target.split("/"), symlinks)
    return members


def verify_archive(
    archive_path: Path,
    checksum_path: Path,
    target_id: str,
    backend: str,
    targets: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    if target_id not in targets:
        raise JetsonCIError(f"unknown target {target_id!r}")
    if backend not in BACKENDS:
        raise JetsonCIError(f"unknown backend {backend!r}; choose rsusb or native")
    _require(archive_path.is_file(), f"archive does not exist or is not a regular file: {archive_path}")
    _require(checksum_path.is_file(), f"checksum sidecar does not exist or is not a regular file: {checksum_path}")
    try:
        checksum_text = checksum_path.read_text(encoding="utf-8")
        checksum_lines = checksum_text.splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise JetsonCIError(f"cannot read checksum sidecar {checksum_path}: {exc}") from exc
    _require(len(checksum_lines) == 1, "checksum sidecar must contain exactly one checksum entry")
    checksum_match = re.fullmatch(r"([0-9a-f]{64})  ([^\r\n]+)", checksum_lines[0])
    _require(checksum_match is not None, "checksum sidecar must use '<sha256><two spaces><archive basename>' format")
    _require(checksum_match.group(2) == archive_path.name,
             f"checksum entry names {checksum_match.group(2)!r}, not requested archive {archive_path.name!r}")
    digest = hashlib.sha256()
    try:
        with archive_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise JetsonCIError(f"cannot read archive {archive_path}: {exc}") from exc
    _require(digest.hexdigest() == checksum_match.group(1), "archive SHA-256 does not match checksum sidecar")

    try:
        with tarfile.open(str(archive_path), mode="r:gz") as archive:
            members = _validate_archive_members(archive)
            manifest_path = ("usr", "local", "share", "doc", "librealsense2", "jetson-build-manifest.json")
            manifest_member = members.get(manifest_path)
            _require(manifest_member is not None and manifest_member.isreg(),
                     "archive is missing the regular embedded Jetson build manifest")
            stream = archive.extractfile(manifest_member)
            _require(stream is not None, "cannot read embedded Jetson build manifest")
            try:
                manifest = json.loads(stream.read().decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise JetsonCIError(f"embedded Jetson build manifest is invalid: {exc}") from exc
    except (OSError, tarfile.TarError) as exc:
        raise JetsonCIError(f"cannot inspect SDK archive {archive_path}: {exc}") from exc

    _require(isinstance(manifest, dict) and manifest.get("schema_version") == 1,
             "embedded Jetson build manifest has an unsupported schema")
    _require(isinstance(manifest.get("source_sha"), str) and SHA_RE.fullmatch(manifest["source_sha"]) is not None,
             "embedded Jetson build manifest has an invalid source SHA")
    manifest_target = manifest.get("target")
    _require(isinstance(manifest_target, dict), "embedded Jetson build manifest target is invalid")
    _require(manifest_target.get("id") == target_id,
             f"embedded manifest target does not match selected target {target_id}")
    _require(manifest.get("backend") == backend,
             f"embedded manifest backend does not match selected backend {backend}")
    image = manifest.get("container_image")
    _require(isinstance(image, dict), "embedded Jetson build manifest image selection is invalid")
    _require(image.get("reference") ==
             f"{targets[target_id]['image']}:{targets[target_id]['image_tag']}",
             "embedded manifest image reference does not match selected target")
    selection = image.get("launcher_selection")
    _require(isinstance(selection, dict), "embedded manifest launcher selection is invalid")
    _require(selection.get("launcher") in ("workflow", "local-build"),
             "embedded manifest does not identify an approved image launcher")
    _require(selection.get("configured_arm64_manifest_digest") == targets[target_id]["image_digest"],
             "embedded manifest image digest does not match selected target")
    observed = image.get("in_container_observation")
    _require(isinstance(observed, dict), "embedded manifest in-container observation is invalid")
    _require(observed.get("os") == "linux" and observed.get("architecture") == "arm64",
             "embedded manifest in-container OS/architecture observation is invalid")
    _require(observed.get("ubuntu_userland") == targets[target_id]["ubuntu"],
             "embedded manifest Ubuntu userland does not match selected target")
    _require(observed.get("image_digest") is None,
             "embedded manifest must not claim an independently observed in-container image digest")
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("validate", help="validate target metadata without network access")

    matrix_parser = subparsers.add_parser("matrix", help="print an allowlisted build matrix as JSON")
    matrix_parser.add_argument("--target", required=True, help="all or one of jp4, jp5, jp6, jp7")
    matrix_parser.add_argument("--backend", required=True, help="both, rsusb, or native")

    subparsers.add_parser("verify-images", help="verify pinned Ubuntu ARM64 registry manifests and layers")

    preflight_parser = subparsers.add_parser("preflight", help=argparse.SUPPRESS)
    preflight_parser.add_argument("--target", required=True)
    preflight_parser.add_argument("--expected-ubuntu-version", required=True)
    preflight_parser.add_argument("--expected-image-digest", required=True)
    preflight_parser.add_argument("--image-launcher", required=True, choices=("workflow", "local-build"))

    device_parser = subparsers.add_parser("device-check", help=argparse.SUPPRESS)
    device_parser.add_argument("--target", required=True)
    device_parser.add_argument("--backend", required=True)

    manifest_parser = subparsers.add_parser("write-manifest", help=argparse.SUPPRESS)
    manifest_parser.add_argument("--target", required=True)
    manifest_parser.add_argument("--backend", required=True)
    manifest_parser.add_argument("--source-sha", required=True)
    manifest_parser.add_argument("--ubuntu-version", required=True)
    manifest_parser.add_argument("--architecture", required=True)
    manifest_parser.add_argument("--host-kernel", required=True)
    manifest_parser.add_argument("--image-launcher", required=True, choices=("workflow", "local-build"))
    manifest_parser.add_argument("--compiler", required=True)
    manifest_parser.add_argument("--cmake", required=True)
    manifest_parser.add_argument("--fetched-json-commit", required=True)
    manifest_parser.add_argument("--dependency-versions", required=True)
    manifest_parser.add_argument("--output", required=True, type=Path)

    checksum_parser = subparsers.add_parser("checksum", help=argparse.SUPPRESS)
    checksum_parser.add_argument("--archive", required=True, type=Path)

    local_build_parser = subparsers.add_parser(
        "local-build", help="build on a native ARM64 host using the verified pinned Ubuntu image"
    )
    local_build_parser.add_argument("--target", required=True, help="one of jp4, jp5, jp6, jp7")
    local_build_parser.add_argument("--backend", required=True, help="rsusb or native")

    archive_parser = subparsers.add_parser(
        "verify-archive", help="verify an SDK archive checksum, paths, symlinks, and embedded manifest"
    )
    archive_parser.add_argument("--archive", required=True, type=Path)
    archive_parser.add_argument("--checksum", required=True, type=Path)
    archive_parser.add_argument("--target", required=True, help="one of jp4, jp5, jp6, jp7")
    archive_parser.add_argument("--backend", required=True, help="rsusb or native")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.error("a command is required")
    try:
        targets = read_targets()
        if args.command == "validate":
            for target_id, target in targets.items():
                print(
                    f"{target_id}: JetPack {target['jetpack']}, L4T {target['l4t']}, "
                    f"Ubuntu {target['ubuntu']}, kernel family {target['kernel_family']}, "
                    f"ARM64 image {target['image_digest']}"
                )
            print(f"Validated {len(targets)} reference targets.")
            return 0
        if args.command == "matrix":
            print(json.dumps(select_matrix(targets, args.target, args.backend), separators=(",", ":")))
            return 0
        if args.command == "verify-images":
            for target in targets.values():
                print(DockerHubClient(target["image_repository"]).verify_target(target))
            print(f"Verified {len(targets)} pinned Ubuntu ARM64 image manifests and userlands.")
            return 0
        if args.command == "checksum":
            print(write_checksum(args.archive))
            return 0
        if args.command == "local-build":
            return local_build(args.target, args.backend, targets)
        if args.command == "verify-archive":
            manifest = verify_archive(args.archive, args.checksum, args.target, args.backend, targets)
            print(
                f"Verified {args.archive.name}: target={args.target} backend={args.backend} "
                f"source_sha={manifest['source_sha']}"
            )
            return 0
        if args.target not in targets:
            raise JetsonCIError(f"unknown target {args.target!r}")
        target = targets[args.target]
        if args.command == "preflight":
            print(json.dumps(preflight_build(
                target,
                expected_ubuntu=args.expected_ubuntu_version,
                expected_image_digest=args.expected_image_digest,
                image_launcher=args.image_launcher,
            ), separators=(",", ":")))
            return 0
        if args.command == "device-check":
            return _device_check(target, args.backend)
        if args.command == "write-manifest":
            manifest = build_manifest(
                targets,
                target_id=args.target,
                backend=args.backend,
                source_sha=args.source_sha,
                ubuntu_version=args.ubuntu_version,
                architecture=args.architecture,
                host_kernel=args.host_kernel,
                image_launcher=args.image_launcher,
                compiler=args.compiler,
                cmake=args.cmake,
                fetched_json_commit=args.fetched_json_commit,
                dependency_versions=_parse_dependency_versions(args.dependency_versions),
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(args.output)
            return 0
        raise JetsonCIError(f"unsupported command {args.command!r}")
    except JetsonCIError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
