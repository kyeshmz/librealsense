# macOS installation

The native C++ RealSense Viewer can be built for macOS from a checkout containing the Viewer bundle changes based on `development`. These instructions do not apply to upstream branches or release tags that lack the `BUILD_MACOS_VIEWER_BUNDLE` option. macOS support is still being qualified; an app bundle is not a developer-signed or notarized release package.

## Build requirements

Install Xcode Command Line Tools and Homebrew, then install CMake and the Viewer dependencies:

```bash
xcode-select --install
brew install cmake glfw libusb
```

Use `brew --prefix` rather than assuming a fixed Homebrew location; Apple Silicon and Intel installations commonly use different prefixes. The build uses the RSUSB backend. DDS, update checks, the AI assistant, and usage statistics are disabled in the commands below to keep this package build independent of those optional network features.

## Build the command-line executable

The bundle option defaults to off. To explicitly build the ordinary Viewer executable instead of an app bundle:

```bash
cmake -S . -B build-viewer-cli \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER=/usr/bin/clang \
  -DCMAKE_CXX_COMPILER=/usr/bin/clang++ \
  -DCMAKE_PREFIX_PATH="$(brew --prefix)" \
  -DFORCE_RSUSB_BACKEND=ON \
  -DBUILD_EXAMPLES=ON \
  -DBUILD_GRAPHICAL_EXAMPLES=ON \
  -DBUILD_GLSL_EXTENSIONS=ON \
  -DBUILD_MACOS_VIEWER_BUNDLE=OFF \
  -DBUILD_WITH_DDS=OFF \
  -DCHECK_FOR_UPDATES=OFF \
  -DENABLE_AI_ASSISTANT=OFF \
  -DENABLE_STATS=OFF
cmake --build build-viewer-cli --target realsense-viewer rs-enumerate-devices --parallel
./build-viewer-cli/Release/realsense-viewer --version
```

## Build and install the app bundle

Set `BUILD_MACOS_VIEWER_BUNDLE=ON` to build the relocatable `realsense-viewer.app`. Install only the `macos-viewer` component; the app is placed directly under the chosen prefix:

```bash
cmake -S . -B build-viewer-app \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER=/usr/bin/clang \
  -DCMAKE_CXX_COMPILER=/usr/bin/clang++ \
  -DCMAKE_PREFIX_PATH="$(brew --prefix)" \
  -DFORCE_RSUSB_BACKEND=ON \
  -DBUILD_EXAMPLES=ON \
  -DBUILD_GRAPHICAL_EXAMPLES=ON \
  -DBUILD_GLSL_EXTENSIONS=ON \
  -DBUILD_MACOS_VIEWER_BUNDLE=ON \
  -DBUILD_WITH_DDS=OFF \
  -DCHECK_FOR_UPDATES=OFF \
  -DENABLE_AI_ASSISTANT=OFF \
  -DENABLE_STATS=OFF
cmake --build build-viewer-app --target realsense-viewer --parallel
cmake --install build-viewer-app --prefix "$PWD/install" --component macos-viewer
open "$PWD/install/realsense-viewer.app"
```

The bundle includes preset files under `Contents/Resources/Presets`. The Viewer does not automatically discover these packaged presets yet; import a preset manually using the Viewer's preset controls. You can also validate the installed bundle's Mach-O dependencies on macOS with `python3 scripts/check-macos-viewer-bundle.py /path/to/realsense-viewer.app`.

Installation copies non-system dependencies into `Contents/Frameworks`, rewrites their loader paths, and applies local ad-hoc signatures after rewriting. These signatures allow modified Mach-O files to execute, particularly on Apple Silicon; they do not provide a trusted distribution identity, notarization, or USB privileges. CMake 3.10 remains the project baseline; the commands above use newer CLI conveniences (`-S`/`-B`, `--parallel`, and `--install`). Use a current CMake for these commands.

CMake caches the compiler selected when a build directory is first configured. If changing compilers, configure a fresh build directory rather than reusing one with a different compiler.

The package workflow builds native `arm64` and `x86_64` artifacts separately. It checks dependency closure after relocating the app to a path containing spaces and runs the bundled executable with `--version`. This is a loader/CLI smoke test only; it does not test Finder launch, GUI rendering, playback, device discovery, or USB streaming. The workflow does not build a `universal2` app.

## macOS Viewer prereleases

The `kyeshmz/librealsense` fork publishes architecture-specific app ZIPs from new `macos-viewer-v*` tags. Its release workflow calls the package workflow above and publishes only after both native macOS jobs pass their relocation, signature, architecture, and CLI checks. Each prerelease includes separate `arm64` and `x86_64` app bundles, a `SHA256SUMS` file, and a `SOURCE.txt` commit identity. Verify the checksum before using an archive. These are preview builds, not stable SDK releases or universal apps. They are ad-hoc signed, not developer-signed or notarized, and do not qualify Finder launch, playback, or live camera access.

## Rendering and device-access limits

The macOS Viewer uses Apple's OpenGL 2.1 compatibility context, GLSL 1.20, and fixed-function rendering paths. It does not use the newer shader-accelerated processing path; rendering features and performance can differ from supported Windows and Linux configurations.

This implementation includes a focused RSUSB HID motion path for D500 devices. This is not a claim of full motion-feature parity or completed hardware qualification; device models, stream combinations, and profile changes should be treated as unqualified until tested on the target Mac.

## USB visibility and capture troubleshooting

Run these checks in Terminal in the active logged-in desktop session. First check whether macOS sees the camera with `system_profiler SPUSBDataType`. Then run `./build-viewer-cli/Release/rs-enumerate-devices --debug` from the same checkout and build directory used for the Viewer. OS USB visibility confirms only that the device is visible to macOS; SDK enumeration also requires successful USB device construction and capture. Save the exact libusb error if SDK enumeration fails.

If the error is `LIBUSB_ERROR_ACCESS`, a human can retry that same enumeration command from the active desktop Terminal with local authorization, for example `sudo ./build-viewer-cli/Release/rs-enumerate-devices --debug`, and enter any password locally without sharing it. This is a diagnostic, not a guarantee: elevated capture can still be restricted and `sudo` may not resolve every restriction.

After successful authorized enumeration, a human doing local development may launch the matching Viewer executable directly from that active desktop Terminal:

```bash
sudo ./build-viewer-cli/Release/realsense-viewer
```

This direct launch is not guaranteed: on some systems the root process may not connect to the user's WindowServer and the GUI may fail. Do not use `sudo open` to launch the GUI. If the D405 appears but no live stream is shown, enable the Stereo Module stream in the Viewer; it was off in the confirmed session. Successful SDK enumeration does not prove Viewer detection or frame delivery; verify those separately with the Viewer and a live streaming test.

USB capture through libusb may require elevated privileges in an active logged-in desktop session. Running an unsigned development build with `sudo` can help access a device, but on some systems a root process cannot connect to the user's WindowServer, so the GUI may fail to open. Running normally may open the Viewer without granting camera access. A camera privacy prompt, an app bundle, local ad-hoc signing, or an entitlement does not grant privileged libusb access. Do not disable SIP or AMFI, or add restricted device-access entitlements, as a workaround. Normal-user USB support and Finder launch with live cameras remain unqualified.

Playback of a supported recording is separate from USB capture and can be used to exercise the Viewer without a connected camera. The package workflow does not verify playback or live recording; validate the specific recording formats, playback controls, and camera recording behavior on the target Mac.

Ethernet/DDS support is outside this package qualification. It depends on separate, unmerged DDS work (including upstream PR [#15551](https://github.com/realsenseai/librealsense/pull/15551)); do not enable `BUILD_WITH_DDS` based on these USB package instructions.
