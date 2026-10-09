# Jetson ARM64 binary builds

This page describes the bounded reference targets for the fork's headless ARM64 SDK archives. It does not certify every Jetson with a matching kernel family. No successful Actions build or physical-device streaming result is claimed here; check the fork's Actions run and test hardware separately.

## Reference images and kernels

| Target | NVIDIA reference release | Ubuntu userland | Target kernel family | Boards covered by that reference | Official NVIDIA source |
| --- | --- | --- | --- | --- | --- |
| `jp4` | JetPack 4.6.6 / L4T 32.7.6 | 18.04 (Bionic) | 4.9 | Nano, TX1, TX2, Xavier | [L4T R32.7.6](https://developer.nvidia.com/embedded/linux-tegra-r3276) |
| `jp5` | JetPack 5.1.6 / L4T 35.6.4 | 20.04 (Focal) | 5.10 | Xavier, Orin | [Jetson Linux R35.6.4](https://developer.nvidia.com/embedded/jetson-linux-r3564) |
| `jp6` | JetPack 6.2.1 / L4T 36.4.4 | 22.04 (Jammy) | 5.15 | Orin | [Jetson Linux R36.4.4](https://developer.nvidia.com/embedded/jetson-linux-r3644) |
| `jp7` | JetPack 7.0 / L4T 38.2.1 | 24.04 (Noble) | 6.8 | Thor T5000 for this reference release | [JetPack 7.0 archive](https://developer.nvidia.com/embedded/jetpack/downloads/archive-7.0) |

These four releases are deliberate reference points, not a claim that they are the newest JetPack releases. JetPack 4.6.6 / Ubuntu 18.04 is a legacy, end-of-life reference and is not a recommendation for a new deployment. A device running a different L4T point release may share one of these kernel families, but that alone does not make it a validated target. In particular, the JP6 reference is Orin-only and the JP7 reference is Thor T5000-only; do not read either row as covering the other board family.

The build container uses a digest-pinned ARM64 Ubuntu **userland** for compilation; it is not an NVIDIA BSP/root filesystem image. Containers do not have an independent kernel: `uname` inside the container reports the runner/host kernel. The manifest's host-kernel field describes that build host, while the target kernel family above describes the Jetson reference compatibility selection. Neither field says that a target Jetson kernel was built, booted, or tested in CI.

## What the archives contain

The workflow configures `BUILD_EXAMPLES=OFF` and `BUILD_TOOLS=ON`, and produces separate `RSUSB` and native-backend SDK archives with SDK libraries, headers, and CLI tools only. `rs-enumerate-devices` is included. These archives contain no examples (including `realsense-viewer`), Python bindings, CUDA, DDS/ROS2 recording or ROSBAG2, or kernel modules. Do not combine the two backend archives in one installation: they provide mutually exclusive backend configurations under the same SDK/library names.

An artifact contains one archive, one checksum sidecar, and one JSON manifest. The build writes them under `jetson-dist/TARGET-BACKEND/` with these names (the 12-character SHA prefix comes from the source revision):

```text
librealsense-jetson-TARGET-BACKEND-SOURCE_SHA12.tar.gz
librealsense-jetson-TARGET-BACKEND-SOURCE_SHA12.tar.gz.sha256
librealsense-jetson-TARGET-BACKEND-SOURCE_SHA12.json
```

The archive is rooted at `usr/local/` and embeds the same manifest at `usr/local/share/doc/librealsense2/jetson-build-manifest.json`. Manifest schema version 1 records `target.id`, `backend`, source SHA, toolchain and dependency versions, plus `container_image.launcher_selection` and `container_image.in_container_observation`. The configured ARM64 digest is recorded as launcher-verified selection; the container separately records observed Linux/Ubuntu/architecture and explicitly does **not** claim to observe its own image digest or the target Jetson BSP/kernel. Resolved dependency versions do not lock apt repositories or guarantee a bit-for-bit or fully reproducible rebuild. Artifacts are temporary GitHub Actions artifacts, not Debian packages, releases, or an apt repository, and expire according to the fork's retention settings.

## Enable and run the workflow on a fork

1. Use a public fork and have a fork maintainer enable GitHub Actions for it (the fork's **Actions** tab may require an explicit enable/approval step). Keep workflow permissions read-only; do not add secrets for these builds.
2. The isolated workflow uses the public GitHub-hosted ARM64 runner label `ubuntu-24.04-arm`, then runs the build container with `--platform linux/arm64`. Runner availability or quota is controlled by GitHub and the repository/account; a configured workflow is not evidence that a run started or passed.
3. The workflow has a branch-scoped, path-limited push trigger and a path-limited pull-request trigger for the Jetson workflow, tooling, and documentation. The intended working branch is `jetson-binary-builds`; confirm the actual `on:` filters in `.github/workflows/jetson-binaries.yml` before relying on a push trigger. A push to another branch or unrelated paths may not trigger it.
4. GitHub generally offers `workflow_dispatch` only when that workflow file is present on the repository's default branch. The current `jetson-binary-builds` branch is built through its push trigger; this work does not change the default branch, so do not assume manual dispatch will be available for it. If the workflow is available for manual dispatch, select the target/backend controls shown by GitHub. Inputs are validated against the allowlist; the offline matrix interface supports `jp4`, `jp5`, `jp6`, `jp7` or `all`, and `rsusb`, `native` or `both`.
5. Wait for the run to finish. Download its artifact from the run page, or authenticate GitHub CLI and download by that run's ID:

   ```sh
   gh auth login
   gh auth status
   read -r -p 'Workflow run ID: ' RUN_ID
   read -r -p 'Fork in OWNER/REPO form: ' REPO
   gh run download "$RUN_ID" --repo "$REPO" --dir ./jetson-artifacts
   ```

    The downloaded files are only available for the configured retention period. The workflow checks ARM64 ELF objects and dependencies, runs the installed enumerator's help command, and exercises relocated SDK consumers using both CMake and pkg-config. It also normalizes the staged pkg-config library path for ARM64. These are build-time checks, not evidence that a workflow passed or that a camera streams on a Jetson. Each matrix job uploads separate pull/build logs even when the build fails; SDK artifacts upload only after a successful build.

## Local preflight and optional Docker build

From the repository checkout, run the offline metadata validation and inspect the generated choices before starting a workflow:

```sh
python3 scripts/jetson/jetson_ci.py validate
python3 scripts/jetson/jetson_ci.py matrix --target all --backend both
```

`verify-images` performs network access to verify the configured ARM64 image digests and image metadata. It is a registry/image preflight, not a build or hardware test:

```sh
python3 scripts/jetson/jetson_ci.py verify-images
```

Use the host-side `local-build` command for optional local builds. It requires a clean source checkout, a native ARM64 host (`uname` and `dpkg`), Docker, Python 3, Git, and registry/network access. The wrapper validates target metadata, verifies the selected registry manifest and Ubuntu userland, pulls the allowlisted digest, checks Docker's Linux/ARM64 image identity, obtains the checked-out source SHA, and passes those exact values into the container. Do not substitute an arbitrary local image or invoke `build.sh` through a hand-written `docker run`: a container cannot independently attest its image digest. `--platform linux/arm64` is not emulation support and does not turn an x86 host into a native ARM64 build host. Apt dependency versions are recorded but not locked, so this is not a claim of fully reproducible builds.

```sh
python3 scripts/jetson/jetson_ci.py local-build --target jp6 --backend rsusb
```

The board-side preflight is read-only and reports the detected board/OS/kernel information. Run it on the Jetson before choosing an archive; change target/backend to match the intended package:

```sh
bash scripts/jetson/check-device.sh jp6 rsusb
```

It checks target/kernel-family compatibility, but cannot certify a complete native kernel ABI match. A rejection or non-reference release needs investigation rather than bypassing the check.

## Verify and install one archive

Download exactly one artifact for the board's target and chosen backend. The archive, `.tar.gz.sha256` sidecar, and `.json` sidecar are inside the downloaded artifact directory. Transfer all three to the Jetson, along with a trusted matching checkout of this repository. The verifier must come from that checkout; it checks the requested archive basename against a single checksum entry, verifies SHA-256, validates every tar member and symlink, and checks the embedded manifest's target/backend/configured image digest. It does not extract files or modify the filesystem. A plain `sha256sum -c` is not a substitute because it does not bind the selection or inspect archive paths. Review the JSON sidecar and corresponding workflow run before installation; source revision, build flags, and dependency/toolchain versions are provenance information, not a signed attestation or apt lockfile.

Set absolute paths and the target/backend you selected. Example for `jp6`/`rsusb`:

```sh
TRUSTED_CHECKOUT=/opt/src/librealsense
ARTIFACT_DIR=/home/ubuntu/jetson-artifacts
ARCHIVE=librealsense-jetson-jp6-rsusb-SOURCE_SHA12.tar.gz
CHECKSUM="$ARCHIVE.sha256"
MANIFEST="${ARCHIVE%.tar.gz}.json"
TARGET=jp6
BACKEND=rsusb
```

Run verification, then unprivileged extraction, and only then the privileged copy. Keep this sequence in one shell so `set -e` prevents extraction or installation after a failed check:

```sh
set -euo pipefail
case "$TRUSTED_CHECKOUT" in /*) ;; *) echo 'TRUSTED_CHECKOUT must be absolute' >&2; exit 2 ;; esac
TRUSTED_CHECKOUT="$(cd -- "$TRUSTED_CHECKOUT" && pwd -P)"
ARCHIVE_PATH="$ARTIFACT_DIR/$ARCHIVE"
CHECKSUM_PATH="$ARTIFACT_DIR/$CHECKSUM"
MANIFEST_PATH="$ARTIFACT_DIR/$MANIFEST"
python3 -m json.tool "$MANIFEST_PATH"
python3 "$TRUSTED_CHECKOUT/scripts/jetson/jetson_ci.py" verify-archive \
  --archive "$ARCHIVE_PATH" --checksum "$CHECKSUM_PATH" \
  --target "$TARGET" --backend "$BACKEND"

STAGE="$(mktemp -d /tmp/librealsense-jetson-install.XXXXXX)"
trap 'rm -rf -- "$STAGE"' EXIT
tar --no-same-owner --no-same-permissions -xzf "$ARCHIVE_PATH" -C "$STAGE"
test -d "$STAGE" && test ! -L "$STAGE"
test -d "$STAGE/usr" && test ! -L "$STAGE/usr"
test -d "$STAGE/usr/local" && test ! -L "$STAGE/usr/local"
test -d /usr && test ! -L /usr
test -d /usr/local && test ! -L /usr/local
test -s "$STAGE/usr/local/bin/rs-enumerate-devices"
test -f "$TRUSTED_CHECKOUT/config/99-realsense-libusb.rules"

sudo apt-get update
sudo apt-get install --yes libusb-1.0-0 libudev1
sudo cp -a --no-preserve=ownership "$STAGE/usr/local/." /usr/local/
sudo ldconfig
LDD_OUTPUT="$(ldd /usr/local/bin/rs-enumerate-devices)"
printf '%s\n' "$LDD_OUTPUT"
if grep -q 'not found' <<< "$LDD_OUTPUT"; then
  echo 'Install the missing runtime package(s) for this Ubuntu release and retry' >&2
  exit 1
fi
sudo install -m 0644 "$TRUSTED_CHECKOUT/config/99-realsense-libusb.rules" \
  /etc/udev/rules.d/99-realsense-libusb.rules
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Do not combine backend archives or overlay an existing backend/distro RealSense installation. Check for conflicts first and use the installing package's removal method or a clean system. The copy deliberately does not preserve archive ownership, and there is no broad `chown`. If `ldd` reports a missing library, use the manifest and the target Ubuntu package metadata to install its runtime package; do not assume build-host libraries exist on the board. Keep the archive, checksum, and manifest with the installation record. The udev rule comes from the explicit absolute trusted checkout path, not the changed artifact working directory.

Reconnect the camera and test according to the device's normal operating procedure. These instructions do not report a hardware streaming result.

## Backend choice and kernel boundary

- **RSUSB is the conservative starting choice** when you must avoid patching the Jetson kernel. It implements UVC/HID handling in userspace over the standard USB driver. It may have functional or performance tradeoffs (including multi-camera scenarios) compared with native drivers; assess the workload on the actual board.
- **Native** uses the Linux kernel UVC/IIO interfaces and may be preferable for production requirements, but the SDK archive does not supply or install patched kernel modules. Native operation depends on matching patches/modules to the exact board configuration, kernel sources, `Module.symvers`, vermagic, and signing policy. Verify those on the board before use; a matching major kernel family is not enough.
- The existing `scripts/patch-realsense-ubuntu-L4T.sh` is a separate, board-side procedure, not a CI step and not a universal installer for these binary targets. For example, it has a case for L4T 32.7.1 but not the JP4 reference L4T 32.7.6. Do not infer JP4.6.6 native patch support from the binary target table. Check that script's exact supported-release cases and follow its precautions before considering any patching; this binary workflow never inserts modules or changes a kernel.

CI build checks and physical-device tests are separate. Image digest checks, compilation, ELF/dependency checks, and smoke checks do not demonstrate that either backend streams from a camera. No successful Actions run or hardware validation is asserted by this document.
