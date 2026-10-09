import copy
import contextlib
import hashlib
import io
import json
import re
import shutil
import sys
import tarfile
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parents[3]
JETSON_DIR = ROOT / "scripts" / "jetson"
sys.path.insert(0, str(JETSON_DIR))

import jetson_ci  # noqa: E402
import release  # noqa: E402


SOURCE_SHA = "a" * 40
TAG = "jetson-v2.58.4-ci.1"
SDK_VERSION = "2.58.4"
TOKEN = "test-token-must-never-be-logged"


class ReleasePackageFixture:
    def __init__(self, directory, *, source_sha=SOURCE_SHA, manifest_mutator=None):
        self.directory = Path(directory)
        self.source_sha = source_sha
        self.targets = jetson_ci.read_targets()
        self.manifest_mutator = manifest_mutator

    def create(self):
        for target_id in release.TARGET_IDS:
            target = self.targets[target_id]
            for backend in release.BACKENDS:
                manifest = jetson_ci.build_manifest(
                    self.targets,
                    target_id=target_id,
                    backend=backend,
                    source_sha=self.source_sha,
                    ubuntu_version=target["ubuntu"],
                    architecture="arm64",
                    host_kernel="6.8.0-test-host",
                    image_launcher="workflow",
                    compiler="g++ test",
                    cmake="cmake test",
                    fetched_json_commit="b" * 40,
                    dependency_versions={"cmake": "test-version"},
                )
                if self.manifest_mutator:
                    manifest = self.manifest_mutator(target_id, backend, manifest)
                prefix = self.source_sha[:12]
                archive_name = (
                    f"librealsense-jetson-{target_id}-{backend}-{prefix}.tar.gz"
                )
                archive_path = self.directory / archive_name
                self._write_archive(archive_path, manifest)
                jetson_ci.write_checksum(archive_path)
                sidecar = self.directory / (archive_name[:-7] + ".json")
                sidecar.write_text(
                    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
        return self.directory

    @staticmethod
    def _write_archive(path, manifest):
        entries = [
            ("dir", ".", 0o755, ""),
            ("dir", "./usr", 0o755, ""),
            ("dir", "./usr/local", 0o755, ""),
            ("dir", "./usr/local/lib", 0o755, ""),
            ("dir", "./usr/local/share", 0o755, ""),
            ("dir", "./usr/local/share/doc", 0o755, ""),
            ("dir", "./usr/local/share/doc/librealsense2", 0o755, ""),
            ("file", "./usr/local/lib/librealsense2.so.2.58.4", 0o644, b"SDK library"),
            ("symlink", "./usr/local/lib/librealsense2.so.2", 0o777, "librealsense2.so.2.58.4"),
            ("symlink", "./usr/local/lib/librealsense2.so", 0o777, "librealsense2.so.2"),
            (
                "file",
                "./usr/local/share/doc/librealsense2/jetson-build-manifest.json",
                0o644,
                json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"),
            ),
        ]
        with tarfile.open(str(path), mode="w:gz") as archive:
            for kind, name, mode, data in entries:
                member = tarfile.TarInfo(name)
                member.mode = mode
                if kind == "dir":
                    member.type = tarfile.DIRTYPE
                    archive.addfile(member)
                elif kind == "symlink":
                    member.type = tarfile.SYMTYPE
                    member.linkname = data
                    archive.addfile(member)
                else:
                    member.type = tarfile.REGTYPE
                    member.size = len(data)
                    archive.addfile(member, io.BytesIO(data))


def _api_error(url, status, token_in_body=None):
    body = b"not found"
    if token_in_body is not None:
        body = token_in_body.encode("utf-8")
    raise urllib.error.HTTPError(url, status, "mock response", {}, io.BytesIO(body))


class FakeResponse:
    def __init__(self, status, document):
        self.status = status
        self.body = document if isinstance(document, bytes) else json.dumps(document).encode("utf-8")

    def getcode(self):
        return self.status

    def read(self, size=-1):
        return self.body if size < 0 else self.body[:size]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeGitHubOpener:
    def __init__(self, *, source_sha=SOURCE_SHA, tag=TAG, existing_release=None,
                 fail_upload_at=None, fail_create_status=None, upload_url=None,
                 error_token=TOKEN, draft_lookup_returns_404=False, annotated_tag=False):
        self.source_sha = source_sha
        self.tag = tag
        self.existing_release = copy.deepcopy(existing_release)
        self.fail_upload_at = fail_upload_at
        self.fail_create_status = fail_create_status
        self.upload_url = upload_url
        self.error_token = error_token
        self.draft_lookup_returns_404 = draft_lookup_returns_404
        self.annotated_tag = annotated_tag
        self.calls = []
        self.release = copy.deepcopy(existing_release)
        self.upload_count = 0
        self.create_payload = None
        self.update_payload = None

    def open(self, request, timeout=0):
        url = request.full_url
        method = request.get_method()
        body = request.data or b""
        parsed = urlsplit(url)
        path = parsed.path
        self.calls.append((method, url, body, request.headers.copy(), timeout))

        if path.endswith("/git/ref/tags/" + self.tag):
            git_object = (
                {"type": "tag", "sha": "e" * 40}
                if self.annotated_tag
                else {"type": "commit", "sha": self.source_sha}
            )
            return FakeResponse(200, {
                "ref": "refs/tags/" + self.tag,
                "object": git_object,
            })

        if path == "/repos/kyeshmz/librealsense/git/tags/" + "e" * 40:
            return FakeResponse(200, {"object": {"type": "commit", "sha": self.source_sha}})

        if path.endswith("/releases/tags/" + self.tag):
            if self.release is None or (
                self.draft_lookup_returns_404 and self.release.get("draft") is True
            ):
                _api_error(url, 404, self.error_token)
            return FakeResponse(200, copy.deepcopy(self.release))

        if path == "/repos/kyeshmz/librealsense/releases" and method == "GET":
            return FakeResponse(200, [] if self.release is None else [copy.deepcopy(self.release)])

        if path == "/repos/kyeshmz/librealsense/releases" and method == "POST":
            if self.fail_create_status:
                _api_error(url, self.fail_create_status, self.error_token)
            self.create_payload = json.loads(body.decode("utf-8"))
            self.release = {
                "id": 123,
                "tag_name": self.tag,
                "target_commitish": self.create_payload["target_commitish"],
                "upload_url": self.upload_url or (
                    "https://uploads.github.com/repos/kyeshmz/librealsense/releases/"
                    "123/assets{?name,label}"
                ),
                "draft": True,
                "prerelease": True,
                "assets": [],
            }
            return FakeResponse(201, copy.deepcopy(self.release))

        if parsed.hostname == "uploads.github.com" and path.endswith("/releases/123/assets"):
            self.upload_count += 1
            if self.fail_upload_at == self.upload_count:
                _api_error(url, 500, self.error_token)
            name = parse_qs(parsed.query).get("name", [None])[0]
            digest = hashlib.sha256(body).hexdigest()
            asset = {
                "id": self.upload_count,
                "name": name,
                "size": len(body),
                "digest": "sha256:" + digest,
                "state": "uploaded",
            }
            self.release["assets"].append(asset)
            return FakeResponse(201, asset)

        release_id = self.release.get("id") if self.release is not None else 123
        if path == f"/repos/kyeshmz/librealsense/releases/{release_id}" and method == "GET":
            return FakeResponse(200, copy.deepcopy(self.release))

        if path == f"/repos/kyeshmz/librealsense/releases/{release_id}" and method == "PATCH":
            self.update_payload = json.loads(body.decode("utf-8"))
            self.release.update(self.update_payload)
            return FakeResponse(200, copy.deepcopy(self.release))

        raise AssertionError(f"unexpected mock request {method} {url}")


class ReleaseValidationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.directory = Path(self.tempdir.name)
        ReleasePackageFixture(self.directory).create()

    def tearDown(self):
        self.tempdir.cleanup()

    def test_version_is_read_from_rs_h_macros_and_tag_is_strict(self):
        self.assertEqual(release.read_sdk_version(), SDK_VERSION)
        release.validate_tag(TAG, SDK_VERSION)
        for invalid in (
            "jetson-v2.58.4",
            "jetson-v2.58.4-ci.0",
            "jetson-v2.58.4-ci.01",
            "jetson-v2.58.4-ci.latest",
            "jetson-v2.58.4-ci.1-extra",
        ):
            with self.subTest(tag=invalid), self.assertRaises(release.ReleaseError):
                release.validate_tag(invalid, SDK_VERSION)
        with self.assertRaisesRegex(release.ReleaseError, "does not match rs.h"):
            release.validate_tag("jetson-v2.58.3-ci.1", SDK_VERSION)

    def test_prepare_generates_deterministic_26_asset_bundle_and_25_sum_entries(self):
        packages = release.prepare_assets(self.directory, TAG, SOURCE_SHA, SDK_VERSION)
        self.assertEqual(len(packages), 8)
        names = sorted(path.name for path in self.directory.iterdir())
        self.assertEqual(len(names), 26)
        self.assertEqual(names, release._expected_release_names(SOURCE_SHA))
        index = json.loads((self.directory / "release-index.json").read_text(encoding="utf-8"))
        self.assertEqual(index["tag"], TAG)
        self.assertEqual(index["source_sha"], SOURCE_SHA)
        self.assertEqual(index["sdk_version"], SDK_VERSION)
        self.assertEqual(len(index["packages"]), 8)
        self.assertEqual(index["packages"][0]["target"], "jp4")
        self.assertEqual(index["packages"][0]["backend"], "rsusb")
        sums = (self.directory / "SHA256SUMS").read_text(encoding="ascii").splitlines()
        self.assertEqual(len(sums), 25)
        self.assertTrue(any(line.endswith("  release-index.json") for line in sums))
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{64}  [^/]+", line) for line in sums))
        first_index = (self.directory / "release-index.json").read_bytes()
        first_sums = (self.directory / "SHA256SUMS").read_bytes()
        release.prepare_assets(self.directory, TAG, SOURCE_SHA, SDK_VERSION)
        self.assertEqual((self.directory / "release-index.json").read_bytes(), first_index)
        self.assertEqual((self.directory / "SHA256SUMS").read_bytes(), first_sums)
        self.assertEqual(len(release._asset_descriptors(self.directory, SOURCE_SHA)), 26)

    def test_missing_and_duplicate_matrix_packages_are_rejected(self):
        archive_name = f"librealsense-jetson-jp4-rsusb-{SOURCE_SHA[:12]}.tar.gz"
        with tempfile.TemporaryDirectory() as missing_directory:
            missing_path = Path(missing_directory)
            ReleasePackageFixture(missing_path).create()
            (missing_path / (archive_name + ".sha256")).unlink()
            with self.assertRaisesRegex(release.ReleaseError, "missing:"):
                release.prepare_assets(missing_path, TAG, SOURCE_SHA, SDK_VERSION)

        with tempfile.TemporaryDirectory() as duplicate_directory:
            duplicate_path = Path(duplicate_directory)
            ReleasePackageFixture(duplicate_path).create()
            duplicate_prefix = "c" * 12
            base = f"librealsense-jetson-jp4-rsusb-{duplicate_prefix}"
            for suffix in (".tar.gz", ".tar.gz.sha256", ".json"):
                shutil.copyfile(
                    duplicate_path / (archive_name[:-7] + suffix),
                    duplicate_path / (base + suffix),
                )
            with self.assertRaisesRegex(release.ReleaseError, "unexpected:"):
                release.prepare_assets(duplicate_path, TAG, SOURCE_SHA, SDK_VERSION)

    def test_wrong_source_manifest_and_sidecar_mismatch_are_rejected(self):
        with tempfile.TemporaryDirectory() as other:
            wrong = Path(other)
            def wrong_source(_target, _backend, manifest):
                manifest["source_sha"] = "c" * 40
                return manifest

            ReleasePackageFixture(wrong, manifest_mutator=wrong_source).create()
            with self.assertRaisesRegex(release.ReleaseError, "does not match the tagged source"):
                release.prepare_assets(wrong, TAG, SOURCE_SHA, SDK_VERSION)

        sidecar = self.directory / (
            f"librealsense-jetson-jp4-rsusb-{SOURCE_SHA[:12]}.json"
        )
        document = json.loads(sidecar.read_text(encoding="utf-8"))
        document["backend"] = "native"
        sidecar.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaisesRegex(release.ReleaseError, "differs from the archive"):
            release.prepare_assets(self.directory, TAG, SOURCE_SHA, SDK_VERSION)

    def test_archive_hash_and_embedded_manifest_failures_are_rejected(self):
        archive = self.directory / (
            f"librealsense-jetson-jp4-rsusb-{SOURCE_SHA[:12]}.tar.gz"
        )
        archive.write_bytes(archive.read_bytes() + b"tampered")
        with self.assertRaisesRegex(release.ReleaseError, "SHA-256 does not match"):
            release.prepare_assets(self.directory, TAG, SOURCE_SHA, SDK_VERSION)

        with tempfile.TemporaryDirectory() as other:
            invalid = Path(other)

            def wrong_image(_target, _backend, manifest):
                manifest["container_image"]["launcher_selection"][
                    "configured_arm64_manifest_digest"
                ] = "sha256:" + "0" * 64
                return manifest

            ReleasePackageFixture(invalid, manifest_mutator=wrong_image).create()
            with self.assertRaisesRegex(release.ReleaseError, "image digest does not match"):
                release.prepare_assets(invalid, TAG, SOURCE_SHA, SDK_VERSION)

        with tempfile.TemporaryDirectory() as other:
            invalid = Path(other)

            def wrong_backend_options(_target, _backend, manifest):
                manifest["cmake_options"]["FORCE_RSUSB_BACKEND"] = "OFF"
                return manifest

            ReleasePackageFixture(invalid, manifest_mutator=wrong_backend_options).create()
            with self.assertRaisesRegex(release.ReleaseError, "backend/build options"):
                release.prepare_assets(invalid, TAG, SOURCE_SHA, SDK_VERSION)

    def test_unexpected_files_and_symlink_assets_are_rejected(self):
        (self.directory / "build.log").write_text("not a release asset", encoding="utf-8")
        with self.assertRaisesRegex(release.ReleaseError, "unexpected:"):
            release.prepare_assets(self.directory, TAG, SOURCE_SHA, SDK_VERSION)
        (self.directory / "build.log").unlink()

        link = self.directory / "unexpected-link"
        link.symlink_to(self.directory / release._package_names(SOURCE_SHA)[0])
        with self.assertRaisesRegex(release.ReleaseError, "symlink"):
            release.prepare_assets(self.directory, TAG, SOURCE_SHA, SDK_VERSION)


class ReleaseAPITests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.directory = Path(self.tempdir.name)
        ReleasePackageFixture(self.directory).create()
        release.prepare_assets(self.directory, TAG, SOURCE_SHA, SDK_VERSION)

    def tearDown(self):
        self.tempdir.cleanup()

    def client(self, opener):
        return release.GitHubReleaseClient(TOKEN, opener=opener)

    def test_publish_creates_draft_uploads_and_verifies_all_assets_before_finalizing(self):
        opener = FakeGitHubOpener()
        message = release.publish_assets(
            self.directory, TAG, SOURCE_SHA, TOKEN, SDK_VERSION, self.client(opener)
        )
        self.assertIn("26 verified assets", message)
        self.assertTrue(message.startswith("Published prerelease"))
        self.assertEqual(opener.upload_count, 26)
        self.assertEqual(opener.create_payload["target_commitish"], SOURCE_SHA)
        self.assertTrue(opener.create_payload["draft"])
        self.assertTrue(opener.create_payload["prerelease"])
        self.assertEqual(opener.create_payload["make_latest"], "false")
        self.assertEqual(opener.update_payload["target_commitish"], SOURCE_SHA)
        self.assertFalse(opener.update_payload["draft"])
        self.assertEqual(opener.update_payload["make_latest"], "false")
        methods = [(call[0], urlsplit(call[1]).path) for call in opener.calls]
        create_index = methods.index(("POST", "/repos/kyeshmz/librealsense/releases"))
        final_get_index = methods.index(("GET", "/repos/kyeshmz/librealsense/releases/123"))
        publish_index = methods.index(("PATCH", "/repos/kyeshmz/librealsense/releases/123"))
        upload_indexes = [i for i, (method, path) in enumerate(methods)
                          if method == "POST" and path.endswith("/assets")]
        self.assertEqual(len(upload_indexes), 26)
        self.assertLess(create_index, min(upload_indexes))
        self.assertLess(max(upload_indexes), final_get_index)
        self.assertLess(final_get_index, publish_index)
        for call in opener.calls:
            self.assertEqual(call[3].get("Authorization"), "Bearer " + TOKEN)
            if urlsplit(call[1]).hostname == "uploads.github.com":
                headers = {key.lower(): value for key, value in call[3].items()}
                self.assertEqual(headers.get("content-length"), str(len(call[2])))

    def test_failed_upload_leaves_partial_release_as_draft_and_never_publishes(self):
        opener = FakeGitHubOpener(fail_upload_at=4)
        with self.assertRaises(release.GitHubAPIError) as context:
            release.publish_assets(
                self.directory, TAG, SOURCE_SHA, TOKEN, SDK_VERSION, self.client(opener)
            )
        self.assertEqual(opener.upload_count, 4)
        self.assertTrue(opener.release["draft"])
        self.assertFalse(any(call[0] == "PATCH" for call in opener.calls))
        self.assertNotIn(TOKEN, str(context.exception))

    def test_permission_errors_are_sanitized_and_do_not_create_a_public_release(self):
        opener = FakeGitHubOpener(fail_create_status=403)
        with self.assertRaises(release.GitHubAPIError) as context:
            release.publish_assets(
                self.directory, TAG, SOURCE_SHA, TOKEN, SDK_VERSION, self.client(opener)
            )
        message = str(context.exception)
        self.assertIn("contents:write", message)
        self.assertNotIn(TOKEN, message)
        self.assertIsNone(opener.release)
        self.assertFalse(any(call[0] == "PATCH" for call in opener.calls))

    def test_remote_tag_must_resolve_to_exact_source_before_release_creation(self):
        opener = FakeGitHubOpener(source_sha="d" * 40)
        with self.assertRaisesRegex(release.ReleaseError, "not requested source SHA"):
            release.publish_assets(
                self.directory, TAG, SOURCE_SHA, TOKEN, SDK_VERSION, self.client(opener)
            )
        self.assertIsNone(opener.create_payload)
        self.assertFalse(any(call[0] == "POST" for call in opener.calls))

    def test_annotated_remote_tag_is_peeled_to_the_exact_commit(self):
        opener = FakeGitHubOpener(annotated_tag=True)
        self.client(opener).verify_remote_tag(TAG, SOURCE_SHA)
        tag_reads = [call for call in opener.calls if "/git/tags/" in call[1]]
        self.assertEqual(len(tag_reads), 1)

    def test_missing_remote_tag_is_not_created_implicitly(self):
        opener = FakeGitHubOpener()

        def missing_tag(request, timeout=0):
            if "/git/ref/tags/" in request.full_url:
                _api_error(request.full_url, 404, TOKEN)
            return opener.open(request, timeout)

        client = self.client(mock.Mock(open=missing_tag))
        with self.assertRaisesRegex(release.ReleaseError, "refusing to create it"):
            release.publish_assets(
                self.directory, TAG, SOURCE_SHA, TOKEN, SDK_VERSION, client
            )
        self.assertFalse(any(call[0] == "POST" for call in opener.calls))

    def test_unexpected_upload_url_is_rejected_before_any_asset_upload(self):
        opener = FakeGitHubOpener(upload_url="https://evil.example/releases/123/assets{?name,label}")
        with self.assertRaisesRegex(release.ReleaseError, "unexpected upload URL"):
            release.publish_assets(
                self.directory, TAG, SOURCE_SHA, TOKEN, SDK_VERSION, self.client(opener)
            )
        self.assertEqual(opener.upload_count, 0)
        self.assertTrue(opener.release["draft"])
        self.assertFalse(any(call[0] == "PATCH" for call in opener.calls))

    def test_matching_existing_release_is_idempotent_and_never_overwritten(self):
        descriptors = release._asset_descriptors(self.directory, SOURCE_SHA)
        existing = {
            "id": 987,
            "tag_name": TAG,
            "target_commitish": SOURCE_SHA,
            "draft": False,
            "prerelease": True,
            "assets": [
                {
                    "name": asset["name"],
                    "size": asset["size"],
                    "digest": "sha256:" + asset["sha256"],
                    "state": "uploaded",
                }
                for asset in descriptors
            ],
        }
        opener = FakeGitHubOpener(existing_release=existing)
        message = release.publish_assets(
            self.directory, TAG, SOURCE_SHA, TOKEN, SDK_VERSION, self.client(opener)
        )
        self.assertIn("no changes made", message)
        self.assertEqual(opener.upload_count, 0)
        self.assertFalse(any(call[0] in ("POST", "PATCH") for call in opener.calls))

    def test_existing_release_with_conflicting_asset_is_rejected_without_mutation(self):
        descriptors = release._asset_descriptors(self.directory, SOURCE_SHA)
        existing = {
            "id": 987,
            "tag_name": TAG,
            "target_commitish": SOURCE_SHA,
            "draft": False,
            "prerelease": True,
            "assets": [
                {
                    "name": asset["name"],
                    "size": asset["size"],
                    "digest": "sha256:" + ("0" * 64 if index == 0 else asset["sha256"]),
                    "state": "uploaded",
                }
                for index, asset in enumerate(descriptors)
            ],
        }
        opener = FakeGitHubOpener(existing_release=existing)
        with self.assertRaisesRegex(release.ReleaseError, "digest mismatch"):
            release.publish_assets(
                self.directory, TAG, SOURCE_SHA, TOKEN, SDK_VERSION, self.client(opener)
            )
        self.assertEqual(opener.upload_count, 0)
        self.assertFalse(any(call[0] in ("POST", "PATCH") for call in opener.calls))

    def test_corrupt_prepared_checksums_fail_before_any_api_call(self):
        with (self.directory / "SHA256SUMS").open("ab") as handle:
            handle.write(b"corrupt\n")
        opener = FakeGitHubOpener()
        with self.assertRaisesRegex(release.ReleaseError, "SHA256SUMS"):
            release.publish_assets(
                self.directory, TAG, SOURCE_SHA, TOKEN, SDK_VERSION, self.client(opener)
            )
        self.assertEqual(opener.calls, [])

    def test_complete_preexisting_draft_is_verified_before_finalization(self):
        descriptors = release._asset_descriptors(self.directory, SOURCE_SHA)
        existing = {
            "id": 987,
            "tag_name": TAG,
            "target_commitish": SOURCE_SHA,
            "upload_url": "https://uploads.github.com/repos/kyeshmz/librealsense/releases/987/assets{?name,label}",
            "draft": True,
            "prerelease": True,
            "assets": [
                {
                    "name": asset["name"],
                    "size": asset["size"],
                    "digest": "sha256:" + asset["sha256"],
                    "state": "uploaded",
                }
                for asset in descriptors
            ],
        }
        opener = FakeGitHubOpener(existing_release=existing, draft_lookup_returns_404=True)
        message = release.publish_assets(
            self.directory, TAG, SOURCE_SHA, TOKEN, SDK_VERSION, self.client(opener)
        )
        self.assertIn("previously complete draft", message)
        self.assertEqual(opener.upload_count, 0)
        self.assertEqual(opener.update_payload["make_latest"], "false")

    def test_api_response_body_and_redirects_cannot_leak_token(self):
        opener = FakeGitHubOpener(fail_create_status=403, error_token=TOKEN)
        client = self.client(opener)
        with self.assertRaises(release.GitHubAPIError) as context:
            client._json_request("POST", release.API_BASE + "/releases", payload={})
        self.assertNotIn(TOKEN, str(context.exception))
        handler = release._RejectRedirects()
        self.assertIsNone(handler.redirect_request(None, None, 302, "found", {}, "https://evil.example/"))
        with self.assertRaisesRegex(release.ReleaseError, "untrusted GitHub"):
            client._validate_url("https://evil.example/repos/kyeshmz/librealsense/releases")

        assets = release._asset_descriptors(self.directory, SOURCE_SHA)
        remote_assets = [
            {
                "name": asset["name"],
                "size": asset["size"],
                "digest": "sha256:" + asset["sha256"],
                "state": "uploaded",
            }
            for asset in assets
        ]
        remote_assets[0]["name"] = TOKEN
        with self.assertRaises(release.ReleaseError) as metadata_error:
            release._verify_release_assets(
                {"tag_name": TAG, "draft": False, "prerelease": True, "assets": remote_assets},
                assets,
                tag=TAG,
                draft=False,
            )
        self.assertNotIn(TOKEN, str(metadata_error.exception))

    def test_unexpected_publisher_error_is_sanitized_for_release_log(self):
        stderr = io.StringIO()
        with mock.patch.dict("os.environ", {"GITHUB_TOKEN": TOKEN}), \
                mock.patch.object(release, "publish_assets", side_effect=RuntimeError(TOKEN)), \
                contextlib.redirect_stderr(stderr):
            result = release.main([
                "publish",
                "--assets-dir", str(self.directory),
                "--tag", TAG,
                "--source-sha", SOURCE_SHA,
            ])
        self.assertEqual(result, 2)
        self.assertNotIn(TOKEN, stderr.getvalue())
        self.assertIn("details omitted", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
