# macOS RealSense Viewer: evidence, implementation, and qualification

## Outcome and decisions

Extend the existing native C++ RealSense Viewer, not a new Swift/Metal UI or the React/Tauri viewer. Deliver an opt-in relocatable macOS application bundle, the focused D500 USB-motion adaptation, and explicit build/package qualification. Preserve the existing command-line viewer build by default.

The user requested a plan and branch/PR audit, then authorized implementation by `openai/gpt-6-luna#max` subagents followed by review and revision by `openai/gpt-6.1-sol#high`.

Implementation branch: `macos-viewer`, based on upstream `development` at `1398ecbafd8231f1c54615b754ac5090bcae7d15`. Worktree: `/tmp/opencode/librealsense-macos-viewer`. The original `/home/kye/librealsense` checkout remains on `master` at `e15c5d6bb1563e778d116f682aeefffbae2daedc`.

## Conventions in force

- Keep changes minimal and match surrounding code. Do not reformat unrelated code.
- Core C++ must remain C++14; public API and examples retain their current compatibility requirements. Do not change public API contracts.
- Declare every new build option with `option()` in `CMake/lrs_options.cmake`. Preserve the project's CMake 3.10 baseline unless an existing feature already requires a newer version.
- Guard macOS-only changes. Preserve non-macOS behavior and the default non-bundle viewer build.
- New source files need the Apache 2.0 header and 2026 RealSense copyright, using the language's comment syntax.
- Phases 1–4 must not commit or push. Phase 5 is explicitly authorized to create/reuse the authenticated user's fork, commit all task files, and push only this task branch to that fork. Do not publish PRs, change GitHub labels, or merge upstream PRs. Never revert other workers' edits or force-push.
- Use the named worktree. Own only the files listed for your phase. The planner owns this plan except your phase's automated checkboxes and appended execution-log entries.
- A macOS app bundle, ad-hoc signing, and camera privacy metadata do not grant privileged USB capture. Do not disable SIP/AMFI or add restricted device-access entitlements.
- Report tests actually executed separately from macOS CI and live hardware checks that remain pending. Human-only manual checkboxes stay unchecked.

## Evidence

### Search coverage and limitations (2026-10-08)

Searched upstream PR titles/bodies with `macos` (101 results, all 11 pages), `osx` (29, all 3 pages), `"apple silicon"` (21, all 3 pages), `darwin`, `mac in:title`, `M1`, `cocoa`, and `glfw mac`; separately checked open macOS/OSX PRs. Screened search results, then read the directly relevant current PR descriptions, diffs, replacement discussions, review threads, and all four check-run pages of #15549/#15551. Results unrelated to macOS Viewer (CUDA, ROS packaging, old language wrappers, unrelated firmware) are not implementation dependencies.

Enumerated all 21 upstream branch heads and public heads in the two contributor forks implicated by current work: `bilims/librealsense` and `connorsoohoo/librealsense`. Fetched bilims heads into local read-only research refs and compared relevant branches to their merge bases with `development`. This is a bounded audit, not a claim that every private/deleted branch or every fork on GitHub was inspected. PR validation statements below are contributor-reported unless explicitly identified as independently checked here.

### Existing capability and base selection

`master` and `r/2.58.4` do not contain the merged Viewer/USB foundation. `development` contains #15535, #15536, #15539, #15548, #15550, #15553, #15583, and #15692. `r/2.59.1` contains those foundational merges except #15692. Verified with `git merge-base --is-ancestor`, not PR status alone. Therefore use `development`, not a broad cherry-pick stack on the release branch.

The macOS installation guide still says Viewer and IMU are unsupported. The Viewer statement is stale on `development`; the D500 USB IMU restriction still exists. `tools/CMakeLists.txt` enables Viewer on Apple in development. Its build requires `BUILD_EXAMPLES=ON`, `BUILD_GRAPHICAL_EXAMPLES=ON`, and GLSL-extension libraries even though macOS runtime GLSL acceleration is disabled.

### Current and merged PR map

All links below are in `https://github.com/realsenseai/librealsense/pull/NUMBER`.

| PR | Status at audit | Head repository / branch | Meaning and disposition |
| --- | --- | --- | --- |
| [#15535](https://github.com/realsenseai/librealsense/pull/15535) | merged | bilims / `fix-macos-libusb-cmake` | Correct bundled libusb headers/archive naming; link CoreFoundation, IOKit, Security. Already reuse from development. Replaces #15241. |
| [#15536](https://github.com/realsenseai/librealsense/pull/15536) | merged | bilims / `fix-device-watcher-shutdown` | Stop watcher before callback destruction. Already reuse. Replaces #15242. |
| [#15539](https://github.com/realsenseai/librealsense/pull/15539) | merged | bilims / `fix-macos-usb-device-capture` | Per-physical-device Darwin capture leases, exception-safe dispatch and release. Already reuse. Replaces #15243 without its signing/security workaround. Contributor tested sudo USB D555, not multicamera/hot-unplug. |
| [#15548](https://github.com/realsenseai/librealsense/pull/15548) | merged | bilims / `blms/macos-viewer-support` | Enable Viewer, ImGui GLSL 120 on GL 2.1, fixed-function pointcloud texcoords, stale callback guard, relative Viewer RPATH. Already reuse, including later #15692. Contributor tested Apple Silicon D555 USB 2D/3D/profile switching. |
| [#15550](https://github.com/realsenseai/librealsense/pull/15550) | merged | bilims / `blms/macos-usb-capture-hardening` | Bounded capture transitions, 500-ms post-capture settle, restore nonblocking dispatcher default. Already reuse. |
| [#15549](https://github.com/realsenseai/librealsense/pull/15549) | open draft; GitHub reports conflicting | bilims / `blms/macos-d555-imu-support`, `4cc0dd623` | Removes D500 Apple motion exclusion and switches HID to existing RSUSB interrupt path. Adapt narrowly to current development rather than merge stale head. Retain #15583 and subsequent sensitivity APIs. |
| [#15551](https://github.com/realsenseai/librealsense/pull/15551) | open ready; blocked | bilims / `blms/macos-d555-dds`, `db7d5744a` | Shared FastDDS, IPv4/IPv6 SO_REUSEPORT patch, C++14 ownership/constexpr fixes, RPATH and bounded cross-process participant lock. Separate optional Ethernet milestone; do not merge into USB package work. Maintainer asked if still relevant on Sept 27. |
| [#15552](https://github.com/realsenseai/librealsense/pull/15552) | open draft; blocked | bilims / `blms/dds-aware-examples`, `71783baf6` | Bounded asynchronous discovery, preserve context/error handling. Optional DDS examples follow-up, not Viewer startup dependency. |
| [#15547](https://github.com/realsenseai/librealsense/pull/15547) | open draft; blocked | bilims / `blms/python-combined-motion-orientation`, `139f24ccd` | Python quaternion property, precision/repr/tests. Not needed for native Viewer USB package. |
| [#15553](https://github.com/realsenseai/librealsense/pull/15553) | merged | bilims / `blms/rs-motion-combined` | Combined-motion example support. Already reuse; does not itself enable USB IMU on Apple. |
| [#15554](https://github.com/realsenseai/librealsense/pull/15554) | merged | bilims / `blms/fw-logger-extension-check` | Reject unsupported firmware-logger devices. Description and ancestry checked; already in base, not packaging work. |
| [#15583](https://github.com/realsenseai/librealsense/pull/15583) | merged | jcelerier / `fix-hid-overread` | HID callback points to 12-byte axis data, not a 38-byte report. Mandatory preservation while adapting #15549. |
| [#15638](https://github.com/realsenseai/librealsense/pull/15638) | merged | AlonHirshberg / `RSDSO-21316-accel-resolution` | Firmware-gated D400 accel unit scaling. Preserve current base; not evidence of macOS live qualification. |
| [#15692](https://github.com/realsenseai/librealsense/pull/15692) | merged | pinquanw / `fix/viewer-capture-queue-lock` | Repairs #15548 callback/render mutex coupling with a separate queue-map mutex. Do not reintroduce old locking. Final amended change has no claimed fresh macOS hardware test. |
| [#15632](https://github.com/realsenseai/librealsense/pull/15632) | merged | current development history | libcurl Ninja byproducts/system libidn2. Reuse when optional network features are built; deterministic package CI disables these features. |

Independently inspected all 34 check runs on the historical current heads: #15549 has a failed `Win_SH_Py_DDS_CI` run (job 94700107345), despite the macOS build passing. Do not call its CI fully green or assume the failure is unrelated without log diagnosis. #15551 returned success on all 34 listed check runs, but those are August results and build-only macOS coverage does not prove runtime discovery today.

The external dependency PR [eProsima/Fast-DDS#6471](https://github.com/eProsima/Fast-DDS/pull/6471) is also still open, not merged, at audit time. Its head is `bilims/Fast-DDS:bugfix/macos-multicast-reuseport` (`7368257ca`). It extends the QNX SO_REUSEPORT conditional to Apple in IPv4 and IPv6 multicast socket setup. Its description distinguishes socket-level validation from a 22/22 end-to-end result that also used downstream participant serialization. Do not attribute that combined result to the socket patch alone or assume the patch is already available in the SDK's pinned FastDDS release.

### Superseded PRs and unsubmitted branch prototypes

| PR / branch | Relationship | Decision |
| --- | --- | --- |
| [#15390](https://github.com/realsenseai/librealsense/pull/15390), bilims `feature/macos-d555-dds-ethernet` (`e1e2c8889`) | Closed unmerged; replaced by #15551. 14-file broad DDS/docs patch. | Reference network setup notes only; do not stack. |
| [#15393](https://github.com/realsenseai/librealsense/pull/15393), bilims `bugfix/macos-viewer-enable` (`97ca7a8dd`) | Closed unmerged; split into #15539/#15547/#15548/#15549. Mixed public API, Python, GUI/RPATH changes. | Do not copy entire diff. Review discussion documents root WindowServer startup failure on a D455 host; privileges remain a qualification risk. |
| [#15398](https://github.com/realsenseai/librealsense/pull/15398), bilims `feature/macos-parity` (`b2338bf2f`) | Closed unmerged; original 39-file stack includes #15390/#15393. Tool pieces became #15552/#15553/#15554; data-collect change not resubmitted. | No wholesale cherry-pick; avoid duplicate and abandoned work. |
| bilims `enable-macos-viewer` (`3905019b6`) | Earlier 8-file Viewer/Python/RPATH prototype; no matching PR found. | Superseded by focused Viewer/Python work. |
| bilims `fix-macos-d555-dds` (`0eea6099d`) | Earlier 11-file DDS prototype predating #15551 review fixes. | Use #15551 as future reference instead. |
| bilims `bug1/concurrent-discovery` (`ddddabe4a`) | DDS stack plus concurrent-discovery workaround, 15 files. | Superseded by #15551 bounded/idempotent implementation; future qualification must test concurrent processes. |
| bilims `fix-dds-aware-examples` (`a782fa5af`) | Earlier 16-file examples/motion/data-collect/fw-logger patch. | Use focused successors #15552/#15553/#15554. |
| [#15240](https://github.com/realsenseai/librealsense/pull/15240), connorsoohoo `connorsoohoo/fix-macos-dispatcher-mutex-deadlock` (`78a4bf831`) | Closed unmerged combined build/shutdown prototype on master. | Replaced by focused successors. |
| [#15241](https://github.com/realsenseai/librealsense/pull/15241), connorsoohoo `connorsoohoo/fix-macos-cmake-libusb` (`4e84cdac4`) | Closed unmerged. | Replaced by merged #15535. |
| [#15242](https://github.com/realsenseai/librealsense/pull/15242), connorsoohoo `connorsoohoo/fix-macos-dispatcher-mutex-deadlock-only` (`a32c5d1a5`) | Closed unmerged. | Replaced by merged #15536. |
| [#15243](https://github.com/realsenseai/librealsense/pull/15243), connorsoohoo `fix-macos-multicam-uvc-capture` (`e12f1d627`) | Closed unmerged capture/ad-hoc signing prototype; discusses restricted entitlement and SIP relaxation. | Replace capture logic with merged #15539; expressly exclude security relaxation. |
| connorsoohoo `connorsoohoo/macos-uvc-capture` (`2cba83288`) | Inspected 26-file prototype: shutdown/thread naming, capture/retry and restricted signing, plus a custom multicamera example and context-init retry. | Core successors already merged; do not copy prototype signing or unqualified multicamera changes. |
| connorsoohoo `connorsoohoo/mega` (`71644de8a`) | Inspected 22-file combined libusb/build/shutdown/capture/signing prototype, including absolute global install RPATH. Later comments warn an AMFI bypass breaks Apple Silicon USB. | Focused successors are preferable; absolute RPATH and security relaxation directly conflict with this deliverable. |
| [#14544](https://github.com/realsenseai/librealsense/pull/14544), brianferri `development` (`c5c44f8e5`) | Closed unmerged broad macOS restoration. | Not current foundation; merged #14454 and focused 2026 work are preferable. |

The merged bilims heads `fix-macos-libusb-cmake`, `fix-device-watcher-shutdown`, `fix-macos-usb-device-capture`, `blms/macos-viewer-support`, `blms/macos-usb-capture-hardening`, `blms/rs-motion-combined`, and `blms/fw-logger-extension-check` have no unique unmerged commit delta against current development. The fork's own `development` branch is stale and must not become the implementation base.

Historical context screened: #14454 (sudo-only macOS/libusb crash support; description checked, branch `ashrafk93:ashraf/macos`), #14407 (merged macOS DDS CI; branch `remibettan:dds_in_mac_gha`), #12972/#9253 (Apple Silicon builds), #8029/#7931 (old HIDAPI IMU path), #3195 (GL 2.1 extensions), #6722 (font oversampling), #4358 (OpenGL deprecation), #9732 (macOS updates default off), #1054/#1128 (GLFW/framework linkage). These are reference history, not new cherry-picks. #14972 and #15391 belong to the separate React/Tauri viewer and firmware-update frontend; deliberately not this implementation.

### Technical constraints

- Native UI: GLFW + ImGui + fixed-function 2D/3D on Apple's legacy OpenGL 2.1 compatibility context. ImGui needs GLSL 120. Keep GLSL processing/render acceleration off on this path; compiling `realsense2-gl` is still required.
- Transport: RSUSB/libusb, with existing per-device Darwin capture leases and bounded transition handling. Unsigned development USB use may need sudo in an active desktop session; root can fail to access WindowServer on some systems. Do not claim normal-user USB support.
- Current CI `Mac_DDS_cpp` is build-only, uses C++20 and a stale `/usr/local/opt/openssl@1.1` path, and does not test a relocated install. Add separate deterministic USB-package coverage without disturbing DDS jobs.
- GitHub's official runner-images table was checked: `macos-15` is native arm64 and `macos-15-intel` is x64. Source: https://github.com/actions/runner-images/blob/main/README.md . Both are currently listed, so use explicit architecture jobs rather than guessing from the macOS label.
- This execution host is Linux with Python but initially no compiler, CMake, or Ninja; passwordless sudo is unavailable. Portable unit/mock/static tests can run here; native compilation and live hardware require suitable runners. Never fabricate a macOS test pass.
- CMake docs retrieved through Context7: `MACOSX_BUNDLE`, `MACOSX_BUNDLE_INFO_PLIST`, explicit `install(TARGETS ... BUNDLE DESTINATION ...)`, and install-time scripts. Reference: https://cmake.org/cmake/help/latest/module/BundleUtilities.html . Bundle dependency rewriting must precede any final signing.

## Existing code to reuse

- `tools/realsense-viewer/CMakeLists.txt`: existing target, shared libraries, install rules, Apple relative RPATH.
- `common/ux-window.cpp`, `common/viewer.cpp`, `src/gl/pc-shader.cpp`: already merged GL 2.1/GLSL 120/fixed-function Viewer implementation. Do not reimplement.
- `common/post-processing-filters-list.*`, `common/subdevice-model.cpp`: latest callback queue ownership/locking from #15692.
- `src/libusb/darwin-device-capture.*`, `src/libusb/handle-libusb.h`: capture lifetime and bounded release.
- `research-bilims/blms/macos-d555-imu-support`: source reference for the narrowly scoped #15549 adaptation.
- `src/hid/hid-device.cpp`, `src/platform/hid-data.h`: existing RSUSB report decode, callback payload sizing, current sensitivity APIs.
- Existing testing infrastructure and pinned Actions SHAs. Portable new Python tests may use stdlib `unittest` directly to avoid loading unrelated hardware pytest infrastructure.

## Phase 1 — Application bundle and dependency validation

Deliver an opt-in relocatable macOS Viewer app bundle without changing the default CLI build.

Files owned:
- [MODIFY] `CMake/lrs_options.cmake`
- [MODIFY] `tools/realsense-viewer/CMakeLists.txt`
- [NEW] `tools/realsense-viewer/macos/Info.plist.in`
- [NEW] `tools/realsense-viewer/macos/install-bundle.cmake.in`
- [NEW] `scripts/check-macos-viewer-bundle.py`
- [NEW] `scripts/tests/test_check_macos_viewer_bundle.py`

Requirements:
1. `BUILD_MACOS_VIEWER_BUNDLE` defaults OFF. When ON on Apple, build `realsense-viewer.app` with display name RealSense Viewer and existing executable name. The installed app lives at `<prefix>/realsense-viewer.app`; define an install component `macos-viewer` so CI can install just the bundle. Fail clearly for invalid requested prerequisites/non-Apple instead of silently producing no bundle.
2. Correct plist executable/id/version metadata. Do not imply camera privacy metadata authorizes libusb capture. No automatic privileged launch or restricted entitlements.
3. Copy non-system dylib/framework dependencies into `Contents/Frameworks` and rewrite install names/RPATH as needed at install time using CMake BundleUtilities or a similarly narrow established solution. Support spaces in paths, install-prefix overrides, and DESTDIR. Installed bundle must not require Homebrew/build-tree libraries or DYLD environment variables.
4. Bundle LICENSE, NOTICE, and existing preset assets into Resources using component-local rules rather than writing to the packaging user's Documents folder. Do not change runtime preset discovery behavior in this phase; explain packaged presets are available to import manually.
5. A Python stdlib checker accepts a bundle path, validates plist/layout, recursively inspects all relevant Mach-O files with `otool`, and rejects external non-system dependencies or unsafe loader paths. Resolve `@rpath`, `@loader_path`, and `@executable_path` against the correct image/runpath context, including symlink containment. Non-Darwin execution must fail honestly unless tests inject mock inspection. Never call successful process startup a GUI or hardware streaming test.
6. Portable tests use mocked tool output and temporary layouts to cover missing bundle/metadata, system-only dependencies, bundled deps, unresolved rpaths, absolute Homebrew/build paths, path spaces, and symlink escapes.

#### Automated verification
- [x] `python3 -m unittest discover -s scripts/tests -p 'test_check_macos_viewer_bundle.py' -v`
- [x] `python3 -m py_compile scripts/check-macos-viewer-bundle.py`
- [x] `git diff --check`

#### Manual verification
- [ ] On Apple Silicon macOS, configure/build/install component `macos-viewer` with bundle option ON.
- [ ] Move installed `.app` outside source/build/install trees; validate and launch without DYLD variables or Homebrew runtime dependencies.
- [ ] Finder launch renders 2D/3D playback with no camera; confirm external preset import.

## Phase 2 — Focused macOS D500 USB-motion adaptation

Enable the existing RSUSB HID streaming path for macOS D500 devices while preserving newer safety and sensitivity fixes.

Files owned:
- [MODIFY] `src/ds/d500/d500-motion.cpp`
- [MODIFY] `src/hid/CMakeLists.txt`
- [MODIFY] `src/hid/hid-device.cpp`
- [MODIFY] `src/hid/hid-device.h`
- [MODIFY] `wrappers/python/CMakeLists.txt`
- [NEW] `scripts/tests/test_macos_motion_source.py`

Requirements:
1. Adapt the one-commit functional delta in #15549 to current development (inspect reference branch), removing D500 Apple exclusion and switching macOS to existing RSUSB interrupts. Do not cherry-pick its stale version of entire files.
2. Remove HIDAPI build/include/member/implementation paths no longer used by this backend, but do not delete vendored third-party files or unrelated legacy history. Keep legacy Python backend source lists consistent, including Darwin capture sources.
3. Preserve `data.fo.frame_size = sizeof(hid)`, current `set_feature_report(..., apply_to_accel)` signature and behavior, new firmware scaling, nonblocking dispatcher defaults, and #15692 queue locking. Handle failure to open the HID messenger before starting capture.
4. Add honest portable source-contract tests for the adaptation: absence of Apple HIDAPI route, availability of shared interrupt path, bounded payload sizing, retention of sensitivity argument, Python Darwin capture sources. These checks are not hardware tests and must be described as such.

#### Automated verification
- [x] `python3 -m unittest discover -s scripts/tests -p 'test_macos_motion_source.py' -v`
- [x] `git diff --check`

#### Manual verification
- [ ] Compile native Apple Silicon and Linux RSUSB targets, including legacy Python backend when enabled.
- [ ] D555 USB: depth/color/accel/gyro together, both motion-first and video-first, profile changes while streaming, three stop/restart/relaunch cycles without replug.
- [ ] Verify callbacks/payloads/units with ASan where practical; preserve existing D400 behavior. Check gravity norm and gyro rates against actual firmware profiles.
- [ ] Hot-unplug/replug, two cameras, and return of each device's interfaces to macOS after shutdown.

## Phase 3 — Dedicated package CI and user instructions

Add reproducible macOS Viewer package qualification and document the actual support and privilege boundaries.

Files owned:
- [NEW] `.github/workflows/macos-viewer.yml`
- [MODIFY] `doc/installation_osx.md`

Requirements:
1. Add a dedicated macOS workflow for native Apple Silicon (`macos-15`) and Intel (`macos-15-intel`) package builds if current runner availability supports both; explicitly name architectures and do not promise universal2. Preserve existing DDS workflow and do not enable unmerged DDS work.
2. Disable DDS/update checks/AI/stats in this deterministic package matrix. Use Homebrew prefix discovery, no hard-coded `/usr/local`/`/opt/homebrew` dependency assumptions. Build bundle ON and ordinary CLI OFF-option coverage in separate build directories. Use existing pinned checkout/upload Actions SHAs; no release publishing or secret requirements.
3. Install only component `macos-viewer` into a clean prefix, copy/move the app to a path with spaces, run the dependency checker with DYLD variables removed, and smoke-run `Contents/MacOS/realsense-viewer --version`. Explicitly label this loader/CLI smoke, not GUI or USB success. Archive `.app` using a macOS-appropriate archive command preserving bundle contents and publish CI artifact only.
4. Run portable script tests in CI, including motion source contracts. Do not use a blanket dependency checker skip to mark macOS tests green.
5. Replace obsolete guide with current native CLI/bundle build flags/paths, known branch/version caveat, fixed-function renderer limitations, bundled preset import, USB sudo and WindowServer caveat, recording/playback qualification, and optional DDS PR dependency. No SIP disabling advice and no claim of notarization/signing/unprivileged USB.

#### Automated verification
- [x] `python3 -m unittest discover -s scripts/tests -p 'test_*.py' -v`
- [x] `git diff --check`

#### Manual verification
- [ ] Both macOS workflow architectures complete clean builds and relocated package checks.
- [ ] Follow documented build/install/launch commands on a clean Mac; ensure CLI default build still works.

## Phase 4 — sol#high review and revision gate

Review and revise the complete macOS Viewer implementation until every substantiated code-review finding is addressed.

Review the complete diff against this plan and current development, prioritizing bundle closure/RPATH resolution, CMake install/DESTDIR/component correctness, preservation of HID safety fixes and non-Apple behavior, CI truthfulness, and documentation claims. The reviewer reports actionable findings with paths and concrete failing scenarios. Fix every substantiated finding, then rerun portable verification and request reviewer recheck. Do not mark native/hardware qualification complete from mocks.

The sol#high worker may revise any file owned by Phases 1–3 after all three workers have completed. It may extend their existing tests but must ask before introducing other files or expanding into deferred transport/privilege work. Report the original findings, actual revisions, tests, and any unresolved qualification separately. If substantive fixes are made, the planner requests a final read-only recheck of the resulting diff.

The first final read-only recheck reproduced three remaining defects that must be fixed before this gate is complete: fixup needs the actual configuration-specific library output directories (`Release` and multi-config variants); CLI smoke/docs must use the configured runtime output directory; and system-prefix classification must not count a nonexistent `@rpath` candidate as resolved. The last fix must preserve legitimate shared-cache-only system images, use read-only inspection rather than loading arbitrary dependency code, and add missing-system-candidate/next-runpath/cache-only regression cases.

#### Automated verification
- [x] `python3 -m unittest discover -s scripts/tests -p 'test_*.py' -v`
- [x] `python3 -m py_compile scripts/check-macos-viewer-bundle.py`
- [x] `git diff --check`

#### Manual verification
- [ ] macOS native package/GUI/hardware qualification completed by a human; review and mocks alone do not tick this box.

## Phase 5 — Publish the reviewed changes to the user's fork

Create or reuse the authenticated user's GitHub fork and commit/push the reviewed macOS Viewer task branch without modifying upstream.

On 2026-10-08, after implementation and review completion, the user explicitly requested: "make a fork for the github and push all of these changes with luna". Authenticated account verified as `kyeshmz`; requested worker resolved to `openai/gpt-6-luna#max`. This authorization supersedes the earlier no-commit/no-push restriction only for this publishing phase.

Files owned for staging/committing (do not change implementation during publishing):
- `CMake/lrs_options.cmake`
- `tools/realsense-viewer/CMakeLists.txt`
- `tools/realsense-viewer/macos/Info.plist.in`
- `tools/realsense-viewer/macos/install-bundle.cmake.in`
- `scripts/check-macos-viewer-bundle.py`
- `scripts/tests/test_check_macos_viewer_bundle.py`
- `src/ds/d500/d500-motion.cpp`
- `src/hid/CMakeLists.txt`
- `src/hid/hid-device.cpp`
- `src/hid/hid-device.h`
- `wrappers/python/CMakeLists.txt`
- `scripts/tests/test_macos_motion_source.py`
- `.github/workflows/macos-viewer.yml`
- `doc/installation_osx.md`
- `.plans/macos-viewer.md`

Requirements:
1. Verify repository/worktree/branch and inspect all changed and new task files. Explicitly stage the listed files, including the plan; do not stage generated bytecode, toolchain, build artifacts, or unrelated work.
2. Create/reuse a real fork of `realsenseai/librealsense` under `kyeshmz`, normally `kyeshmz/librealsense`. Use GitHub tooling and existing credentials without displaying or persisting tokens. An unrelated same-name repository is a blocker, not an overwrite target.
3. Preserve upstream `origin`; add/reuse a distinct `fork` remote to the verified fork. Preserve existing remote settings if there is a name collision; use another distinct name if needed.
4. Commit all reviewed task changes with an accurate message and existing configured git identity. If no identity is configured, use the authenticated user's name and GitHub noreply address for the command only, without changing global config. Do not add AI coauthor/trailers.
5. Push only `macos-viewer` to the fork with tracking, never upstream, no force push, no mirror/tags/release or PR creation. If a divergent remote task branch already exists, publish a distinct safely named task branch rather than overwrite it, and report the actual name.
6. Verify the remote branch SHA matches the local commit and tracking points to the fork; final working tree must be clean. Report fork URL, branch URL, commit SHA, tests, and any blocker honestly. Native CI/hardware claims remain unchanged; pushing is not a native validation pass.
7. Record automated-verification checkboxes and a publishing execution-log entry before the final task commit so the plan is included in the pushed tree without leftover edits. Remote verification results can be reported in the final handoff instead of editing the plan after pushing.

#### Automated verification
- [x] `env MACOS_VIEWER_TEST_CMAKE=/tmp/opencode/toolchain/cmake-3.31.10-linux-x86_64/bin/cmake MACOS_VIEWER_TEST_NINJA=/tmp/opencode/toolchain/ninja/usr/bin/ninja MACOS_VIEWER_TEST_CC=/tmp/opencode/toolchain/zig-cc TMPDIR=/tmp/opencode python3 -m unittest discover -s scripts/tests -p 'test_*.py' -v`
- [x] `python3 -m py_compile scripts/check-macos-viewer-bundle.py`
- [x] `git diff --check`
- [x] `git diff --cached --check`

#### Manual verification
- [ ] Native macOS CI, GUI, playback, and live USB qualification; these remain separate from publishing.

## Follow-up milestones (not implemented in this task)

1. DDS/Ethernet: rebase #15551 on current development; investigate whether current dependency versions still need static/shared and SO_REUSEPORT workarounds, then separately audit lock-file security, idempotent patch application, C++14 ownership callbacks, install closure and concurrent discovery. Validate D555 depth/color/combined motion, MTU/interface selection, two simultaneous clients, clean rediscovery. Bring #15552 only if example behavior is in scope; #15547 only for Python orientation parity.
2. Normal-user live USB/Finder UX: resolve privilege/WindowServer conflict on supported macOS versions. Consider a narrowly scoped authorized helper/service only after designing authentication, IPC, lifecycle and signing; this is separate architecture/security work, not solved by ad-hoc codesigning.
3. Distribution: developer identity, hardened runtime, dependency signing inside-out, notarization/stapling, clean-machine Gatekeeper checks, DMG and release ownership. Requires credentials and explicit release authorization.
4. Renderer modernization: evaluate consistent OpenGL core rewrite or Metal after feature/performance measurements. Do not combine with the initial package deliverable.

## Out of scope

- New frontend, React/Tauri, Swift UI, Metal renderer, universal2 packaging.
- Merging/rebasing/publishing upstream PRs, pushing upstream, or changing repository release branches. The user's fork/task-branch push is authorized in Phase 5 only.
- DDS, Python orientation, DDS example parity, firmware updates/calibration changes beyond existing Viewer behavior.
- Privilege escalation helpers, entitlement bypasses, SIP/AMFI changes, notarization credentials, public release publishing.

## Halt surfaces

- A worker needs a file outside ownership: stop and ask the planner to amend the plan.
- Unexpected prior edits in an owned file: inspect, preserve and coordinate rather than overwrite.
- Native compiler/macOS/hardware unavailable: execute portable verification, clearly report remaining qualification; do not invent results.
- Removing HIDAPI reveals a different active backend consumer: preserve that consumer and ask before expanding scope.
- BundleUtilities cannot produce self-contained closure for current dependencies: report exact missing image/path and fix within packaging scope; do not mask failures with DYLD variables.

## Definition of done

- Evidence/branch mapping retained in this plan; current development is the base and old parity stacks are not reintroduced.
- Three implementation phases satisfy listed requirements with portable verification passing and sol#high review findings addressed.
- macOS build/GUI/USB and distribution limitations remain visibly pending where not executed.
- Default builds and public APIs are preserved; no changes to the original master working tree. Remote publication is limited to the user's fork/task branch under Phase 5 authorization.

## Execution log

- 2026-10-08: Planner audited upstream PRs and branch ancestry, selected development, created isolated worktree, and resolved requested models. Initial environment has no C++ compiler/CMake and no passwordless sudo. No native/hardware test results claimed.
- 2026-10-08: Phase 3 added separate arm64/x86_64 macOS package CI, relocated-bundle dependency and CLI smoke checks, and current macOS build/privilege documentation. Portable unittest discovery passed (14 tests); `git diff --check` passed. Native macOS builds, GUI/playback, and live USB qualification remain pending.
- 2026-10-08: Dispatched Phases 1, 2 and 3 to separate luna#max workers with non-overlapping file ownership. Inspected the remaining connorsoohoo prototype branch diffs and confirmed runner architecture labels. Bootstrapped checksum-verified CMake 3.31.10 and Zig 0.14.1 plus an extracted Ubuntu Ninja package under `/tmp/opencode/toolchain`, without system installation. An isolated unmodified development baseline configured successfully for Linux RSUSB; this is not macOS validation.
- 2026-10-08: Planner reran all 7 motion source-contract tests successfully. Both changed translation units (`src/hid/hid-device.cpp`, `src/ds/d500/d500-motion.cpp`) compiled into Linux RSUSB object files with Zig's Clang 19.1.7 and the baseline's CMake flags/includes. D500 compilation emitted the existing abstract-class/nonvirtual-destructor warning also observed in the unmodified D400 baseline. A preliminary Zig `-fsyntax-only` attempt returned `FileNotFound`; real `-c` object compilation succeeded. Bundled libusb baseline build required disabling its Linux udev integration because this host lacks libudev headers; no repository code was changed for that environment workaround. The first full baseline core build reached 57/231 steps before the tool's 120-second timeout and was resumed without a timeout; no full-build pass is claimed yet.
- 2026-10-08: Both changed motion translation units also compiled with explicit `-std=c++14`; the public `rs.hpp` header compiled standalone with `-std=c++11`. The new workflow parsed successfully with a scratch-extracted PyYAML package and its triggers, read-only permissions, and arm64/x86_64 runner matrix were checked. These are Linux compile/static checks, not an executed Actions run.
- 2026-10-08: Phase 2 removed the D500 Apple motion exclusion and routed macOS HID through the shared RSUSB interrupt path; removed HIDAPI integration and added portable source-contract tests. Verification passed: the required unittest discovery ran 7 tests (`OK`), and `git diff --check` exited 0 with no output. Native compilation, macOS CI, and live-device qualification remain pending; this Linux environment has no CMake or C++ compiler.
- 2026-10-08: Phase 3 final integrated verification reran after the portable bundle and motion tests were all present: unittest discovery passed 15 tests and `git diff --check` passed. macOS architecture jobs and GUI/USB qualification were not run in this Linux environment.
- 2026-10-08: Phase 3 reran final verification after additional portable test cases landed: unittest discovery passed 16 tests and `git diff --check` passed. macOS architecture jobs and GUI/USB qualification remain pending.
- 2026-10-08: Phase 1 delivered the opt-in macOS Viewer app bundle, component-local resources, install-time BundleUtilities fixup, and portable dependency checker. Required verification passed: bundle checker unittest discovery (9 tests), script py_compile, and `git diff --check`; an additional XML parse of the plist template passed. Native macOS configure/build/install, relocated app inspection/launch, and GUI/hardware checks remain pending.
- 2026-10-08: All three luna#max workers completed; planner dispatched the complete diff and new files to sol#high for review and revisions. Independently compiled all 40 public `.h`/`.hpp` headers in isolation with explicit C++11 using the scratch toolchain (40/40 passed). No public headers were modified.
- 2026-10-08: The full unmodified Linux baseline compiled all core translation units, but the normal link failed because Zig's driver rejects existing `--exclude-libs` arguments. No repository build flags were changed. Separate scratch link checks omitted only those two symbol-export arguments: baseline and modified-motion shared libraries both linked successfully. The modified variant substitutes the two independently compiled C++14 motion objects. Both libraries dynamically loaded, reported API version 25900, and passed three software-device create/delete cycles through ctypes. This is a limited Linux link/API smoke check, not a normal project build pass, native macOS build, GUI, or hardware qualification.
- 2026-10-08: After sol#high revisions, planner independently reran `python3 -m unittest discover -s scripts/tests -p 'test_*.py' -v` (34 tests passed), `python3 -m py_compile scripts/check-macos-viewer-bundle.py` (exit 0), and `git diff --check` (exit 0). The original `/home/kye/librealsense` master checkout remains clean. A fresh sol#high worker is performing the required final read-only recheck; native macOS and live hardware checks remain pending.
- 2026-10-08: Final read-only sol#high recheck found three reproducible remaining defects: missing actual Release/config-specific dylib search dirs at bundle fixup, incorrect CLI executable path in workflow/docs, and false acceptance of missing system-runpath candidates. The gate remains open until all three are revised and rechecked, despite 34 portable tests passing. Planner amended Phase 4 with these requirements and delegated the revision back to sol#high.
- 2026-10-08: sol#high corrected all three remaining findings and expanded verification to 43 tests, including real CMake generation and dependency lookup across single/multi-config outputs. The subsequent independent read-only sol#high recheck found no remaining blocking defects. Planner then independently reran all 43 tests with the scratch CMake/Ninja/compiler explicitly selected (43 passed, no skips), `py_compile` (exit 0), and `git diff --check` (exit 0). Implementation and code-review gates are complete; native macOS builds, actual dyld-cache/fixup/signing/relocation, CI execution, GUI/playback, and live USB remain pending. No commits, pushes, or PR publication were performed.
- 2026-10-08: Phase 4 reviewed the tracked diff against development and all Phase 1–3 new files. Fixed the configure-blocking preset COMPONENT/PATTERN ordering, unreadable plist XML prefix, missing early GLSL prerequisite validation, CMake 3.10 dylib embedding layout, absent post-fixup ad-hoc signing, and host-dependent non-Darwin test. Reworked the checker to inspect each architecture's actual load commands (excluding dylib IDs), reject malformed metadata/embedded dyld environment, validate matching Mach-O dylib slices, use first-match runpath resolution, and preserve executable/inherited-loader contexts. Added regressions; corrected preset-path/branch/signing documentation and explicit CI architecture/GLSL flags plus relocated signature verification. Required commands passed: unittest discovery (34 tests, OK), py_compile (exit 0), and git diff --check (exit 0). The same portable suite passed with a mocked Darwin platform. Scratch CMake 3.31 fixtures passed component resources, prefix override, DESTDIR with spaces, signing-order/failure propagation, and invalid-option guards; the downloaded CMake 3.10.3 module's old dylib default and new override were exercised with CMake 3.31. Workflow/plist static parsing passed. Inspected BundleUtilities RPATH deletion and static/direct rosbag2 SQLite construction; no plugin-runtime-assets finding or motion-source revision was needed. Fixture setup initially failed until the mock module path was propagated; no native fixup/signing execution is claimed. Native macOS builds, relocated real Mach-O/signature execution, GUI/playback, and USB qualification remain pending. Substantive fixes require the planner's final read-only recheck.
- 2026-10-08: Phase 4 follow-up addressed all three findings from the independent recheck. Bundle fixup now receives configuration-specific realsense2/realsense2-gl target directories through file(GENERATE), selected at install time without relying on post-3.10 install generator-expression support. Corrected ordinary CLI paths in workflow/docs to Release/realsense-viewer. System dependency resolution now requires an on-disk file or affirmative read-only _dyld_shared_cache_contains_path membership via ctypes.CDLL(None), including direct system loads; unavailable cache inspection fails closed. Mocks explicitly model system availability. Required unittest discovery passed 43 tests with no skips, using the scratch CMake/Ninja/compiler selected via MACOS_VIEWER_TEST_* environment variables and TMPDIR=/tmp/opencode; py_compile and git diff --check exited 0. The added generation fixture ran real CMake 3.31.10 with Ninja single-config Release and Ninja Multi-Config Release/Debug, real GetPrerequisites gp_resolve_item lookup, distinct GL output directories, install-prefix overrides and DESTDIR with spaces; generated CLI paths also matched actual target paths. Cache regressions cover missing first candidate followed by a valid bundled image, missing everywhere, missing direct system loads, cache-only availability, on-disk availability, read-only runtime lookup, and unavailable-query rejection. The first new run exposed the exact /usr/lib root classification edge case, which was fixed before the passing rerun. These are Linux generation/mock tests, not native macOS compilation, fixup/signing, shared-cache execution, GUI, or USB qualification. Motion files were not modified. Planner final read-only recheck remains required.
- 2026-10-08: Phase 5 publication verification passed: explicit-toolchain unittest discovery (43 tests, no skips), `py_compile`, working-diff whitespace check, and staged task-file whitespace check. GitHub confirmed account `kyeshmz`, but fork creation was rejected with HTTP 403 (`Resource not accessible by personal access token`); the `gh` executable is not installed. No fork remote, local commit, or push was created, so publication is blocked pending GitHub fork-creation permission. Native macOS CI and live hardware qualification remain pending.
- 2026-10-08: Phase 5 resumed after the user created `kyeshmz/librealsense`. Read-only GitHub repository metadata verified `fork: true` and both `parent` and `source` as `realsenseai/librealsense`; the authenticated user remains `kyeshmz`. Added the distinct `fork` remote while preserving `origin`; read-only `git ls-remote` confirmed no existing `macos-viewer` branch on the fork. Re-ran required pre-commit checks: explicit-toolchain unittest discovery passed 43 tests without skips, `py_compile`, `git diff --check`, and `git diff --cached --check` exited 0. These are portable Linux tests, not native macOS or hardware qualification. Commit and fork-only push are the remaining Phase 5 operations.
