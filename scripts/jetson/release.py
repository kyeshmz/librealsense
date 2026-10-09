#!/usr/bin/env python3
"""Prepare and safely publish a verified Jetson SDK prerelease."""

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import jetson_ci


ROOT = Path(__file__).resolve().parents[2]
VERSION_HEADER = ROOT / "include/librealsense2/rs.h"
API_BASE = "https://api.github.com/repos/kyeshmz/librealsense"
API_PREFIX = "/repos/kyeshmz/librealsense/"
OWNER = "kyeshmz"
REPOSITORY = "librealsense"
API_VERSION = "2026-03-10"
USER_AGENT = "librealsense-jetson-release/1"
MAX_API_RESPONSE = 8 * 1024 * 1024
MAX_RELEASE_LIST_PAGES = 100
TARGET_IDS = ("jp4", "jp5", "jp6", "jp7")
BACKENDS = ("rsusb", "native")
SOURCE_SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")
TAG_RE = re.compile(r"^jetson-v(?P<version>\d+\.\d+\.\d+)-ci\.(?P<sequence>[1-9]\d*)$")
VERSION_MACROS = (
    "RS2_API_MAJOR_VERSION",
    "RS2_API_MINOR_VERSION",
    "RS2_API_PATCH_VERSION",
)


class ReleaseError(Exception):
    """Validation or publication error suitable for a sanitized CLI message."""


class GitHubAPIError(ReleaseError):
    def __init__(self, status: int, method: str, url: str):
        self.status = status
        self.method = method
        path = urllib.parse.urlsplit(url).path
        if status == 403:
            detail = "check the release job's contents:write permission and fork policy"
        elif status == 404 and (
            (method == "POST" and path == "/repos/kyeshmz/librealsense/releases")
            or (method == "PATCH" and re.fullmatch(
                r"/repos/kyeshmz/librealsense/releases/\d+", path
            ) is not None)
        ):
            detail = "check contents:write and ensure workflow changes are merged into the default branch"
        elif status in (301, 302, 303, 307, 308):
            detail = "redirects are refused to prevent credential forwarding"
        else:
            detail = "response body omitted"
        super().__init__(f"GitHub API {method} {path} failed with HTTP {status}; {detail}")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ReleaseError(message)


def read_sdk_version(header_path: Path = VERSION_HEADER) -> str:
    try:
        text = header_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ReleaseError(f"cannot read SDK version header at {header_path}: {exc}") from exc

    values = []
    for macro in VERSION_MACROS:
        matches = re.findall(
            r"(?m)^\s*#\s*define\s+" + re.escape(macro) + r"\s+([0-9]+)\s*(?:/\*.*?\*/)?\s*$",
            text,
        )
        _require(len(matches) == 1, f"rs.h must define {macro} exactly once as an integer")
        values.append(matches[0])
    return ".".join(values)


def validate_tag(tag: str, sdk_version: str) -> None:
    match = TAG_RE.fullmatch(tag)
    _require(match is not None, "tag must match jetson-vMAJOR.MINOR.PATCH-ci.POSITIVE_INTEGER")
    _require(match.group("version") == sdk_version,
             f"tag SDK version {match.group('version')} does not match rs.h version {sdk_version}")


def validate_source_sha(source_sha: str) -> None:
    _require(
        isinstance(source_sha, str) and SOURCE_SHA_RE.fullmatch(source_sha) is not None,
        "source SHA must be a lowercase 40- to 64-character hexadecimal hash",
    )


def _read_json(path: Path, description: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"cannot read {description} {path.name}: {exc}") from exc


def _regular_files(directory: Path) -> Dict[str, Path]:
    _require(not directory.is_symlink(), "asset directory must not be a symlink")
    _require(directory.is_dir(), f"asset directory does not exist or is not a directory: {directory}")
    result: Dict[str, Path] = {}
    try:
        for path in directory.iterdir():
            _require(not path.is_symlink(), f"asset directory contains a symlink: {path.name}")
            try:
                mode = path.lstat().st_mode
            except OSError as exc:
                raise ReleaseError(f"cannot inspect release asset {path.name}: {exc}") from exc
            _require(stat.S_ISREG(mode), f"asset directory contains a non-regular file: {path.name}")
            _require(path.name not in result, f"asset directory contains a duplicate name: {path.name}")
            result[path.name] = path
    except OSError as exc:
        raise ReleaseError(f"cannot list asset directory {directory}: {exc}") from exc
    return result


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ReleaseError(f"cannot read release asset {path.name}: {exc}") from exc
    return digest.hexdigest()


def _sha256_bytes(contents: bytes) -> str:
    return hashlib.sha256(contents).hexdigest()


def _package_names(source_sha: str) -> List[str]:
    prefix = source_sha[:12]
    names = []
    for target_id in TARGET_IDS:
        for backend in BACKENDS:
            archive = f"librealsense-jetson-{target_id}-{backend}-{prefix}.tar.gz"
            names.extend((archive, archive + ".sha256", archive[:-7] + ".json"))
    return sorted(names)


def _check_exact_names(actual: List[str], expected: List[str], description: str) -> None:
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    if missing or extra:
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if extra:
            details.append("unexpected: " + ", ".join(extra))
        raise ReleaseError(f"{description} does not match the required asset set ({'; '.join(details)})")


def _package_record(
    files: Dict[str, Path],
    target_id: str,
    backend: str,
    source_sha: str,
    targets: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    prefix = source_sha[:12]
    archive_name = f"librealsense-jetson-{target_id}-{backend}-{prefix}.tar.gz"
    checksum_name = archive_name + ".sha256"
    manifest_name = archive_name[:-7] + ".json"
    archive_path = files[archive_name]
    checksum_path = files[checksum_name]
    manifest_path = files[manifest_name]

    try:
        manifest = jetson_ci.verify_archive(archive_path, checksum_path, target_id, backend, targets)
    except jetson_ci.JetsonCIError as exc:
        raise ReleaseError(f"{archive_name}: {exc}") from exc
    sidecar = _read_json(manifest_path, "JSON manifest sidecar")
    _require(isinstance(sidecar, dict), f"{manifest_name} must contain a JSON object")
    _require(sidecar == manifest, f"{manifest_name} differs from the archive's embedded manifest")
    _require(manifest.get("source_sha") == source_sha,
             f"{archive_name} source SHA does not match the tagged source")
    _require(manifest.get("backend") == backend,
             f"{archive_name} embedded backend does not match its package name")
    _require(manifest.get("cmake_options") == jetson_ci.backend_cmake_options(backend),
             f"{archive_name} backend/build options do not match the selected backend")

    metadata = targets[target_id]
    target_info = manifest.get("target")
    _require(isinstance(target_info, dict), f"{archive_name} has invalid target metadata")
    expected_target = {
        "id": target_id,
        "jetpack": metadata["jetpack"],
        "l4t_reference": metadata["l4t"],
        "ubuntu_userland": metadata["ubuntu"],
        "kernel_family": metadata["kernel_family"],
        "boards": metadata["boards"],
        "source_url": metadata["source_url"],
    }
    for key, expected_value in expected_target.items():
        _require(target_info.get(key) == expected_value,
                 f"{archive_name} target metadata field {key} does not match configured {target_id}")

    image = manifest.get("container_image")
    _require(isinstance(image, dict), f"{archive_name} has invalid image metadata")
    _require(image.get("reference") == f"{metadata['image']}:{metadata['image_tag']}",
             f"{archive_name} image reference does not match configured {target_id}")
    selection = image.get("launcher_selection")
    _require(isinstance(selection, dict), f"{archive_name} has invalid image digest metadata")
    _require(selection.get("configured_arm64_manifest_digest") == metadata["image_digest"],
             f"{archive_name} image digest does not match configured {target_id}")

    return {
        "target": target_id,
        "backend": backend,
        "jetpack": metadata["jetpack"],
        "l4t_reference": metadata["l4t"],
        "ubuntu_userland": metadata["ubuntu"],
        "kernel_family": metadata["kernel_family"],
        "source_sha": source_sha,
        "image": {
            "reference": image["reference"],
            "arm64_manifest_digest": selection["configured_arm64_manifest_digest"],
        },
        "archive": {
            "name": archive_name,
            "size": archive_path.stat().st_size,
            "sha256": _sha256_path(archive_path),
        },
        "checksum": {
            "name": checksum_name,
            "size": checksum_path.stat().st_size,
            "sha256": _sha256_path(checksum_path),
        },
        "manifest": {
            "name": manifest_name,
            "size": manifest_path.stat().st_size,
            "sha256": _sha256_path(manifest_path),
        },
    }


def _validate_packages(
    files: Dict[str, Path],
    source_sha: str,
) -> List[Dict[str, Any]]:
    targets = jetson_ci.read_targets()
    expected_names = _package_names(source_sha)
    package_files = [name for name in files if name not in ("release-index.json", "SHA256SUMS")]
    _check_exact_names(package_files, expected_names, "SDK package directory")

    records = []
    for target_id in TARGET_IDS:
        for backend in BACKENDS:
            records.append(
                _package_record(files, target_id, backend, source_sha, targets)
            )
    return records


def _index_bytes(tag: str, source_sha: str, sdk_version: str,
                 packages: List[Dict[str, Any]]) -> bytes:
    document = {
        "schema_version": 1,
        "tag": tag,
        "source_sha": source_sha,
        "sdk_version": sdk_version,
        "packages": packages,
    }
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _sha256sums_bytes(files: Dict[str, Path], index_contents: bytes) -> bytes:
    digests = {name: _sha256_path(path) for name, path in files.items()
               if name not in ("release-index.json", "SHA256SUMS")}
    digests["release-index.json"] = _sha256_bytes(index_contents)
    lines = [f"{digests[name]}  {name}\n" for name in sorted(digests)]
    return "".join(lines).encode("ascii")


def _expected_release_names(source_sha: str) -> List[str]:
    return sorted(_package_names(source_sha) + ["release-index.json", "SHA256SUMS"])


def _validate_prepared_assets(
    directory: Path,
    tag: str,
    source_sha: str,
    sdk_version: str,
) -> List[Dict[str, Any]]:
    files = _regular_files(directory)
    _check_exact_names(sorted(files), _expected_release_names(source_sha), "prepared release directory")
    packages = _validate_packages(files, source_sha)
    expected_index = _index_bytes(tag, source_sha, sdk_version, packages)
    try:
        actual_index = files["release-index.json"].read_bytes()
        actual_sums = files["SHA256SUMS"].read_bytes()
    except OSError as exc:
        raise ReleaseError(f"cannot read prepared release metadata: {exc}") from exc
    _require(actual_index == expected_index,
             "release-index.json does not match the verified package metadata")
    expected_sums = _sha256sums_bytes(files, expected_index)
    _require(actual_sums == expected_sums,
             "SHA256SUMS does not cover the exact verified package files and release index")
    return packages


def prepare_assets(
    directory: Path,
    tag: str,
    source_sha: str,
    sdk_version: Optional[str] = None,
) -> List[Dict[str, Any]]:
    validate_source_sha(source_sha)
    version = sdk_version or read_sdk_version()
    validate_tag(tag, version)
    files = _regular_files(directory)
    input_names = _package_names(source_sha)
    release_names = _expected_release_names(source_sha)

    if sorted(files) == input_names:
        packages = _validate_packages(files, source_sha)
        index_contents = _index_bytes(tag, source_sha, version, packages)
        sums_contents = _sha256sums_bytes(files, index_contents)
        try:
            (directory / "release-index.json").write_bytes(index_contents)
            (directory / "SHA256SUMS").write_bytes(sums_contents)
        except OSError as exc:
            raise ReleaseError(f"cannot write prepared release metadata: {exc}") from exc
        _validate_prepared_assets(directory, tag, source_sha, version)
        return packages

    if sorted(files) == release_names:
        return _validate_prepared_assets(directory, tag, source_sha, version)

    _check_exact_names(sorted(files), input_names, "downloaded SDK artifact directory")
    raise ReleaseError("could not prepare the SDK release asset set")


def _asset_descriptors(directory: Path, source_sha: str) -> List[Dict[str, Any]]:
    files = _regular_files(directory)
    _check_exact_names(sorted(files), _expected_release_names(source_sha), "prepared release directory")
    result = []
    for name in sorted(files):
        path = files[name]
        result.append({
            "name": name,
            "path": path,
            "size": path.stat().st_size,
            "sha256": _sha256_path(path),
        })
    return result


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


class GitHubReleaseClient:
    def __init__(self, token: str, opener: Optional[Any] = None):
        _require(isinstance(token, str) and bool(token.strip()), "GITHUB_TOKEN is required for publish")
        _require("\r" not in token and "\n" not in token,
                 "GITHUB_TOKEN contains invalid header characters")
        self.token = token
        self.opener = opener or urllib.request.build_opener(_RejectRedirects())

    @staticmethod
    def _validate_url(url: str) -> None:
        try:
            parsed = urllib.parse.urlsplit(url)
            port = parsed.port
        except ValueError as exc:
            raise ReleaseError("GitHub returned an invalid API/upload URL") from exc
        _require(parsed.scheme == "https" and parsed.username is None and parsed.password is None,
                 "refusing a non-HTTPS or credential-bearing GitHub URL")
        _require(port in (None, 443), "refusing a GitHub URL using a nonstandard port")
        if parsed.hostname == "api.github.com":
            _require(parsed.path.startswith(API_PREFIX), "refusing an API URL outside this repository")
        elif parsed.hostname == "uploads.github.com":
            _require(parsed.path.startswith(API_PREFIX + "releases/"),
                     "refusing an upload URL outside this repository")
        else:
            raise ReleaseError("refusing an untrusted GitHub API/upload host")

    def _request(
        self,
        method: str,
        url: str,
        *,
        data: Optional[bytes] = None,
        content_type: Optional[str] = None,
    ) -> Tuple[int, bytes]:
        self._validate_url(url)
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + self.token,
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": API_VERSION,
        }
        if data is not None:
            headers["Content-Length"] = str(len(data))
        if content_type:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=120) as response:
                status = getattr(response, "status", response.getcode())
                body = response.read(MAX_API_RESPONSE + 1)
        except urllib.error.HTTPError as exc:
            status = exc.code
            try:
                exc.close()
            except Exception:
                pass
            raise GitHubAPIError(status, method, url) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            # Avoid formatting exception/request objects: they can contain sensitive headers.
            raise ReleaseError(
                f"GitHub {method} request failed ({type(exc).__name__}); response body omitted"
            ) from None
        _require(len(body) <= MAX_API_RESPONSE, "GitHub API response exceeded the size limit")
        return status, body

    def _json_request(
        self,
        method: str,
        url: str,
        *,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        data = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
        status, body = self._request(
            method,
            url,
            data=data,
            content_type="application/json" if data is not None else None,
        )
        _require(status in (200, 201), f"unexpected GitHub API status {status} for {method}")
        try:
            document = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ReleaseError(f"GitHub API {method} returned invalid JSON; response body omitted") from None
        _require(isinstance(document, dict), f"GitHub API {method} did not return a JSON object")
        return document

    def verify_remote_tag(self, tag: str, source_sha: str) -> None:
        encoded_tag = urllib.parse.quote(tag, safe="")
        url = API_BASE + "/git/ref/tags/" + encoded_tag
        try:
            reference = self._json_request("GET", url)
        except GitHubAPIError as exc:
            if exc.status == 404:
                raise ReleaseError(f"remote tag {tag} does not exist; refusing to create it") from None
            raise
        _require(reference.get("ref") == "refs/tags/" + tag,
                 f"GitHub returned a different tag ref while resolving {tag}")
        obj = reference.get("object")
        _require(isinstance(obj, dict), f"remote tag {tag} has invalid Git object metadata")
        seen = set()
        for _ in range(8):
            object_type = obj.get("type")
            object_sha = obj.get("sha")
            _require(isinstance(object_sha, str) and SOURCE_SHA_RE.fullmatch(object_sha) is not None,
                     f"remote tag {tag} contains an invalid Git object SHA")
            if object_type == "commit":
                _require(object_sha == source_sha,
                         f"remote tag {tag} resolves to {object_sha}, not requested source SHA {source_sha}")
                return
            _require(object_type == "tag", f"remote tag {tag} does not resolve to a commit")
            _require(object_sha not in seen, f"remote tag {tag} contains an annotated-tag cycle")
            seen.add(object_sha)
            obj = self._json_request("GET", API_BASE + "/git/tags/" + object_sha).get("object")
            _require(isinstance(obj, dict), f"annotated tag {tag} has invalid target metadata")
        raise ReleaseError(f"remote tag {tag} exceeds the annotated-tag depth limit")

    def find_release(self, tag: str) -> Optional[Dict[str, Any]]:
        encoded_tag = urllib.parse.quote(tag, safe="")
        try:
            return self._json_request("GET", API_BASE + "/releases/tags/" + encoded_tag)
        except GitHubAPIError as exc:
            if exc.status != 404:
                raise

        # The tag endpoint exposes published releases. List releases as well so
        # an authenticated publisher can detect and safely handle existing drafts.
        for page in range(1, MAX_RELEASE_LIST_PAGES + 1):
            url = API_BASE + "/releases?" + urllib.parse.urlencode(
                {"per_page": 100, "page": page}
            )
            status, body = self._request("GET", url)
            _require(status == 200, f"unexpected GitHub API status {status} while listing releases")
            try:
                releases = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ReleaseError("GitHub release list returned invalid JSON; response body omitted") from None
            _require(isinstance(releases, list), "GitHub release list did not return a JSON array")
            matches = [item for item in releases
                       if isinstance(item, dict) and item.get("tag_name") == tag]
            _require(len(matches) <= 1, f"GitHub returned duplicate releases for tag {tag}")
            if matches:
                return matches[0]
            if len(releases) < 100:
                return None
        raise ReleaseError("GitHub release list exceeded the safe pagination limit")

    @staticmethod
    def _release_id(release: Dict[str, Any]) -> int:
        release_id = release.get("id")
        _require(isinstance(release_id, int) and release_id > 0,
                 "GitHub returned a release without a valid numeric id")
        return release_id

    def create_draft(self, tag: str, source_sha: str, sdk_version: str) -> Dict[str, Any]:
        payload = {
            "tag_name": tag,
            "target_commitish": source_sha,
            "name": f"Jetson SDK {sdk_version} test prerelease",
            "body": (
                "CPU-only headless ARM64 SDK archives for the four documented JetPack reference targets. "
                "No CUDA, kernel modules, or physical Jetson streaming validation are included. "
                "See release-index.json and SHA256SUMS for package metadata and checksums."
            ),
            "draft": True,
            "prerelease": True,
            "generate_release_notes": False,
            "make_latest": "false",
        }
        return self._json_request("POST", API_BASE + "/releases", payload=payload)

    def trusted_upload_endpoint(self, release: Dict[str, Any]) -> str:
        release_id = self._release_id(release)
        expected = (
            f"https://uploads.github.com/repos/{OWNER}/{REPOSITORY}/releases/"
            f"{release_id}/assets{{?name,label}}"
        )
        _require(release.get("upload_url") == expected,
                 "GitHub returned an unexpected upload URL; refusing credential forwarding")
        return expected[:-len("{?name,label}")]

    def upload_asset(
        self,
        upload_endpoint: str,
        asset: Dict[str, Any],
    ) -> Dict[str, Any]:
        name = asset["name"]
        _require("/" not in name and "\\" not in name,
                 "release asset name must be a basename")
        try:
            contents = asset["path"].read_bytes()
        except OSError as exc:
            raise ReleaseError(f"cannot read release asset {name}: {exc}") from exc
        _require(len(contents) == asset["size"] and _sha256_bytes(contents) == asset["sha256"],
                 f"release asset changed after verification: {name}")
        url = upload_endpoint + "?" + urllib.parse.urlencode({"name": name})
        status, body = self._request(
            "POST", url, data=contents, content_type="application/octet-stream"
        )
        _require(status == 201, f"unexpected GitHub upload status {status} for {name}")
        try:
            uploaded = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ReleaseError(f"GitHub upload response for {name} was invalid JSON; response body omitted") from None
        _require(isinstance(uploaded, dict), f"GitHub upload response for {name} was not an object")
        _require(uploaded.get("name") == name, f"GitHub uploaded an unexpected asset name for {name}")
        _require(uploaded.get("size") == asset["size"], f"GitHub asset size does not match for {name}")
        _require(uploaded.get("digest") == "sha256:" + asset["sha256"],
                 f"GitHub asset digest does not match for {name}")
        _require(uploaded.get("state") == "uploaded", f"GitHub asset is not complete: {name}")
        return uploaded

    def get_release(self, release_id: int) -> Dict[str, Any]:
        return self._json_request("GET", API_BASE + f"/releases/{release_id}")

    def publish_release(self, release: Dict[str, Any], source_sha: str) -> Dict[str, Any]:
        release_id = self._release_id(release)
        payload = {
            "draft": False,
            "prerelease": True,
            "target_commitish": source_sha,
            "make_latest": "false",
        }
        return self._json_request("PATCH", API_BASE + f"/releases/{release_id}", payload=payload)


def _verify_release_assets(
    release: Dict[str, Any],
    assets: List[Dict[str, Any]],
    *,
    tag: str,
    draft: bool,
) -> None:
    _require(release.get("tag_name") == tag, "GitHub release tag does not match the requested tag")
    _require(release.get("prerelease") is True, "GitHub release is not marked as a prerelease")
    _require(release.get("draft") is draft,
             "GitHub release draft/publication state does not match the expected state")
    actual = release.get("assets")
    _require(isinstance(actual, list), "GitHub release assets are missing or invalid")
    expected_by_name = {asset["name"]: asset for asset in assets}
    _require(len(actual) == len(expected_by_name), "GitHub release has a missing or extra asset")
    seen = set()
    for uploaded in actual:
        _require(isinstance(uploaded, dict), "GitHub release contains invalid asset metadata")
        name = uploaded.get("name")
        _require(name in expected_by_name, "GitHub release contains an unexpected asset")
        _require(name not in seen, "GitHub release contains a duplicate asset")
        seen.add(name)
        expected = expected_by_name[name]
        _require(uploaded.get("state") == "uploaded", f"GitHub release asset is incomplete: {name}")
        _require(uploaded.get("size") == expected["size"], f"GitHub release asset size mismatch: {name}")
        _require(uploaded.get("digest") == "sha256:" + expected["sha256"],
                 f"GitHub release asset digest mismatch: {name}")
    _require(seen == set(expected_by_name), "GitHub release is missing expected assets")


def publish_assets(
    directory: Path,
    tag: str,
    source_sha: str,
    token: str,
    sdk_version: Optional[str] = None,
    client: Optional[GitHubReleaseClient] = None,
) -> str:
    validate_source_sha(source_sha)
    version = sdk_version or read_sdk_version()
    validate_tag(tag, version)
    _validate_prepared_assets(directory, tag, source_sha, version)
    assets = _asset_descriptors(directory, source_sha)
    # Rebind the descriptors to the verified index/sums after hashing the files;
    # uploads perform another digest check immediately before each request.
    _validate_prepared_assets(directory, tag, source_sha, version)
    github = client or GitHubReleaseClient(token)

    github.verify_remote_tag(tag, source_sha)
    existing = github.find_release(tag)
    if existing is not None:
        _verify_release_assets(existing, assets, tag=tag, draft=existing.get("draft") is True)
        if existing.get("draft") is True:
            release = github.publish_release(existing, source_sha)
            _verify_release_assets(release, assets, tag=tag, draft=False)
            return f"Published previously complete draft prerelease {tag} with {len(assets)} verified assets."
        return f"Existing prerelease {tag} already matches all {len(assets)} verified assets; no changes made."

    release = github.create_draft(tag, source_sha, version)
    _require(release.get("tag_name") == tag, "GitHub created a release for an unexpected tag")
    _require(release.get("draft") is True and release.get("prerelease") is True,
             "GitHub did not create a draft prerelease; refusing asset upload")
    _require(release.get("assets") == [],
             "new GitHub draft already contains assets; refusing to overwrite or append")
    upload_endpoint = github.trusted_upload_endpoint(release)
    for asset in assets:
        github.upload_asset(upload_endpoint, asset)

    complete_draft = github.get_release(github._release_id(release))
    _verify_release_assets(complete_draft, assets, tag=tag, draft=True)
    published = github.publish_release(complete_draft, source_sha)
    _verify_release_assets(published, assets, tag=tag, draft=False)
    return f"Published prerelease {tag} with {len(assets)} verified assets; it is not marked latest."


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command")

    for command, help_text in (
        ("prepare", "verify the complete eight-package matrix and create index/checksum assets"),
        ("publish", "publish verified assets as a draft-first GitHub prerelease"),
    ):
        command_parser = commands.add_parser(command, help=help_text)
        command_parser.add_argument("--assets-dir", required=True, type=Path,
                                    help="directory containing the SDK packages (and, for publish, prepared metadata)")
        command_parser.add_argument("--tag", required=True,
                                    help="jetson-vMAJOR.MINOR.PATCH-ci.N tag")
        command_parser.add_argument("--source-sha", required=True,
                                    help="full source commit SHA recorded by the builds")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.error("a command is required")
    try:
        sdk_version = read_sdk_version()
        validate_tag(args.tag, sdk_version)
        validate_source_sha(args.source_sha)
        if args.command == "prepare":
            packages = prepare_assets(args.assets_dir, args.tag, args.source_sha, sdk_version)
            print(f"Prepared {len(packages)} SDK packages and 26 release assets for {args.tag}.")
            return 0
        if args.command == "publish":
            token = os.environ.get("GITHUB_TOKEN", "")
            print(publish_assets(args.assets_dir, args.tag, args.source_sha, token, sdk_version))
            return 0
        parser.error(f"unsupported command {args.command!r}")
    except (ReleaseError, jetson_ci.JetsonCIError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        # Keep the Actions release log useful without allowing unexpected exception
        # text (which may contain request details) to expose the token.
        print(f"error: release operation failed ({type(exc).__name__}); details omitted", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
