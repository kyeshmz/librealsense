# Jetson binary builds

## Goal
Build downloadable ARM64 librealsense SDK binaries with GitHub Actions in a public fork, validate target images against JetPack/L4T kernel families, and iterate Luna max implementation with Sol review.

## Conventions in force
- All work is on branch `jetson-binary-builds`; do not modify upstream or the existing macOS branch.
- Add an isolated workflow and scripts; preserve upstream workflows and kernel patch scripts.
- Use Python standard library and unittest for tooling; no dependency required for local tests.
- Bash scripts use `set -euo pipefail`, quote paths, fail closed on unknown targets and invalid architectures.
- Pin external Actions to verified full commit SHAs and container images to verified ARM64 manifest digests.
- No embedded credentials, privileged containers, kernel insertion, automatic hardware configuration, or hardware-test claims.
- Report host kernel separately from target kernel family: containers do not have independent kernels.
- Package both RSUSB and native variants separately; CUDA, graphical examples, Python bindings and ROSBAG2 are disabled and explicitly documented.
- Portable ARM64 baseline: no host-specific `-march=native` or host-only optimizations.
- Never mark manual verification complete or claim CI passed without observing a successful run.
- Implementers do not commit or push; the coordinator owns publication and plan text.

## Evidence
- Upstream commit `e15c5d6bb` on master matches public fork `kyeshmz/librealsense/master` as inspected 2026-10-08. Fork creation through MCP returned 403, but existing public fork and SSH authentication are available.
- https://developer.nvidia.com/embedded/linux-tegra-r3276 : JetPack 4.6.6 / L4T 32.7.6 / Ubuntu 18.04 / kernel 4.9; Nano, TX1, TX2 and Xavier; EOL.
- https://developer.nvidia.com/embedded/jetson-linux-r3564 : JetPack 5.1.6 / L4T 35.6.4 / Ubuntu 20.04 / kernel 5.10; Xavier and Orin.
- https://developer.nvidia.com/embedded/jetson-linux-r3644 : JetPack 6.2.1 / L4T 36.4.4 / Ubuntu 22.04 / kernel 5.15; Orin only.
- https://developer.nvidia.com/embedded/jetpack/downloads/archive-7.0 : JetPack 7.0 / L4T 38.2.1 / Ubuntu 24.04 / kernel 6.8; Thor T5000 only for this reference release.
- https://developer.nvidia.com/embedded/jetson-linux-archive : newer releases exist. The four reference releases above are deliberately bounded, not advertised as latest.
- https://docs.github.com/en/actions/reference/runners/github-hosted-runners : public repository standard Linux ARM64 runner `ubuntu-24.04-arm`; Docker steps run on host so modern Node Actions never execute inside Bionic.
- Context7 NVIDIA documentation confirms r36.4.4 kernel 5.15.148; r38.2.1 kernel 6.8.12 and Thor-only support.
- Container images are Ubuntu ARM64 userlands, NOT NVIDIA BSP images; sufficient for CPU-only SDK compilation, not CUDA or kernel module compilation. Live Docker Registry image/config checks must verify digest, OS, architecture, and distro from image layers or container runtime.
- Local machine is x86_64; has Python3/PyYAML/git/curl, lacks compiler/CMake/Docker/gh; passwordless sudo unavailable. Local contract tests can run, actual ARM64 builds require Actions or separately provisioned tooling.

## Existing code to reuse
- `CMake/lrs_options.cmake`: flags for backends, CUDA, tools, headless build, ROSBAG2, NEON.
- `CMake/install_config.cmake`: staged SDK, headers, CMake and pkgconfig exports.
- `config/99-realsense-libusb.rules`: USB device permission rules; installation is manual.
- `scripts/patch-realsense-ubuntu-L4T.sh`: board-side native backend kernel patching. Never run in CI; its supported exact releases differ from userspace binary targets.
- `.github/workflows/buildsCI.yaml`: SHA-pinned checkout style.

## Phase 1
### Outcome
Produce a validated four-userland ARM64 build matrix and digest-pinned headless SDK archives with recorded dependencies for both backends through an isolated GitHub Actions workflow.

### Files
- [NEW] `.github/workflows/jetson-binaries.yml`
- [NEW] `scripts/jetson/targets.json`
- [NEW] `scripts/jetson/jetson_ci.py`
- [NEW] `scripts/jetson/build.sh`
- [NEW] `scripts/jetson/check-device.sh`
- [NEW] `scripts/jetson/tests/test_jetson_ci.py`

### Requirements
- Single source of truth targets IDs `jp4`, `jp5`, `jp6`, `jp7` with JetPack, reference L4T, Ubuntu version, kernel family, boards, source URL, actual ARM64 image digest. Restrict intended support to reference targets; matching families may be candidates, not automatically certified exact releases.
- Tool CLI `python3 scripts/jetson/jetson_ci.py validate`, `matrix --target all --backend both`, and `verify-images`; validate checks data offline, matrix supports all or target ID and both/rsusb/native. Use CLI additional commands as needed and document them.
- Live verify-images resolves digest and checks registry config architecture/OS, with hash verification. Fail on missing digest, wrong architecture, wrong content hash; do not claim distro validation from Docker tag alone.
- Workflow push and PR restricted to new workflow/tooling and Jetson docs; manual dispatch target/backend choice; on host checkout and upload Actions, generate matrix then Docker --platform linux/arm64 on `ubuntu-24.04-arm`. Read-only permissions, fail-fast false, finite timeout, concurrency, unique artifacts, fail on missing outputs. No release publishing needed.
- No untrusted shell interpolation of manual inputs; pass through env/arguments and validate allowlist. Use `bash scripts/jetson/build.sh TARGET BACKEND` inside container; require native ARM64 and expected `/etc/os-release` and dpkg architecture before apt or build.
- Build current checked-out source, Release shared SDK and CLI tools only (`BUILD_EXAMPLES=OFF`, `BUILD_TOOLS=ON`, including rs-enumerate-devices), stage install under `/usr/local`, include udev rules/license, produce `.tar.gz`, checksum and JSON manifest with source SHA, resolved image digest, target metadata, actual build userland/architecture, host kernel explicitly labeled, backend, flags/toolchain/dependency versions and resolved fetched JSON git commit. Do not claim full dependency or bit-for-bit reproducibility.
- Normalize the staged pkg-config `.pc` library path to the configured ARM64 install library directory: upstream `config/librealsense.pc.in` hardcodes x86_64. Do not modify upstream template in this phase. Validate it with a compiled pkg-config SDK consumer.
- Check all staged ELF objects ARM64, ldd no missing dependencies, run installed enumerate --help without camera AND a separate consumer using installed `find_package(realsense2 CONFIG REQUIRED)` and `realsense2::realsense2` with a harmless SDK call AND a pkg-config consumer. Perform smoke tests from a relocated unpacked archive with build and original staging paths unavailable; inspect exports for build-tree references. Preserve installed rsutils archives referenced by exports. Fail on absent library/tools.
- Toolchain compatibility Bionic must be tested in Actions, not assumed. All tooling invoked inside Bionic must support its Python 3.6 and CMake 3.10, unless a newer version is explicitly provisioned. Disable ROSBAG2 and DDS to avoid modern FastCDR requirement; ensure modern SDK's dependencies compile with selected toolchain or make explicit checked adjustment.
- Board check-device script is read-only: checks ARM64, Ubuntu, nv_tegra_release and kernel family for selected target/backend; outputs exact detected values, warns nonreference L4T and native patch prerequisites; rejects mismatched family and RT/custom ambiguity conservatively. Does not certify full native kernel ABI and never installs patches.
- Regression tests offline for invalid metadata, malformed hashes, matrix choices and eight combinations, wrong OS/architecture, kernel family boundaries, nonreference/unknown releases, backend labeling and manifests/checksums as applicable. Add workflow consistency assertions without requiring YAML dependencies if possible.

#### Automated verification
- [x] `python3 -m unittest discover -s scripts/jetson/tests -v`
- [x] `python3 scripts/jetson/jetson_ci.py validate`
- [x] `python3 scripts/jetson/jetson_ci.py matrix --target all --backend both`
- [x] `python3 scripts/jetson/jetson_ci.py verify-images`
- [x] `bash -n scripts/jetson/build.sh scripts/jetson/check-device.sh`
- [x] `git diff --check`

#### Manual verification
- [ ] Install matching archive and enumerate/stream with RealSense on each physical Jetson.
- [ ] Verify native patched modules match exact board config, Module.symvers, vermagic and signing before using native SDK.

## Phase 2
### Outcome
Document image/kernel mapping, artifact use, local preflight, backend limitations and fork CI activation without overstating validation.

### Files
- [MODIFY] `doc/installation_jetson.md` (small link/section only, preserve original guide)
- [NEW] `doc/jetson_binary_builds.md`

### Requirements
- Document exact reference table and cite official NVIDIA sources from Evidence. Explain Ubuntu userland vs NVIDIA BSP vs host kernel, older EOL Bionic, Thor vs Orin for selected reference releases, and deliberate nonlatest reference selection.
- Document workflow manual target/backend controls and branch-scoped push, public ARM runner availability, enabling Actions on forks, artifact expiry/download, GitHub auth and local Docker build route. GitHub manual workflow_dispatch requires workflow availability on default branch; this implementation branch is built through push without changing master.
- Archives are non-CUDA/headless SDKs with native or RSUSB and CLI tools only (`BUILD_EXAMPLES=OFF`); no examples/viewer/Python bindings/ROS2 recording, no kernel modules. Headless CLI actual contents must match Phase 1 implementation. Describe digest-pinned userland builds with recorded dependencies, not fully reproducible builds.
- Commands must show checksum verification, artifact extraction/staging into /usr/local carefully (review manifest first), runtime apt dependencies, ldconfig and manual udev install/reload, avoiding installation of both backend variants or conflicts with distro packages. Board check command `bash scripts/jetson/check-device.sh jp6 rsusb`.
- Native backend requires exact-release board-side patch verification; do not imply upstream script accepts every target (e.g. R32.7.6 not in current patch script cases). Explain safest default RSUSB and production tradeoffs.
- Clearly separate CI build checks from hardware streaming tests; no successful CI claim until coordinator supplies evidence.

#### Automated verification
- [x] `python3 -c "from pathlib import Path; a=Path('doc/installation_jetson.md').read_text(); b=Path('doc/jetson_binary_builds.md').read_text(); assert 'jetson_binary_builds.md' in a; assert all(x in b for x in ['18.04', '20.04', '22.04', '24.04', '4.9', '5.10', '5.15', '6.8', 'RSUSB', 'CUDA', 'checksum'])"`
- [x] `git diff --check`

#### Manual verification
- [ ] Follow installation and streaming instructions on Jetson with a RealSense camera.

## Phase 3 (coordinator)
### Outcome
Sol reviews the implementation, Luna fixes verified findings, and public Actions builds are observed and iterated until passing or a concrete external blocker is reported.
- Save review findings in execution log. Dispatch repairs with explicit ownership and verification; repeat Sol review after changes.
- Check workflow YAML/actionlint if tools can be provisioned without sudo.
- Reconcile documentation with final CLI/filenames; remove pending implementation markers. Sol documentation review requires a fail-closed checksum-to-selected-archive binding, archive member/path/type/set-ID/symlink-boundary validation before privileged copy, and explicit trusted-checkout absolute path for udev rules after changing artifact directories. Prefer one reusable tooling archive verifier with tests over unsafe prose-only tar inspection.
- Commit scoped changes, set fork remote SSH and push branch; no changes to master or existing branches.
- Observe public Actions run and download/inspect representative manifests and checksums if run succeeds. If CI build fails, obtain logs and dispatch targeted Luna max correction, push and repeat.
- Do not change account settings, spend on paid runners, or claim hardware tests. Publication/run auth/Actions opt-in blockers must be explicit.

## Phase 4
### Outcome
Resolve Sol bootstrap, provenance and installation-safety findings and reconcile documentation with the implemented artifact interfaces.

### Files
- [MODIFY] `.github/workflows/jetson-binaries.yml`
- [MODIFY] `scripts/jetson/jetson_ci.py`
- [MODIFY] `scripts/jetson/build.sh`
- [MODIFY] `scripts/jetson/tests/test_jetson_ci.py`
- [MODIFY] `doc/jetson_binary_builds.md`

### Requirements
- Before invoking Python/Git or apt, use available shell/dpkg/uname/os-release tools to reject invalid architecture/userland and missing image identity. Have the trusted workflow/host launcher supply expected Ubuntu version and configured image digest from validated target metadata. Then explicitly install python3, ca-certificates and all build prerequisites before Python helper, Git discovery or HTTPS CMake fetches. Never disable TLS.
- Require supplied image identity equals the configured digest for artifact production. Distinguish launcher-verified pinned selection from an independently observed in-container image identity; do not imply kernel/BSP observation. Add host CLI `local-build --target jp6 --backend rsusb` selecting/pulling the allowlisted digest, verifying Docker ARM64 image identity, obtaining source SHA and supplying exact environment to container. Require native ARM64 host; no emulation or privileged Docker flags. Document wrapper instead of an arbitrary manual Docker image invocation.
- Add standard-library `verify-archive --archive FILE --checksum FILE --target jp6 --backend rsusb`. Bind exactly the requested archive basename to a single checksum entry and verify hash. Before extraction, reject absolute/traversing/duplicate paths, hardlinks/special files/set-ID modes, paths outside `usr/local` except real ancestor/root directories, and symlink targets/chains escaping the install tree or members beneath symlink parents. Verify embedded manifest target/backend/image matches selected target. Support benign root `.` and relative SDK library symlink chains. No filesystem mutation by verifier.
- Documentation uses fail-closed grouped install commands: verifier success gates unprivileged extraction (`--no-same-owner --no-same-permissions`), real ancestor directory checks and privileged copy with deliberate root ownership (`cp -a --no-preserve=ownership`, not broad chown). Install udev from absolute trusted included archive path or matching checkout. Remove all pending reconciliation markers, give actual output directory/archive/sidecar/schema details, local wrapper and current tested limits. Keep prose focused.
- Workflow prepare runs offline regression tests. Capture pull/build output into unique log files and upload logs on `always()` so failed public runs can be diagnosed using artifacts; keep failure status with pipefail, no fake-success continuation. SDK upload still fails when outputs absent and only runs after successful build.
- Add regression coverage for missing bootstrap tools/order/identity, local wrapper selection/env/rejections, checksum binding/mismatch, unsafe tar cases and valid SDK symlinks/manifest. Keep helper/tests compatible with Python3.6 and minimize new machinery.

#### Automated verification
- [x] `python3 -m unittest discover -s scripts/jetson/tests -v`
- [x] `PYTHONHOME=/tmp/opencode/jetson-tools/python36/root/usr LD_LIBRARY_PATH=/tmp/opencode/jetson-tools/python36/root/usr/lib/x86_64-linux-gnu /tmp/opencode/jetson-tools/python36/root/usr/bin/python3.6 -m unittest discover -s scripts/jetson/tests -v`
- [x] `python3 scripts/jetson/jetson_ci.py validate`
- [x] `python3 scripts/jetson/jetson_ci.py verify-images`
- [x] `/tmp/opencode/jetson-tools/actionlint -shellcheck /tmp/opencode/jetson-tools/shellcheck-root/usr/bin/shellcheck .github/workflows/jetson-binaries.yml`
- [x] `/tmp/opencode/jetson-tools/shellcheck-root/usr/bin/shellcheck scripts/jetson/build.sh scripts/jetson/check-device.sh`
- [x] `git diff --check`

#### Manual verification
- [ ] Install and stream on a physical Jetson.

## Phase 5
### Outcome
Fix observed Bionic ELF parsing and Focal Git ownership compatibility failures without weakening architecture or source-provenance checks.

### Files
- [MODIFY] `scripts/jetson/build.sh`
- [MODIFY] `scripts/jetson/tests/test_jetson_ci.py`

### Requirements
- Actual run 37877580713: jp6/jp7 both backends passed and produced four archives; jp4 both compiled/installed SDK but failed ELF archive check with `non-ARM64 ELF object ... librsutils.a (                           AArch64)`; jp5 both failed after apt with `fatal: detected dubious ownership in repository at '/workspace'` despite `git -c safe.directory=...`. Full build logs saved `/tmp/opencode/jetson-tools/ci-failures-37877580713/{jp4,jp5}-{rsusb,native}.log`.
- Normalize readelf Machine field output portably across Bionic/Focal awk/sed/Bash, including leading/trailing whitespace and multiple member headers, while still rejecting any non-AArch64 object. Do not skip static archives or architecture checks. Add regression tests running actual parsing logic with realistic old readelf fixtures, mixed-architecture archive fixture and malformed/missing machine fields as appropriate.
- In ephemeral verified build container only, configure narrow global `safe.directory` for the exact source checkout path after installing Git and before source commands. Older Ubuntu security backports do not reliably honor command-scoped `-c safe.directory`. Never trust `*`, modify host Git config, chown the host checkout or weaken HEAD/dirty checks. Tests enforce exact scope and ordering.
- Keep other build/image/provenance/consumer/manifest validations unchanged.

#### Automated verification
- [x] `python3 -m unittest discover -s scripts/jetson/tests -v`
- [x] `PYTHONHOME=/tmp/opencode/jetson-tools/python36/root/usr LD_LIBRARY_PATH=/tmp/opencode/jetson-tools/python36/root/usr/lib/x86_64-linux-gnu /tmp/opencode/jetson-tools/python36/root/usr/bin/python3.6 -m unittest discover -s scripts/jetson/tests -v`
- [x] `/tmp/opencode/jetson-tools/shellcheck-root/usr/bin/shellcheck scripts/jetson/build.sh scripts/jetson/check-device.sh`
- [x] `/tmp/opencode/jetson-tools/actionlint -shellcheck /tmp/opencode/jetson-tools/shellcheck-root/usr/bin/shellcheck .github/workflows/jetson-binaries.yml`
- [x] `git diff --check`

#### Manual verification
- [ ] Install and stream on physical Jetson.

## Out of scope
- CUDA, GPU/graphics, Python wheels, ROSBAG2, precompiled kernel modules, kernel flashing/insertion, hardware streaming validation, changing upstream general CI or existing macOS work.
- Pull request to upstream; publishing a new release or apt repository; setting repository/account settings.

## Halt surfaces
- Verified upstream API/toolchain incompatibility requiring changes outside owned files: report and stop for coordinator plan amendment.
- Registry failure, unavailable hosted runner, Actions disabled, missing push/log permissions: report exact evidence rather than pretending success.
- No physical board: manual verification remains open.

## Definition of done
- Offline validation/tests and shell checks pass; live image digest/architecture checks pass.
- Sol review resolves all critical/high correctness and security findings; corrections implemented by Luna max.
- Public branch exists and new eight-combination Actions build passes with compiled archives, or task reported incomplete with precise remaining blocker.
- Clear user summary with repo/branch/run links, supported reference mappings, actual test status, and hardware limits.

## Execution log
- 2026-10-08: Coordinator cloned upstream into `/home/kye/Documents/personal/librealsense`, confirmed clean identical fork master, created local branch `jetson-binary-builds`, discovered public existing fork and working SSH after MCP fork-create 403. No user changes overwritten.
- 2026-10-08: Started Phase 1 tooling and Phase 2 documentation independently with `openai/gpt-6-luna#max`, plus read-only Sol architectural review. Set fork remote to SSH. Provisioned checksum-verified actionlint 1.7.12 and extracted shellcheck 0.11.0 under `/tmp/opencode/jetson-tools` without sudo. Existing fork Actions are active; public APIs expose run/jobs/annotations but downloading Actions logs requires API authentication (unauthenticated logs endpoint returned 403). CI diagnostics should be surfaced in public annotations/step summaries so failures remain inspectable without adding credentials.
- 2026-10-08: Sol architectural review identified proven upstream headless-examples OpenGL imported-target defect, hardcoded x86_64 pkg-config libdir, weak consumer verification, and overbroad reproducibility language. Coordinator amended Phase 1/2: tools-only artifacts, staged `.pc` normalization, both installed CMake and pkg-config consumers after relocation, JSON commit provenance, and explicit dependency/reproducibility limits. No upstream source files need changing. Bionic GCC7/CMake3.10 remain CI checks; CMake CLI must stay compatible unless upgraded.
- 2026-10-08: Sol read-only documentation review confirmed NVIDIA mappings and GitHub dispatch/runner claims. Three medium findings queued for final reconciliation: checksum failures do not gate extraction or bind the chosen archive; privileged copy needs member/path/type/set-ID/symlink confinement checks; udev install relative path fails after changing into artifact directory. Coordinator independently verified `nightly.link` can retrieve existing public fork Actions artifacts without authentication, enabling later archive/log inspection without configuring credentials or installing a GitHub App.
- 2026-10-08: Coordinator downloaded/extracted checksum-verified Ubuntu Bionic Python 3.6.9 and dependency debs into `/tmp/opencode/jetson-tools/python36` (no system installation/sudo). Interpreter and SSL import work on local x86_64; can validate Bionic Python compatibility independently from actual ARM64 compilation. Invoke with `PYTHONHOME=/tmp/opencode/jetson-tools/python36/root/usr LD_LIBRARY_PATH=/tmp/opencode/jetson-tools/python36/root/usr/lib/x86_64-linux-gnu /tmp/opencode/jetson-tools/python36/root/usr/bin/python3.6`.
- 2026-10-08: Phase 1 delivered isolated workflow/tooling and 17 passing tests. Coordinator independently reran all 17 tests on local Python and Bionic Python3.6.9, live four-image digest/architecture/userland checks, actionlint and shellcheck: all passed. Found pre-publication bootstrap concern (Python called before installation in minimal Ubuntu) and `.gitignore:93` ignores `targets.json`; coordinator must force-add that exact manifest file when committing. Full read-only Sol implementation review launched. No compiled binaries or CI run yet.
- 2026-10-08: Sol full implementation review withheld signoff. Hash-verified image rootfs layers prove all four images lack Python and CA certificates (two high blockers); local source SHA discovery invokes missing Git before apt (medium), and local manifests incorrectly label a requested digest as actual image (medium). Added Phase 4 to resolve these findings, prior docs safety findings, and failed-run log accessibility. Native/backend separation, registry hash validation and relocated consumer checks were positively reviewed. Eight actual ARM64 builds remain required.
- 2026-10-08: Luna Phase 4 completed repairs with 28 passing tests on both current Python and Bionic3.6, four live image checks and clean linters. Coordinator independently reran tests/linters, force-added ignored `scripts/jetson/targets.json`, committed `b1e63f45f2ec77c8aded1f0b6413f842151750eb`, and pushed public fork branch `jetson-binary-builds`. Confirmed published target manifest is retrievable at that exact source SHA. Actual run: https://github.com/kyeshmz/librealsense/actions/runs/37877580713 . A background observer is watching this exact run/source commit; no completion claim yet.
- 2026-10-08: Sol re-review approved code-only publication/CI with no remaining critical/high/medium findings. Independently passed 28 tests on current Python and3.6, four image digest/userland verifications, actionlint/shellcheck/Bash syntax and diff checks. All prior bootstrap/provenance/docs safety findings resolved. Bionic and all eight actual SDK builds, artifact execution and hardware verification still pending CI/manual evidence respectively.
- 2026-10-08: Actual ARM64 run 37877580713 completed failure: prepare passed, jp6+native/rsusb and jp7+native/rsusb all passed and produced four archives. Both jp4 SDKs compiled and installed but old readelf/awk Machine whitespace caused false non-ARM64 rejection; both jp5 jobs stopped on Focal Git dubious-ownership despite command-scoped safe.directory. Retrieved public log artifacts through nightly.link and saved full build logs in `/tmp/opencode/jetson-tools/ci-failures-37877580713/`. Added Phase5 targeted compatibility fixes, then rerun all eight combinations and re-review with Sol.
- 2026-10-08: While Luna repairs legacy failures, coordinator independently downloaded/inspected all four successful jp6/jp7 archives from run37877580713. GitHub artifact ZIP digests, SDK archive checksums, embedded/sidecar manifest equality, exact source SHA, image digests, userland/kernel-reference metadata and backend flags all matched. Verified 52 ARM64 ELF objects per artifact including static archive members; production `verify-archive` accepted all four real packages. Recorded GCC11.4/CMake3.22.1 (jp6), GCC13.3/CMake3.28.3 (jp7), with build host kernel6.17.0-1022-azure explicitly distinct from target kernels. Evidence saved `/tmp/opencode/jetson-tools/artifact-inspection/37877580713/summary.json`. These four passes do not satisfy the full eight-combination gate.
- 2026-10-08: Luna Phase5 replaced old awk field mutation with portable sed Machine-field normalization and strict per-member checks, and configured exact REPO_ROOT global safe.directory inside ephemeral verified container. Added regressions: all32 tests pass on current/BionicPython3.6 plus linters/diff. Sol independently repeated checks and approved code-only full rerun with no critical/high/medium findings; architecture/static-archive/dirty/HEAD validations remain intact. No hardware tests.
- 2026-10-08: Phase 2 documentation added the four bounded NVIDIA reference mappings, fork workflow/artifact guidance, local preflight and Docker route, checksum/manifest-led install steps, and backend/kernel limitations. Documentation contract assertion and `git diff --check` passed. Manual Jetson verification remains open; Phase 1 artifact names/layout and exact manifest/output details remain pending reconciliation.
- 2026-10-08: Reconciled Phase 2 docs with Sol's amended Phase 1 contract: documents libraries/headers and CLI tools only (`BUILD_EXAMPLES=OFF`, `BUILD_TOOLS=ON`), digest-pinned userland and recorded-but-not-locked dependencies, workflow_dispatch default-branch limitation, pkg-config normalization and relocated CMake/pkg-config consumer checks as intended workflow checks only. No Phase 1 workflow/tooling files were present in the shared worktree for interface inspection. Required documentation assertion and `git diff --check` passed again; manual Jetson validation and CI results remain unobserved.
- 2026-10-08: Completed Phase 1 tooling. `jetson_ci.py` exposes offline `validate`, allowlisted JSON `matrix --target all|jp4|jp5|jp6|jp7 --backend both|rsusb|native`, and live `verify-images`; `check-device.sh TARGET BACKEND` is read-only. `build.sh TARGET BACKEND` builds a shared SDK plus CLI tools only, stages an archive rooted at `usr/local`, normalizes the staged ARM64 pkg-config path, preserves `librsutils.a`, and checks the relocated archive with `rs-enumerate-devices --help` plus CMake and pkg-config consumers. Artifact path is `jetson-dist/TARGET-BACKEND/librealsense-jetson-TARGET-BACKEND-SOURCE_SHA12.tar.gz`, with `.sha256` and `.json` sidecars; the manifest is also embedded under `/usr/local/share/doc/librealsense2/jetson-build-manifest.json` and records the fetched nlohmann/json commit and resolved installed package versions. All six Phase 1 automated commands passed; actionlint, shellcheck, and Python 3.6 validation also passed. Live registry checks resolved and hash-verified all four ARM64 manifests, configs, and Ubuntu layers. No ARM64 SDK build, GitHub Actions run, or physical Jetson test was performed; those remain for the coordinator's publication/run and hardware verification.
- 2026-10-08: Phase 4 added shell-only architecture/userland/launcher checks before apt, installed Python 3 and CA certificates before helper/Git/HTTPS build work, and required the supplied target, Ubuntu and image digest to match validated target metadata. Added the native-ARM64 `local-build` launcher (registry verification, pinned Docker pull/inspection, clean source SHA) and manifest fields separating launcher-selected digest from in-container observations. Added read-only `verify-archive` checksum binding, tar member/type/set-ID/path/symlink and embedded-manifest checks; replaced unsafe documentation extraction and udev paths. Workflow now runs offline tests and uploads unique pull/build logs on `always()` while uploading SDK artifacts only after success. The seven Phase 4 automated commands all passed on local Python and Bionic Python 3.6 where applicable; live registry verification passed for all four images. No ARM64 SDK build, GitHub Actions build run, or physical Jetson test was performed.
- 2026-10-08: Phase 5 fixed readelf field normalization by trimming each `Machine:` value with POSIX `sed`, validating every machine header (including archive members), and failing closed for missing/empty fields or a nonzero readelf result with ELF output. Added regressions that execute the production shell parser against old-readelf whitespace, multiple archive members, mixed architectures, and malformed fields. Git now receives one exact `safe.directory` entry for `REPO_ROOT` in the verified ephemeral container after apt/preflight and before HEAD/status; dirty-tree and source-SHA checks remain intact. All 32 tests passed on local Python and Bionic Python 3.6; shellcheck, actionlint and `git diff --check` passed. No follow-up ARM64 CI run or hardware verification was performed; run 37877580713 remains the observed partial success (jp6/jp7 only), not an all-target pass.
