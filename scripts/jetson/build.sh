#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
CI_TOOL="$SCRIPT_DIR/jetson_ci.py"

if [[ "$#" -ne 2 ]]; then
    echo "usage: bash scripts/jetson/build.sh TARGET BACKEND" >&2
    exit 2
fi

TARGET="$1"
BACKEND="$2"
case "$TARGET" in
    jp4|jp5|jp6|jp7) ;;
    *) echo "unknown Jetson target: $TARGET" >&2; exit 2 ;;
esac
case "$BACKEND" in
    rsusb|native) ;;
    *) echo "unknown backend: $BACKEND" >&2; exit 2 ;;
esac

MACHINE="$(uname -m)"
[[ "$MACHINE" == "aarch64" || "$MACHINE" == "arm64" ]] || {
    echo "build requires native ARM64, found machine $MACHINE" >&2
    exit 1
}
if ! command -v dpkg >/dev/null 2>&1; then
    echo "dpkg is required for the pre-install architecture check" >&2
    exit 1
fi
ARCHITECTURE="$(dpkg --print-architecture)"
[[ "$ARCHITECTURE" == "arm64" ]] || {
    echo "build requires native ARM64 dpkg architecture, found $ARCHITECTURE" >&2
    exit 1
}

if [[ ! -r /etc/os-release ]]; then
    echo "cannot read /etc/os-release before package installation" >&2
    exit 1
fi
# /etc/os-release is supplied by the base operating-system image.
# shellcheck disable=SC1091
. /etc/os-release
[[ "${ID:-}" == "ubuntu" ]] || {
    echo "build requires Ubuntu userland, found ID=${ID:-missing}" >&2
    exit 1
}

JETSON_EXPECTED_UBUNTU_VERSION="${JETSON_EXPECTED_UBUNTU_VERSION:-}"
JETSON_IMAGE_DIGEST="${JETSON_IMAGE_DIGEST:-}"
JETSON_IMAGE_LAUNCHER="${JETSON_IMAGE_LAUNCHER:-}"
[[ "${JETSON_TARGET:-}" == "$TARGET" && "${JETSON_BACKEND:-}" == "$BACKEND" ]] || {
    echo "launcher target/backend do not match the build arguments" >&2
    exit 1
}
[[ "$JETSON_EXPECTED_UBUNTU_VERSION" =~ ^[0-9]{2}\.[0-9]{2}$ ]] || {
    echo "trusted launcher must provide JETSON_EXPECTED_UBUNTU_VERSION" >&2
    exit 1
}
[[ "$JETSON_IMAGE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || {
    echo "trusted launcher must provide a complete JETSON_IMAGE_DIGEST" >&2
    exit 1
}
[[ "$JETSON_IMAGE_LAUNCHER" == "workflow" || "$JETSON_IMAGE_LAUNCHER" == "local-build" ]] || {
    echo "trusted launcher must identify itself as workflow or local-build" >&2
    exit 1
}
[[ "${VERSION_ID:-}" == "$JETSON_EXPECTED_UBUNTU_VERSION" ]] || {
    echo "build container Ubuntu ${VERSION_ID:-missing} does not match launcher selection $JETSON_EXPECTED_UBUNTU_VERSION" >&2
    exit 1
}

APT_PACKAGES=(
    python3
    ca-certificates
    build-essential
    cmake
    git
    pkg-config
    libusb-1.0-0-dev
    libudev-dev
    libssl-dev
)

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install --yes --no-install-recommends "${APT_PACKAGES[@]}"

# Minimal Ubuntu images may not contain Python, Git, or TLS roots. Do not invoke
# the helper, discover Git provenance, or fetch CMake dependencies until these
# packages have been installed; TLS verification remains enabled.
PRECHECK="$(python3 "$CI_TOOL" preflight \
    --target "$TARGET" \
    --expected-ubuntu-version "$JETSON_EXPECTED_UBUNTU_VERSION" \
    --expected-image-digest "$JETSON_IMAGE_DIGEST" \
    --image-launcher "$JETSON_IMAGE_LAUNCHER")"
read -r IMAGE_DIGEST UBUNTU_VERSION ARCHITECTURE < <(
    python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["image_digest"], d["os_release"]["VERSION_ID"], d["architecture"])' <<< "$PRECHECK"
)
[[ "$IMAGE_DIGEST" == "$JETSON_IMAGE_DIGEST" ]] || {
    echo "launcher image digest does not match validated target metadata" >&2
    exit 1
}
[[ "$ARCHITECTURE" == "arm64" ]] || { echo "build requires native ARM64 dpkg architecture" >&2; exit 1; }

SOURCE_SHA="${SOURCE_SHA:-}"
GIT_HEAD="$(git -c safe.directory="$REPO_ROOT" -C "$REPO_ROOT" rev-parse HEAD)"
if [[ -z "$SOURCE_SHA" ]]; then
    SOURCE_SHA="$GIT_HEAD"
fi
[[ "$SOURCE_SHA" =~ ^[0-9a-f]{40,64}$ && "$SOURCE_SHA" == "$GIT_HEAD" ]] || {
    echo "SOURCE_SHA must match the checked-out lowercase Git SHA" >&2
    exit 1
}
WORKTREE_STATUS="$(git -c safe.directory="$REPO_ROOT" -C "$REPO_ROOT" status --porcelain --untracked-files=all -- . ':!jetson-logs' ':!jetson-dist')"
[[ -z "$WORKTREE_STATUS" ]] || {
    echo "build requires a clean source checkout (excluding workflow diagnostic logs)" >&2
    exit 1
}

HOST_KERNEL="$(uname -r)"
[[ -n "$HOST_KERNEL" ]] || { echo "cannot determine the host kernel release" >&2; exit 1; }

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/librealsense-jetson-${TARGET}-${BACKEND}.XXXXXX")"
BUILD_DIR="$WORK_DIR/build"
STAGE_DIR="$WORK_DIR/stage"
RELOCATED_DIR="$WORK_DIR/relocated"
CONSUMER_SOURCE="$WORK_DIR/consumer-source"
CONSUMER_BUILD="$WORK_DIR/consumer-build"
PKG_CONFIG_BUILD="$WORK_DIR/pkg-config-build"
JOBS="$(getconf _NPROCESSORS_ONLN 2>/dev/null || nproc)"
[[ "$JOBS" =~ ^[1-9][0-9]*$ ]] || JOBS=2

OUT_DIR="$REPO_ROOT/jetson-dist/${TARGET}-${BACKEND}"
ARCHIVE_NAME="librealsense-jetson-${TARGET}-${BACKEND}-${SOURCE_SHA:0:12}.tar.gz"
ARCHIVE="$OUT_DIR/$ARCHIVE_NAME"
MANIFEST="$OUT_DIR/${ARCHIVE_NAME%.tar.gz}.json"
CHECKSUM="$ARCHIVE.sha256"
OUTPUT_DIR_CREATED=0
OUTPUTS_CREATED=0

cleanup() {
    status=$?
    if [[ "$OUTPUTS_CREATED" -eq 1 && "$status" -ne 0 ]]; then
        rm -f -- "$ARCHIVE" "$MANIFEST" "$CHECKSUM"
    fi
    rm -rf -- "$WORK_DIR"
    if [[ "$OUTPUT_DIR_CREATED" -eq 1 && -d "$OUT_DIR" && -z "$(find "$OUT_DIR" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
        rmdir -- "$OUT_DIR"
    fi
    exit "$status"
}
trap cleanup EXIT

if [[ ! -d "$OUT_DIR" ]]; then
    mkdir -p -- "$OUT_DIR"
    OUTPUT_DIR_CREATED=1
fi
if [[ -e "$ARCHIVE" || -e "$MANIFEST" || -e "$CHECKSUM" ]]; then
    echo "refusing to overwrite existing build output in $OUT_DIR" >&2
    exit 1
fi

cmake_options=(
    "-DCMAKE_BUILD_TYPE=Release"
    "-DCMAKE_INSTALL_PREFIX=/usr/local"
    "-DCMAKE_INSTALL_LIBDIR=lib"
    "-DENABLE_CCACHE=OFF"
    "-DBUILD_SHARED_LIBS=ON"
    "-DBUILD_EXAMPLES=OFF"
    "-DBUILD_TOOLS=ON"
    "-DBUILD_GRAPHICAL_EXAMPLES=OFF"
    "-DBUILD_WITH_CUDA=OFF"
    "-DBUILD_WITH_CUDA_ZEROCOPY=OFF"
    "-DBUILD_PYTHON_BINDINGS=OFF"
    "-DBUILD_ROSBAG2=OFF"
    "-DBUILD_WITH_DDS=OFF"
    "-DBUILD_RS2_ALL=OFF"
    "-DBUILD_WITH_CPU_EXTENSIONS=OFF"
    "-DBUILD_WITH_NEON=ON"
    "-DCHECK_FOR_UPDATES=OFF"
)
if [[ "$BACKEND" == "rsusb" ]]; then
    cmake_options+=("-DFORCE_RSUSB_BACKEND=ON")
else
    cmake_options+=("-DFORCE_RSUSB_BACKEND=OFF")
fi

mkdir -p -- "$BUILD_DIR" "$STAGE_DIR"
(
    cd -- "$BUILD_DIR"
    cmake "$REPO_ROOT" "${cmake_options[@]}"
    cmake --build . -- -j"$JOBS"
    DESTDIR="$STAGE_DIR" cmake --build . --target install -- -j"$JOBS"
)

JSON_SOURCE="$BUILD_DIR/third-party/json"
[[ -d "$JSON_SOURCE/.git" ]] || {
    echo "CMake did not leave the fetched nlohmann/json git checkout at $JSON_SOURCE" >&2
    exit 1
}
FETCHED_JSON_COMMIT="$(git -c safe.directory="$JSON_SOURCE" -C "$JSON_SOURCE" rev-parse HEAD)"
[[ "$FETCHED_JSON_COMMIT" =~ ^[0-9a-f]{40,64}$ ]] || {
    echo "could not resolve fetched nlohmann/json commit" >&2
    exit 1
}

PAYLOAD="$STAGE_DIR/usr/local"
PC_FILE="$PAYLOAD/lib/pkgconfig/realsense2.pc"
[[ -s "$PC_FILE" ]] || { echo "installed realsense2.pc is missing" >&2; exit 1; }
python3 - "$PC_FILE" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
text, count = re.subn(r"(?m)^libdir\s*=.*$", "libdir=${prefix}/lib", text)
if count != 1:
    raise SystemExit(f"expected one libdir entry in {path}, found {count}")
if "x86_64-linux-gnu" in text:
    raise SystemExit(f"host-specific x86_64 library path remains in {path}")
path.write_text(text, encoding="utf-8")
PY

install -D -m 0644 "$REPO_ROOT/LICENSE" "$PAYLOAD/share/doc/librealsense2/LICENSE"
install -D -m 0644 "$REPO_ROOT/config/99-realsense-libusb.rules" \
    "$PAYLOAD/share/librealsense2/99-realsense-libusb.rules"

LIBRARY="$PAYLOAD/lib/librealsense2.so"
ENUMERATOR="$PAYLOAD/bin/rs-enumerate-devices"
[[ -e "$LIBRARY" ]] || { echo "installed shared librealsense library is missing" >&2; exit 1; }
[[ -x "$ENUMERATOR" ]] || { echo "installed rs-enumerate-devices is missing" >&2; exit 1; }
[[ -s "$PAYLOAD/lib/librsutils.a" ]] || { echo "installed rsutils export archive is missing" >&2; exit 1; }
[[ -s "$PAYLOAD/lib/cmake/realsense2/realsense2Config.cmake" ]] || {
    echo "installed CMake package config is missing" >&2
    exit 1
}

ELF_COUNT=0
DYNAMIC_ELF_COUNT=0
while IFS= read -r -d '' file; do
    header="$(readelf -h "$file" 2>/dev/null || true)"
    [[ "$header" == *"Machine:"* ]] || continue
    machines="$(awk -F: '/Machine:/ {gsub(/^[[:space:]]+/, "", $2); print $2}' <<< "$header")"
    [[ -n "$machines" ]] || continue
    while IFS= read -r machine; do
        [[ "$machine" == "AArch64" ]] || {
            echo "non-ARM64 ELF object found: $file ($machine)" >&2
            exit 1
        }
    done <<< "$machines"
    ELF_COUNT=$((ELF_COUNT + 1))
    if grep -Eq 'Type:[[:space:]]+(DYN|EXEC)([[:space:]]|$)' <<< "$header"; then
        DYNAMIC_ELF_COUNT=$((DYNAMIC_ELF_COUNT + 1))
        ldd_output="$(LD_LIBRARY_PATH="$PAYLOAD/lib" ldd "$file" 2>&1)" || {
            echo "ldd failed for staged ELF $file:" >&2
            echo "$ldd_output" >&2
            exit 1
        }
        if grep -q 'not found' <<< "$ldd_output"; then
            echo "unresolved dependency in staged ELF $file:" >&2
            echo "$ldd_output" >&2
            exit 1
        fi
    fi
done < <(find "$PAYLOAD" -type f -print0)
[[ "$ELF_COUNT" -gt 0 && "$DYNAMIC_ELF_COUNT" -gt 0 ]] || {
    echo "no staged ARM64 ELF objects or dynamic ELF objects found" >&2
    exit 1
}

DEPENDENCY_VERSIONS="$(dpkg-query -W -f='${db:Status-Abbrev} ${binary:Package}=${Version}\n' \
    | awk '$1 == "ii" { print $2 }' | sort)"
COMPILER_VERSION="$(g++ --version | head -n 1)"
CMAKE_VERSION="$(cmake --version | head -n 1)"

mkdir -p -- "$PAYLOAD/share/doc/librealsense2"
python3 "$CI_TOOL" write-manifest \
    --target "$TARGET" \
    --backend "$BACKEND" \
    --source-sha "$SOURCE_SHA" \
    --ubuntu-version "$UBUNTU_VERSION" \
    --architecture "$ARCHITECTURE" \
    --host-kernel "$HOST_KERNEL" \
    --image-launcher "$JETSON_IMAGE_LAUNCHER" \
    --compiler "$COMPILER_VERSION" \
    --cmake "$CMAKE_VERSION" \
    --fetched-json-commit "$FETCHED_JSON_COMMIT" \
    --dependency-versions "$DEPENDENCY_VERSIONS" \
    --output "$PAYLOAD/share/doc/librealsense2/jetson-build-manifest.json"

mkdir -p -- "$WORK_DIR/package"
cp -a "$STAGE_DIR/usr" "$WORK_DIR/package/"
SOURCE_DATE_EPOCH="$(git -c safe.directory="$REPO_ROOT" -C "$REPO_ROOT" show -s --format=%ct "$SOURCE_SHA" 2>/dev/null || true)"
if [[ ! "$SOURCE_DATE_EPOCH" =~ ^[0-9]+$ ]]; then
    SOURCE_DATE_EPOCH=0
fi

OUTPUTS_CREATED=1
tar --sort=name --mtime="@${SOURCE_DATE_EPOCH}" --owner=0 --group=0 --numeric-owner \
    -cf - -C "$WORK_DIR/package" . | gzip -n > "$ARCHIVE"
cp "$PAYLOAD/share/doc/librealsense2/jetson-build-manifest.json" "$MANIFEST"
python3 "$CI_TOOL" checksum --archive "$ARCHIVE"

mkdir -p -- "$RELOCATED_DIR"
tar -xzf "$ARCHIVE" -C "$RELOCATED_DIR"
RELOCATED_PREFIX="$RELOCATED_DIR/usr/local"

EXPORT_DIR="$RELOCATED_PREFIX/lib/cmake/realsense2"
for forbidden_path in "$REPO_ROOT" "$BUILD_DIR" "$STAGE_DIR"; do
    if grep -R -F -q -- "$forbidden_path" "$EXPORT_DIR"; then
        echo "installed CMake exports contain forbidden build/source path $forbidden_path" >&2
        exit 1
    fi
done

# Make the original build and stage trees unavailable before relocation smoke tests.
rm -rf -- "$BUILD_DIR" "$STAGE_DIR"

LD_LIBRARY_PATH="$RELOCATED_PREFIX/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    "$RELOCATED_PREFIX/bin/rs-enumerate-devices" --help >/dev/null

mkdir -p -- "$CONSUMER_SOURCE" "$CONSUMER_BUILD"
cat > "$CONSUMER_SOURCE/CMakeLists.txt" <<'CMAKE'
cmake_minimum_required(VERSION 3.10)
project(jetson_sdk_consumer LANGUAGES CXX)
find_package(realsense2 CONFIG REQUIRED)
add_executable(sdk-smoke main.cpp)
target_link_libraries(sdk-smoke PRIVATE realsense2::realsense2)
CMAKE
cat > "$CONSUMER_SOURCE/main.cpp" <<'CPP'
#include <librealsense2/rs.h>

int main()
{
    rs2_error * error = nullptr;
    const int version = rs2_get_api_version(&error);
    if( error )
    {
        rs2_free_error(error);
        return 1;
    }
    return version > 0 ? 0 : 1;
}
CPP
(
    cd -- "$CONSUMER_BUILD"
    cmake -DCMAKE_PREFIX_PATH="$RELOCATED_PREFIX" "$CONSUMER_SOURCE"
    cmake --build . -- -j"$JOBS"
)
LD_LIBRARY_PATH="$RELOCATED_PREFIX/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    "$CONSUMER_BUILD/sdk-smoke"

mkdir -p -- "$PKG_CONFIG_BUILD"
export PKG_CONFIG_PATH="$RELOCATED_PREFIX/lib/pkgconfig"
export PKG_CONFIG_SYSROOT_DIR="$RELOCATED_DIR"
pkg-config --exists realsense2
PKG_FLAGS_TEXT="$(pkg-config --cflags --libs realsense2)"
[[ "$PKG_FLAGS_TEXT" == *"$RELOCATED_PREFIX/lib"* ]] || {
    echo "pkg-config did not resolve the relocated ARM64 library directory: $PKG_FLAGS_TEXT" >&2
    exit 1
}
read -r -a PKG_FLAGS <<< "$PKG_FLAGS_TEXT"
CXX_BIN="${CXX:-c++}"
"$CXX_BIN" "$CONSUMER_SOURCE/main.cpp" "${PKG_FLAGS[@]}" -o "$PKG_CONFIG_BUILD/sdk-smoke"
LD_LIBRARY_PATH="$RELOCATED_PREFIX/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    "$PKG_CONFIG_BUILD/sdk-smoke"

echo "Built $ARCHIVE"
echo "Manifest: $MANIFEST"
echo "Checksum: $CHECKSUM"
