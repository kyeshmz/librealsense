# macOS connected-camera detection: iterative implementation and review

## Outcome

Make the connected RealSense D405 visible to the native Viewer on `macos-viewer`, with evidence from this Mac, not merely package-build checks. Iterate diagnosis → bounded implementation → independent luna#max review → live verification → revised plan until detection works or a specific human-only hardware/authorization step blocks progress.

## Conventions in force

- Preserve the existing fork branch and all prior work; keep changes minimal and match surrounding code. Do not reformat unrelated code.
- Core C++ remains C++14; public APIs retain C++11 compatibility and ABI. Guard macOS-only behavior and preserve other backends.
- New source files require Apache 2.0 and 2026 RealSense copyright headers. Declare any new CMake option in `CMake/lrs_options.cmake`; preserve CMake 3.10 compatibility.
- Only Phase 4 may commit or push. Phase 4 is authorized by the user's explicit request to commit all our task changes with luna and push to the existing fork; it may commit/push only reviewed task files to `kyeshmz/librealsense:macos-viewer`. No releases, upstream writes, force-pushes, or unrelated work.
- Do not update firmware, disable SIP/AMFI, add restricted entitlements, change system permissions, or install privileged helpers. Never request or collect a password. Without explicit human authorization, `sudo -n` may only check existing authorization; interactive sudo needs the human.
- Only one worker may use the camera at a time. Bound hardware commands and GUI processes; terminate only processes created for this task.
- Workers own only their phase files. They may tick their own automated checkboxes and append execution-log entries; other plan text belongs to the planner. Manual checkboxes belong to the human.
- Build and diagnostic artifacts belong under ignored `build-macos-detection/` or the approved OpenCode temporary directory. Distinguish OS USB visibility, SDK construction/enumeration, GUI detection, and actual frame delivery.

## Evidence

- Workspace: `/Users/kyeshmz/Documents/wholeearth/librealsense`, Apple Silicon arm64, macOS 15.5. CMake, Ninja, Homebrew libusb 1.0.30, GLFW, and librealsense 2.58.4 are installed.
- Original checkout was clean `master`. User clarified existing branch is remote. Found `kyeshmz/librealsense:macos-viewer`, fetched it via newly added `fork` remote and switched to its tracking branch at `82a4d4ff8948f9e8fe5142bac639543f16e3386f`. Upstream `origin` is unchanged.
- `system_profiler SPUSBDataType` detects Intel RealSense Depth Camera 405, VID:PID 8086:0b5b, USB 3 at 5 Gb/s through a VIA hub. Available current 900 mA; required 720 mA.
- Installed `/opt/homebrew/bin/rs-enumerate-devices` fails with interface 0 `RS2_USB_STATUS_ACCESS`, `failed to set power state`, and no device detected. This binary is upstream 2.58.4, NOT proof of the fork's runtime behavior.
- `sudo -n true` reports a password is required. Do not assume privileged runtime tests can be completed automatically.
- Human ran the native AppleClang branch enumerator with local `sudo` authorization and supplied output confirming a constructed RealSense D405, firmware 5.15.1.55, USB descriptor 3.2, and depth/color/IR profiles. This is human-provided SDK detection evidence, not yet Viewer visibility or frame delivery.
- Human subsequently launched the native AppleClang Viewer directly with `sudo`, confirmed the D405 is listed, and then corrected the apparent no-image failure: the Stereo Module was off; turning it on produces working live streaming. Startup printed `GLFW Driver Error: Cocoa: Regular windows do not have icons on macOS`, which did not block the confirmed camera/stream operation. Normal-user Finder camera access remains unqualified.
- Upstream master `CONTRIBUTING.md` was fetched and compared with the local copy: exact matching SHA256 `cdd788685073cf8a76a5bd6babc199cdf05c86ac723b37a36f13d3bb5799d92f`. It explicitly requires contributions on feature branches based on `development` and PRs targeting `development`, not stable master. Human selected `development (Recommended)` after clarification.
- Fresh `git fetch origin master development` confirms branch base `1398ecbaf` remains an upstream development ancestor. Existing fork branch has only 3 unique task commits relative to current upstream development at `efd6a8202`; 845 relative to master are not 845 user changes. Upstream development advanced 10 commits after the branch base. No history rewrite is needed merely to correct the comparison; preserve the working tested branch and use upstream development as comparison/PR target.
- `scripts/pr_check.sh` unconditionally attempts `sudo apt-get install dos2unix` and requires GNU grep `-P`; do not run its package installation or `--fix` on this Mac. Perform equivalent read-only license/copyright/tab/CRLF checks on task source instead. `api_check.sh` creates/deletes files in its working directory and uses unqualified g++; planner safely performed its intent in an approved temporary directory with `/usr/bin/clang++ -std=c++11 -fsyntax-only`, independently compiling 40/40 public headers.
- Existing `.plans/macos-viewer.md` documents earlier packaging/release work on a different host. Preserve it unchanged; its old publishing authorization and qualification results do not authorize new publishing or prove live-camera access here. This task has its own plan.
- Branch already contains Darwin per-device capture leases in `src/libusb/darwin-device-capture.*`, explicit capture in `usb_device_libusb` construction, bounded waits/settling, and app-bundle packaging. Guide explicitly warns normal-user capture may fail and sudo GUI can fail WindowServer access.
- Context7 libusb docs consulted: active kernel drivers prevent interface claim/I/O; detach enables claiming; claiming itself is a logical operation. Source: https://github.com/libusb/libusb/blob/master/core.c . Darwin permission behavior must be verified, not guessed from this generic API contract.

## Existing code to reuse

- `.github/copilot-instructions.md`, `.github/skills/build.md`, `cpp_coding.md`, `file_creation.md`, `testing.md`: project conventions and native build/test recipes.
- `doc/installation_osx.md`: branch-native Viewer build recipe with optional network features disabled.
- `src/libusb/darwin-device-capture.cpp`, `device-libusb.cpp`, `handle-libusb.h`, `context-libusb.cpp`: capture, claim, error propagation and initialization.
- `tools/enumerate-devices`, `examples/capture`, existing portable script tests and bundle checker.

## Phase 1 — Establish native branch baseline and precise blocker

Build the existing fork natively and identify the first failure between USB visibility and SDK camera detection.

Files owned:
- [NEW generated artifacts only] `build-macos-detection/**`

Requirements:
1. Read project instructions and applicable repository skill files. Do not edit tracked source in this phase.
2. Configure Release native arm64 with RSUSB, examples/graphical examples/GLSL/tools enabled, DDS/update checks/AI/stats disabled, ordinary CLI Viewer. Prefer installed Homebrew dependencies; use `/opt/homebrew/bin/cmake` if the default executable differs.
3. Build `rs-enumerate-devices` and `realsense-viewer`; bound diagnostics. Check linked dependency paths to prove local branch library is in use.
4. Run local enumeration with useful existing debug logging, capture exact failure code/stage, and compare with OS USB detection. Investigate the current libusb Darwin capture implementation and version-specific behavior through current primary sources/docs when needed.
5. If enumeration succeeds, perform a bounded live depth frame probe using existing SDK API, then bounded GUI launch. If privileges block capture, do not endlessly retry or bypass security. Report the exact human command needed and whether a focused code/diagnostic improvement is justified.
6. Give concrete proposed source changes and regression-test strategy for the planner to scope the next phase. Report partial build failures as blockers rather than broad unsolicited fixes.

#### Automated verification
- [x] `/opt/homebrew/bin/cmake -S . -B build-macos-detection -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH=/opt/homebrew -DFORCE_RSUSB_BACKEND=ON -DBUILD_EXAMPLES=ON -DBUILD_GRAPHICAL_EXAMPLES=ON -DBUILD_GLSL_EXTENSIONS=ON -DBUILD_TOOLS=ON -DBUILD_MACOS_VIEWER_BUNDLE=OFF -DBUILD_WITH_DDS=OFF -DCHECK_FOR_UPDATES=OFF -DENABLE_AI_ASSISTANT=OFF -DENABLE_STATS=OFF`
- [ ] `/opt/homebrew/bin/cmake --build build-macos-detection --target rs-enumerate-devices realsense-viewer --parallel 6`
- [x] `git diff --check`

#### Manual verification
- [ ] Human authorizes any needed privileged camera test without sharing passwords.

## Phase 2 — Evidence-scoped implementation

Implement the smallest verified fix or actionable diagnostic needed for this Mac's connected camera.

Files owned:
- [MODIFY] `src/libusb/darwin-device-capture.cpp`
- [MODIFY] `src/libusb/enumerator-libusb.cpp`
- [NEW only if needed for executable testing] `src/libusb/darwin-capture-error.h`
- [NEW] `scripts/tests/test_darwin_capture_errors.py`
- [MODIFY] `doc/installation_osx.md`
- [generated artifacts only] `build-macos-detection/**`

Requirements:
1. Preserve capture lease timing, registry synchronization, NOT_FOUND handling, detach/attach lifecycle, cleanup, and non-macOS behavior. No capture retry or authorization bypass; an actionable error is not a camera-access fix.
2. Preserve libusb error name, numeric status, operation, and interface in all three constructor failures (open, auto-detach configuration, explicit detach). Include concise macOS permission guidance only for ACCESS, accurately noting elevated capture can require local authorization and that sudo may not resolve every restriction. Do not claim sudo always works or suggest entitlements/security changes.
3. Catch constructor exceptions by const reference in the enumerator rather than slicing; include `e.what()` and correct numeric index in its existing warning. Do not broaden catch scope or remove null-device behavior.
4. Add executable hardware-free regression coverage for ACCESS, BUSY, NO_DEVICE, OTHER and operation/interface formatting. If needed, extract only a small private inline formatter into the owned header. Tests must exercise production formatting with a compiler and controlled libusb error names, not only search strings. State compile skips honestly on hosts lacking compilers; no skips expected on this Mac. Also guard exception-preserving enumeration source integration.
5. Update macOS instructions to explicitly select Xcode `/usr/bin/clang` and `/usr/bin/clang++` in both existing build recipes, explain using a fresh build directory when changing compilers, and provide a short OS USB versus SDK enumeration troubleshooting procedure using the matching build, active desktop Terminal, and human-local authorization. Preserve signing, WindowServer, and security caveats.
6. Rebuild native AppleClang SDK and Viewer and run all portable tests. Do not access camera or run GUI in this phase; the human may be running the separately requested privileged baseline test. Keep required compiler/API compatibility and changes surgical.

#### Automated verification
- [x] `/opt/homebrew/bin/cmake --build build-macos-detection/apple-clang --target rs-enumerate-devices realsense-viewer --parallel 6`
- [x] `python3 -m unittest discover -s scripts/tests -p 'test_*.py' -v`
- [x] `git diff --check`

#### Manual verification
- [ ] Human runs matching SDK enumeration with locally authorized capture if necessary.

## Phase 0 — Read-only Viewer diagnostic review

Identify whether the existing Viewer clearly exposes USB capture failures and recommend the smallest user-actionable diagnostic improvement.

Files owned: none; read-only review, no hardware/build commands and no plan edits.

Requirements:
1. Trace native Viewer startup/device refresh/error notification paths in `tools/realsense-viewer/`, `common/viewer*`, and `common/notifications*`.
2. Do not duplicate Phase 1's build, USB backend research, or camera access. Determine what the user sees when SDK construction/discovery fails despite OS USB presence.
3. Give actionable findings only, with file/line, concrete failure scenario, and a minimal remedy preserving non-macOS behavior. No speculative security bypass or privileged helper.

#### Automated verification
- [x] `git diff --check`

#### Manual verification
- [ ] Human verifies readability of any resulting Viewer error message.

## Phase 3 — Independent review and live qualification

Review the completed change independently and verify whether the connected D405 is visible and delivering frames.

Files owned: none; source and plan are read-only.

Requirements:
1. Review the Phase 2 diff independently for error preservation, correct ACCESS-only guidance, unchanged cleanup/capture lifecycle and non-Apple behavior, honest compiler/troubleshooting instructions, and executable regression coverage.
2. Run portable tests and whitespace checks; no camera access or GUI launch without explicit planner dispatch. Report concrete actionable findings, with source paths and scenarios. Do not fix source during review.
3. Camera enumeration/frame/GUI qualification is a separate planner-run or human-authorized check after review. If findings or live failures remain, planner appends a newly scoped revision phase and delegates implementation and fresh review again. No mock/compile pass qualifies actual camera access.

#### Automated verification
- [x] `python3 -m unittest discover -s scripts/tests -p 'test_*.py' -v`
- [x] `git diff --check`

#### Manual verification
- [ ] Native Viewer displays the connected D405 in the active desktop session.
- [ ] Live frames are delivered from the connected D405.

## Phase 4 — Publish reviewed task changes with luna

Commit all reviewed camera-detection task changes and push the existing macos-viewer branch to the user's existing fork.

User explicitly authorized: "commit all our changes with luna and pus hit to the fork that we have". Use `openai/gpt-6-luna#max`. This phase may start only after Phase 2 completion and independent review has no substantiated blocking findings, including Phase 5/7 revisions and Phase 8 final recheck for the documentation findings.

Files owned for staging/commit:
- `src/libusb/darwin-device-capture.cpp`
- `src/libusb/enumerator-libusb.cpp`
- `src/libusb/darwin-capture-error.h` if created by Phase 2
- `scripts/tests/test_darwin_capture_errors.py`
- `doc/installation_osx.md`
- `.plans/macos-camera-detection.md`

Requirements:
1. Verify branch is `macos-viewer`, origin remains upstream and fork URL is `https://github.com/kyeshmz/librealsense.git`. Inspect status/diff and stage only the reviewed task files explicitly. Do not stage build artifacts, generated bytecode, existing `.plans/macos-viewer.md`, or unrelated edits. Do not make source revisions in publication.
2. Rerun all portable tests, check working/staged whitespace and confirm the native AppleClang targets build. Distinguish human-reported successful authorized D405 Viewer/live streaming from automatically verified tests; no unprivileged support claim.
3. Record phase verification and publication preparation in this plan before staging and committing it. Use configured identity and a concise, accurate one-sentence commit message with no AI attribution/trailers. No global Git configuration changes.
4. Push only `macos-viewer` to `fork` without force, tags, upstream writes, releases, PR creation or changing branch names. If remote has advanced/diverged, stop and report rather than overwrite or silently reconcile.
5. Verify local HEAD equals remote branch SHA via `git ls-remote`; confirm tracking fork/macos-viewer. Report commit SHA, branch URL, tests, and clean task status. Do not edit plan after successful push merely to record the SHA; return it in handoff.
6. Preserve the CONTRIBUTING-correct development base; user explicitly chose it. `CONTRIBUTING.md` already exactly matches upstream master and must not be rewritten. Do not rebase onto master, merge/rebase upstream merely to hide comparison history, or rewrite published commits. Report the comparison against upstream development so unrelated upstream commits are not represented as ours. No PR creation was requested.
7. Run read-only task-file license/copyright/tab/CRLF equivalents of `pr_check.sh`, without its Ubuntu-only package installation and without auto-fix. Planner already independently compiled all 40 public headers standalone in C++11; cite that accurately rather than claiming the original script ran verbatim. No public API headers changed.

#### Automated verification
- [x] `/opt/homebrew/bin/cmake --build build-macos-detection/apple-clang --target rs-enumerate-devices realsense-viewer --parallel 6`
- [x] `python3 -m unittest discover -s scripts/tests -p 'test_*.py' -v`
- [x] `git diff --check`
- [x] `git diff --cached --check`

#### Manual verification
- [ ] Normal-user Finder access remains separate and unqualified; do not mark from sudo live test.

## Phase 5 — Revise the documented CLI build target

Ensure the documented CLI build produces the enumerator used by the troubleshooting commands.

Files owned:
- [MODIFY] `doc/installation_osx.md`
- [MODIFY] `scripts/tests/test_darwin_capture_errors.py`

Requirements:
1. Independent Phase 3 review found the CLI recipe builds only `realsense-viewer` but troubleshooting later runs `build-viewer-cli/Release/rs-enumerate-devices`, a separate target. Include `rs-enumerate-devices` in that CLI recipe's build command so a fresh documented build has both binaries.
2. Add a minimal portable documentation regression asserting that the CLI build recipe explicitly builds the enumerator it later invokes. Keep the previous executable formatter regression and source integration check intact.
3. No other source/doc changes, hardware commands, commits, or pushes. Do not broaden into packaging or modify prior task plans. Run full portable suite and whitespace check; append revision evidence.

#### Automated verification
- [x] `python3 -m unittest discover -s scripts/tests -p 'test_*.py' -v`
- [x] `git diff --check`

#### Manual verification
- [ ] Human following a fresh CLI recipe obtains both Viewer and enumerator.

## Phase 6 — Independent revision recheck

Confirm the documentation blocker is fixed and no substantiated review blockers remain before fork publication.

Files owned: none; source and plan read-only.

Requirements:
1. Re-review Phase 5 changed documentation/test against original Phase 3 finding; check multi-target CMake command is valid and fresh build produces both target paths.
2. Recheck integrated diagnostic diff for new regressions, run full portable suite and whitespace check, and report explicit pass/findings. Do not edit files, run hardware/GUI, commit or push.
3. Contribution base is resolved: user explicitly selected development, local CONTRIBUTING.md exactly matches upstream. No branch rewrite is needed.

#### Automated verification
- [x] `python3 -m unittest discover -s scripts/tests -p 'test_*.py' -v`
- [x] `git diff --check`

#### Manual verification
- [ ] Unprivileged Finder support remains unqualified.

## Phase 7 — Align elevated Viewer guidance with tested development workflow

Document direct elevated development Viewer launch accurately without removing WindowServer or normal-user access caveats.

Files owned:
- [MODIFY] `doc/installation_osx.md`
- [MODIFY] `scripts/tests/test_darwin_capture_errors.py`

Requirements:
1. Replace the absolute "Do not run the GUI with sudo" instruction with a conditional local-development path: after successful authorized enumeration, a human may launch the matching Viewer executable directly with local sudo authorization in the active desktop Terminal; it may fail WindowServer access on some systems. Do not use sudo open or promise unprivileged/Finder support.
2. Provide the matching `sudo ./build-viewer-cli/Release/realsense-viewer` command and mention enabling the Stereo Module stream after discovery, reflecting this human-confirmed session. Preserve no-SIP/AMFI/restricted-entitlement advice and no password sharing.
3. Add minimal documentation regression retaining direct elevated launch guidance, conditional WindowServer warning, and explicitly unqualified normal-user/Finder access. Preserve prior target/formatter tests. No source changes or hardware commands.

#### Automated verification
- [x] `python3 -m unittest discover -s scripts/tests -p 'test_*.py' -v`
- [x] `git diff --check`

#### Manual verification
- [ ] Guide remains honest about normal-user/Finder access not being verified.

## Phase 8 — Final read-only documentation recheck

Confirm both documentation findings are fixed and clear the reviewed diagnostic changes for fork publication.

Files owned: none; read-only.

Requirements:
1. Check only the revised elevated-development guidance and new regression against Phase 6 finding, and confirm CLI target fix remains. No need to repeat already completed hardware qualification or full source review.
2. Run complete portable suite and whitespace check; explicitly report no remaining blockers or precise new issue. No edits/hardware/commits/pushes.

#### Automated verification
- [x] `python3 -m unittest discover -s scripts/tests -p 'test_*.py' -v`
- [x] `git diff --check`

#### Manual verification
- [ ] Human-only normal-user qualification remains pending.

## Out of scope

- Unrelated earlier packaging milestones, DDS, D500 IMU parity, renderer modernization, public API additions, firmware flashing, release/PR publication, and upstream writes. Phase 4 fork branch publication is explicitly authorized.
- Security bypasses, automatic privilege escalation, privileged-helper architecture, dependency upgrades without direct evidence.

## Halt surfaces

- Need to edit outside owned paths: stop and request revised scope.
- Human-only password, authorization, camera privacy, cable replug, or desktop access is required: report exact evidence and safe next action; do not fabricate success.
- Unexpected prior edits or another process holding the camera: preserve user work and coordinate.
- Build or test failure: retain logs, identify stage, and request the next plan rather than claiming done.

## Definition of done

- Native branch builds, independent luna#max review has no remaining substantiated blockers, SDK sees the actual connected D405, and native Viewer displays that device.
- A bounded live frame probe provides additional streaming evidence where possible; GUI/device/frame checks are stated separately and honestly.
- If external authorization/hardware intervention is unavoidable, report task blocked with the exact action needed; do not equate OS enumeration, mocks, or compilation with camera detection.

## Execution log

- 2026-10-08: Planner found and checked out the user's remote branch, verified OS USB presence and installed SDK access failure, resolved requested model to `openai/gpt-6-luna#max`, and created this hardware-specific plan. Old packaging plan preserved unchanged.
- 2026-10-08 Phase 1: Prescribed Release/arm64 configure succeeded. Its required combined build failed under PATH-selected Homebrew Clang 23 in `rs-enumerate-devices`/Viewer build work at `include/librealsense2-gl/rs_processing_gl.hpp:346-356` (enum values 128-135 outside `[0,127]`); the enumeration target built separately. Reconfigured under `build-macos-detection/apple-clang` with Xcode AppleClang 17 and both `rs-enumerate-devices` and `realsense-viewer` built successfully; Viewer `--version` returned 2.59.0.0. Local dependencies resolve to Homebrew libusb (`/opt/homebrew/opt/libusb/lib/libusb-1.0.0.dylib`) and GLFW (`/opt/homebrew/opt/glfw/lib/libglfw.3.dylib`), with the SDK dylibs resolved from the matching build's `Release` RPATH.
- 2026-10-08 Phase 1 live diagnosis: `system_profiler SPUSBDataType` sees the D405 (`8086:0b5b`, USB 5 Gb/s). The locally built SDK debug enumerator sees 7 libusb devices and one RealSense candidate, but fails constructing it (`cannot access depth sensor`; final `No device detected`, exit 1). A bounded libusb probe found `libusb_open` and `libusb_set_auto_detach_kernel_driver(false)` succeed, then `libusb_detach_kernel_driver(interface 0)` fails with `-3 (LIBUSB_ERROR_ACCESS)`. No GUI detection or frame-delivery test was run because SDK construction fails first. Full probe output is retained under ignored `build-macos-detection/diagnostics/`.
- 2026-10-08 Phase 1 handoff: No safe code change can grant macOS interface-capture authorization. Human-only next test: from the active logged-in desktop Terminal, run `sudo /Users/kyeshmz/Documents/wholeearth/librealsense/build-macos-detection/apple-clang/Release/rs-enumerate-devices --debug` and enter any authorization locally without sharing it. If implementation is later scoped, the smallest actionable diagnostic is to include libusb status/name in errors from `src/libusb/darwin-device-capture.cpp` and log the caught exception in `src/libusb/enumerator-libusb.cpp`; verify with a hardware-free message-format test if a helper is extracted, AppleClang build, and the human-authorized live CLI test. No tracked source was changed.
- 2026-10-08: Phase 0 luna#max static review found generic empty-device UI, SDK diagnostics confined to the initially closed console, a refresh catch that logs without populating the existing error popup, and pre-loop discovery exceptions that only reach stderr. No source changed or live UI test claimed. Planner independently ran existing portable suite (52/52 passed) and checked downloaded app dependency closure, ad-hoc signature, and CLI version 2.59.0; these are not live-camera tests. Phase 1 native baseline remains in progress.
- 2026-10-08: Phase 1 completed blocked on human-local capture authorization; native AppleClang build succeeded but Homebrew Clang failed on existing GL enum extensions. Planner revised Phase 2 to preserve exact capture errors, stop exception slicing, add executable regression coverage, and document the verified Xcode compiler path. Human privileged enumeration is required before further hardware qualification; diagnostic changes cannot grant USB privileges.
- 2026-10-08 Phase 2: Added libusb name/code/operation/interface diagnostics to all three Darwin capture constructor failures with macOS authorization guidance only for `LIBUSB_ERROR_ACCESS`; kept the `NOT_FOUND` branch and cleanup lifecycle intact. Enumerator now logs `e.what()` from a const-reference catch and streams a numeric index. Added compiled formatter coverage using controlled libusb error names for ACCESS, BUSY, NO_DEVICE, and OTHER, plus an enumerator integration guard. Both macOS build recipes now select Xcode Clang explicitly; docs cover fresh compiler build directories and human-local OS USB versus SDK troubleshooting. Required AppleClang build, all 54 portable tests, and `git diff --check` passed. No camera or GUI was accessed; live SDK enumeration and Viewer/frame qualification remain pending human-local authorized capture testing.
- 2026-10-08: Human supplied authorized native enumeration output: D405 construction succeeds, firmware 5.15.1.55, USB 3.2, depth/color/IR profiles present. The permission hypothesis is confirmed for this Mac; no security settings or firmware changes are needed for this SDK test. Next qualification is a direct human-local authorized native Viewer launch and stream check. Phase 2 worker is implementing diagnostic preservation only and is prohibited from camera access.
- 2026-10-08: Human confirms native Viewer displays D405 and live streaming works after enabling the initially disabled Stereo Module. This meets hardware/UI goals through human observation using local sudo authorization; no frame-count benchmark or unprivileged Finder support is claimed. The Cocoa window-icon warning is nonblocking in this tested session. Finish the in-flight minimal diagnostic implementation and independent review; no speculative transport/security fix is justified now.
- 2026-10-08: User explicitly authorized luna to commit all our task changes and push to the existing fork. Planner added Phase 4 with explicit task-only staging, native/portable checks, independent-review prerequisite, fork-only non-force push, and remote SHA verification. Implementation remains in flight; no publishing has occurred yet.
- 2026-10-08: User questioned master comparison and requested CONTRIBUTING compliance. Planner fetched the linked upstream CONTRIBUTING.md, verified local copy is identical, fetched current origin/master/development, and confirmed only 3 preexisting fork commits are unique relative to development. User explicitly selected development after clarification. No branch rewrite is necessary; publish existing feature branch and compare it to upstream development, preserving all prior work. Planner independently passed standalone C++11 compilation of 40/40 public headers in an approved temporary directory; original Ubuntu-specific PR script is unsafe/incompatible to run verbatim on this host. Phase 3 independent luna#max review is in flight.
- 2026-10-08: Phase 3 independent luna#max review passed 54 tests and whitespace but found one blocker: documented CLI build omits the separate rs-enumerate-devices target used by later troubleshooting. No source/lifecycle/permission-guidance blocker found. Planner added Phase 5 bounded documentation/test revision and Phase 6 independent recheck; publication remains gated on their completion.
- 2026-10-08 Phase 5: Updated the documented CLI build to explicitly build both `realsense-viewer` and `rs-enumerate-devices`. Added a portable documentation regression tying that multi-target recipe to the enumerator path used by troubleshooting; retained the compiled formatter and enumerator integration tests. Full portable suite passed (55 tests) and `git diff --check` passed. No hardware, source, commit, or push activity.
- 2026-10-08: Server restarts interrupted Phase 6 dispatch; disk state preserved completed Phase 2/5 work with no commit/push. Fresh luna#max Phase 6 reviewer passed 55 tests and whitespace, confirmed missing-enumerator recipe fixed, and found only the absolute sudo-GUI prohibition contradicts the human-tested development workflow. Planner scoped Phase 7 guidance/test revision and Phase 8 final documentation-only recheck. No source or hardware work needs repeating.
- 2026-10-08 Phase 7: Replaced the blanket sudo-GUI prohibition with the conditional direct development launch `sudo ./build-viewer-cli/Release/realsense-viewer` after successful authorized enumeration, retained the conditional WindowServer caveat, prohibited `sudo open`, and documented enabling the Stereo Module stream. Normal-user USB/Finder qualification and security/password caveats remain unchanged. Added a documentation regression while preserving prior target, formatter, and source-integration tests. Full portable suite passed (56 tests) and `git diff --check` passed; no hardware or GUI commands were run.
- 2026-10-08 Phase 8: Independent luna#max recheck found no blocking issues. Both documentation findings are resolved, CLI builds both targets, direct elevated development launch matches human-tested usage while retaining conditional WindowServer and unqualified normal-user/Finder caveats. Full portable suite passed 56 tests; whitespace passed. Hardware success remains human-reported. Planner cleared Phase 4 publication of the six task files with no branch rewrite or upstream writes.
- 2026-10-08 Phase 4 pre-publication: Incremental AppleClang target build passed (`ninja: no work to do`), the portable Python suite passed all 56 tests, and `git diff --check` passed. Read-only `pr_check.sh` equivalents passed for the three changed/new C++ files and new Python test: Apache 2.0 and copyright notices are present, with no tabs or CRLF. The Ubuntu-only script itself was not run. Pending working changes are exactly the six Phase 4 task paths; no source edits were made in this phase.
- 2026-10-08 Phase 4 publishing preparation: Confirmed `macos-viewer` tracks `fork/macos-viewer` at the expected baseline `82a4d4ff8948f9e8fe5142bac639543f16e3386f`; the fork remote URL is `https://github.com/kyeshmz/librealsense.git` and `origin` remains upstream. Fetched `origin/development`; the preserved branch has 3 existing unique commits ahead and 10 behind that development comparison, so no history rewrite is appropriate. Configured author and committer identities are available. Plan to stage only the six Phase 4 paths and commit as `Improve macOS USB capture diagnostics and setup guidance`; human-only normal-user/Finder qualification remains unclaimed.
- 2026-10-08 Phase 4 staging: Explicitly staged exactly the six owned paths; `git diff --cached --check` passed with no whitespace errors. No generated or unrelated paths are staged.
