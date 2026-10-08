# License: Apache 2.0. See LICENSE file in root directory.
# Copyright(c) 2026 RealSense, Inc. All Rights Reserved.

"""Validate that a relocated macOS RealSense Viewer bundle is self-contained."""

import argparse
import ctypes
import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path


_SYSTEM_PREFIXES = (
    "/System/Library/",
    "/usr/lib/",
    "/usr/libexec/",
    "/Library/Apple/System/Library/",
    "/System/Volumes/Preboot/Cryptexes/OS/System/Library/",
    "/System/Volumes/Preboot/Cryptexes/OS/usr/lib/",
)
_LOAD_PATH = re.compile(r"\s*(?:name|path)\s+(.+?)\s+\(offset\s+\d+\)\s*$")
_DYLIB_LOADS = {
    "LC_LOAD_DYLIB", "LC_LOAD_WEAK_DYLIB", "LC_REEXPORT_DYLIB",
    "LC_LOAD_UPWARD_DYLIB", "LC_LAZY_LOAD_DYLIB",
}
_PATH_LOADS = _DYLIB_LOADS | {"LC_LOAD_DYLINKER", "LC_PREBOUND_DYLIB"}
_VERSION = re.compile(r"\d+\.\d+\.\d+\Z")


class BundleCheckError(Exception):
    """Raised when the bundle is incomplete or has unsafe dependencies."""


class OtoolInspector:
    """Read Mach-O metadata using the macOS command-line inspection tools."""

    def __init__(self):
        self._cache_contains_path = None

    def system_image_available(self, path):
        if path.is_file():
            return True
        if self._cache_contains_path is None:
            try:
                # dlopen(NULL) only exposes the already loaded process runtime;
                # never dlopen a dependency being inspected. Apple's dyld.h
                # declares this read-only, stat-like cache query since macOS 11.
                runtime = ctypes.CDLL(None)
                contains_path = runtime._dyld_shared_cache_contains_path
                contains_path.argtypes = [ctypes.c_char_p]
                contains_path.restype = ctypes.c_bool
                self._cache_contains_path = contains_path
            except (OSError, AttributeError) as error:
                raise BundleCheckError("cannot query the active dyld shared cache: {}".format(error))
        return bool(self._cache_contains_path(os.fsencode(path)))

    @staticmethod
    def _run(arguments, path):
        try:
            result = subprocess.run(
                arguments + [str(path)],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError) as error:
            detail = getattr(error, "stderr", "") or str(error)
            raise BundleCheckError("{} failed for {}: {}".format(arguments[0], path, detail.strip()))
        return result.stdout

    def is_macho(self, path):
        return "Mach-O" in self._run(["file", "-b"], path)

    def slices(self, path):
        # Never merge fat-binary metadata: one slice's runpaths cannot satisfy
        # another slice's loads. -L also includes LC_ID_DYLIB, which is not a load.
        architectures = self._run(["lipo", "-archs"], path).split()
        if not architectures or len(set(architectures)) != len(architectures):
            raise BundleCheckError("cannot enumerate Mach-O slices for {}".format(path))
        return {
            arch: self._parse_load_commands(
                self._run(["otool", "-arch", arch, "-hv", "-l"], path), path, arch
            )
            for arch in architectures
        }

    @staticmethod
    def _parse_load_commands(output, path, arch):
        metadata = {"dependencies": [], "rpaths": [], "filetype": None}
        command = None
        filetype_column = None
        ncmds_column = None
        expected_commands = None
        commands_seen = 0
        pending_path = False
        for line in output.splitlines():
            words = line.split()
            if "filetype" in words and "ncmds" in words:
                filetype_column = words.index("filetype")
                ncmds_column = words.index("ncmds")
            elif filetype_column is not None:
                if len(words) > max(filetype_column, ncmds_column):
                    metadata["filetype"] = words[filetype_column]
                    try:
                        expected_commands = int(words[ncmds_column])
                    except ValueError:
                        pass
                filetype_column = None
            if line.strip().startswith("cmd "):
                if pending_path:
                    raise BundleCheckError("missing path in {} ({})".format(path, arch))
                command = words[1]
                commands_seen += 1
                if command == "LC_DYLD_ENVIRONMENT":
                    raise BundleCheckError("embedded dyld environment in {} ({})".format(path, arch))
                if (("DYLIB" in command or command.startswith("LC_LOAD"))
                        and command not in _PATH_LOADS | {"LC_ID_DYLIB"}):
                    raise BundleCheckError("unsupported load command {} in {}".format(command, path))
                pending_path = command in _PATH_LOADS or command == "LC_RPATH"
            elif pending_path:
                match = _LOAD_PATH.match(line)
                if match:
                    key = "rpaths" if command == "LC_RPATH" else "dependencies"
                    metadata[key].append(match.group(1))
                    pending_path = False
        if (pending_path or expected_commands != commands_seen
                or metadata["filetype"] not in {"EXECUTE", "DYLIB", "BUNDLE"}):
            raise BundleCheckError("incomplete or unsupported Mach-O metadata for {} ({})".format(path, arch))
        return metadata


def _is_within(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _is_system_path(path):
    value = str(path)
    return any(value == prefix.rstrip("/") or value.startswith(prefix) for prefix in _SYSTEM_PREFIXES)


def _resolved(path):
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError):
        return path.absolute()


def _check_layout(root):
    if not root.is_dir() or root.suffix != ".app":
        raise BundleCheckError("not a macOS .app bundle directory: {}".format(root))

    contents = root / "Contents"
    executable = contents / "MacOS" / "realsense-viewer"
    resources = contents / "Resources"
    frameworks = contents / "Frameworks"
    plist_path = contents / "Info.plist"
    missing = [
        str(path.relative_to(root))
        for path in (executable, resources, frameworks, plist_path)
        if not path.exists()
    ]
    if missing:
        raise BundleCheckError("bundle is missing required paths: {}".format(", ".join(missing)))
    if not executable.is_file() or not resources.is_dir() or not frameworks.is_dir():
        raise BundleCheckError("bundle has an invalid Contents/MacOS, Resources, or Frameworks layout")

    try:
        with plist_path.open("rb") as plist_file:
            plist = plistlib.load(plist_file)
    except (OSError, plistlib.InvalidFileException, ValueError) as error:
        raise BundleCheckError("cannot read {}: {}".format(plist_path, error))
    if not isinstance(plist, dict):
        raise BundleCheckError("{} is not a property-list dictionary".format(plist_path))

    expected = {
        "CFBundleExecutable": "realsense-viewer",
        "CFBundleIdentifier": "com.intel.realsense.viewer",
        "CFBundleDisplayName": "RealSense Viewer",
        "CFBundleName": "RealSense Viewer",
        "CFBundlePackageType": "APPL",
    }
    invalid = [
        "{}={!r} (expected {!r})".format(key, plist.get(key), value)
        for key, value in expected.items()
        if plist.get(key) != value
    ]
    short_version = plist.get("CFBundleShortVersionString")
    bundle_version = plist.get("CFBundleVersion")
    if not isinstance(short_version, str) or not _VERSION.fullmatch(short_version):
        invalid.append("CFBundleShortVersionString={!r} (expected x.y.z)".format(short_version))
    if not isinstance(bundle_version, str) or not _VERSION.fullmatch(bundle_version):
        invalid.append("CFBundleVersion={!r} (expected x.y.z)".format(bundle_version))
    if short_version != bundle_version:
        invalid.append("CFBundleShortVersionString and CFBundleVersion do not match")
    if invalid:
        raise BundleCheckError("invalid bundle metadata: {}".format("; ".join(invalid)))

    required_resources = (resources / "LICENSE", resources / "NOTICE.md")
    missing_resources = [str(path.relative_to(root)) for path in required_resources if not path.is_file()]
    presets = resources / "Presets"
    if not presets.is_dir() or not any(presets.rglob("*.preset")):
        missing_resources.append("Contents/Resources/Presets/*.preset")
    if missing_resources:
        raise BundleCheckError("bundle is missing packaged resources: {}".format(", ".join(missing_resources)))

    return executable


def _check_symlinks(root):
    errors = []
    for current, directories, files in os.walk(str(root), topdown=True, followlinks=False):
        for name in list(directories) + files:
            path = Path(current) / name
            if not path.is_symlink():
                continue
            try:
                target = path.resolve(strict=True)
            except (OSError, RuntimeError) as error:
                errors.append("{}: broken or cyclic symlink ({})".format(path.relative_to(root), error))
                continue
            if not _is_within(target, root):
                errors.append("{}: symlink escapes the app bundle to {}".format(path.relative_to(root), target))
        directories[:] = [name for name in directories if not (Path(current) / name).is_symlink()]
    return errors


def _macho_images(root, inspector):
    images = {}
    for current, directories, files in os.walk(str(root), topdown=True, followlinks=False):
        directories[:] = [name for name in directories if not (Path(current) / name).is_symlink()]
        for name in files:
            path = Path(current) / name
            try:
                physical_path = path.resolve(strict=True)
                if physical_path.is_file() and inspector.is_macho(physical_path):
                    if physical_path not in images:
                        images[physical_path] = inspector.slices(physical_path)
            except (OSError, RuntimeError, BundleCheckError) as error:
                raise BundleCheckError("cannot inspect {}: {}".format(path.relative_to(root), error))
    return images


def _expand_path(value, loader_image, executable):
    if value == "@loader_path":
        path = loader_image.parent
    elif value.startswith("@loader_path/"):
        suffix = value[len("@loader_path/"):]
        if suffix.startswith("/"):
            raise ValueError("absolute suffix after loader token")
        path = loader_image.parent / suffix
    elif value == "@executable_path":
        path = executable.parent
    elif value.startswith("@executable_path/"):
        suffix = value[len("@executable_path/"):]
        if suffix.startswith("/"):
            raise ValueError("absolute suffix after loader token")
        path = executable.parent / suffix
    elif value.startswith("@"):
        raise ValueError("unsupported loader token")
    else:
        path = Path(value)
        if not path.is_absolute():
            raise ValueError("relative paths are unsafe")
    return _resolved(path)


def _classify(path, root):
    path = _resolved(path)
    if _is_system_path(path):
        return "system", path
    if _is_within(path, root):
        return "bundle", path
    return "external", path


def validate_bundle(bundle_path, inspector=None, platform=None):
    """Validate a bundle; tests may inject an inspector on non-Darwin hosts."""
    if platform is None:
        platform = sys.platform
    if inspector is None:
        if platform != "darwin":
            raise BundleCheckError("Mach-O bundle inspection requires macOS and otool")
        inspector = OtoolInspector()

    supplied_path = Path(bundle_path).expanduser()
    if supplied_path.suffix != ".app":
        raise BundleCheckError("bundle path must name a .app directory: {}".format(supplied_path))
    root = _resolved(supplied_path)
    executable = _check_layout(root)
    symlink_errors = _check_symlinks(root)
    if symlink_errors:
        raise BundleCheckError("unsafe bundle symlinks: {}".format("; ".join(symlink_errors)))

    executable = _resolved(executable)
    images = _macho_images(root, inspector)
    if executable not in images:
        raise BundleCheckError("Contents/MacOS/realsense-viewer is not a readable Mach-O executable")

    errors = []
    visited = set()
    active = set()

    def runpath_base(entry):
        raw, owner, entry_executable = entry
        try:
            path = _expand_path(raw, owner, entry_executable)
        except ValueError as error:
            return None, "{}: unsafe runpath {!r}: {}".format(owner.relative_to(root), raw, error)
        kind, path = _classify(path, root)
        if kind == "external":
            return None, "{}: external non-system runpath {!r} resolves to {}".format(owner.relative_to(root), raw, path)
        if kind == "system" and raw.startswith("@"):
            return None, "{}: token runpath {!r} escapes the app bundle".format(owner.relative_to(root), raw)
        if kind == "bundle" and raw.startswith("/"):
            return None, "{}: absolute in-bundle runpath {!r} is not relocatable".format(owner.relative_to(root), raw)
        if kind == "bundle" and not path.is_dir():
            return None, "{}: unresolved in-bundle runpath {!r} resolves to {}".format(owner.relative_to(root), raw, path)
        return (kind, path), None

    def resolve_dependency(raw, image, runpaths, image_executable):
        if raw.startswith("@rpath/"):
            suffix = raw[len("@rpath/"):]
            if suffix.startswith("/"):
                errors.append("{}: unsafe dependency {!r}: absolute suffix after loader token".format(image.relative_to(root), raw))
                return None
            for entry in runpaths:
                base, error = runpath_base(entry)
                if error:
                    errors.append(error)
                    continue
                kind, directory = base
                candidate_kind, candidate = _classify(directory / suffix, root)
                if candidate_kind == "external":
                    errors.append("{}: dependency {!r} escapes the bundle through {}".format(image.relative_to(root), raw, candidate))
                elif candidate_kind == "system":
                    if inspector.system_image_available(candidate):
                        return candidate
                elif candidate.is_file():
                    # dyld uses the first match, not the union of all matches.
                    return candidate
            errors.append("{}: unresolved @rpath dependency {!r}".format(image.relative_to(root), raw))
            return None

        try:
            candidate = _expand_path(raw, image, image_executable)
        except ValueError as error:
            errors.append("{}: unsafe dependency {!r}: {}".format(image.relative_to(root), raw, error))
            return None
        kind, candidate = _classify(candidate, root)
        if kind == "system":
            if raw.startswith("@"):
                errors.append("{}: token dependency {!r} escapes the app bundle".format(image.relative_to(root), raw))
                return None
            if not inspector.system_image_available(candidate):
                errors.append("{}: unresolved system dependency {!r} (not on disk or in the active dyld shared cache)".format(image.relative_to(root), raw))
                return None
            return candidate
        if kind == "external":
            errors.append("{}: external non-system dependency {!r} resolves to {}".format(image.relative_to(root), raw, candidate))
            return None
        if raw.startswith("/"):
            errors.append("{}: absolute in-bundle dependency {!r} is not relocatable".format(image.relative_to(root), raw))
            return None
        if not candidate.is_file():
            errors.append("{}: unresolved in-bundle dependency {!r} resolves to {}".format(image.relative_to(root), raw, candidate))
            return None
        return candidate

    def visit(image, arch, image_executable, inherited_runpaths):
        metadata = images[image][arch]
        entries = [(raw, image, image_executable) for raw in metadata["rpaths"]]
        runpaths = entries + inherited_runpaths
        visit_key = (image, arch, image_executable, tuple(runpaths))
        active_key = (image, arch, image_executable)
        if visit_key in visited or active_key in active:
            return
        visited.add(visit_key)
        active.add(active_key)
        for entry in entries:
            _, error = runpath_base(entry)
            if error:
                errors.append(error)
        for dependency in metadata["dependencies"]:
            target = resolve_dependency(dependency, image, runpaths, image_executable)
            if target is None or _is_system_path(target):
                continue
            if target not in images:
                errors.append("{} ({}): dependency {!r} is not Mach-O".format(image.relative_to(root), arch, dependency))
            elif arch not in images[target]:
                errors.append("{} ({}): dependency {!r} has no matching architecture slice".format(image.relative_to(root), arch, dependency))
            elif images[target][arch]["filetype"] != "DYLIB":
                errors.append("{} ({}): dependency {!r} is not MH_DYLIB".format(image.relative_to(root), arch, dependency))
            else:
                visit(target, arch, image_executable, runpaths)
        active.remove(active_key)

    # Each executable starts a fresh dyld context; a helper does not inherit
    # the main executable's LC_RPATH or @executable_path. Location alone does
    # not make a dylib in Contents/MacOS an executable.
    for image, slices in images.items():
        for arch, metadata in slices.items():
            if metadata["filetype"] == "EXECUTE":
                visit(image, arch, image, [])
    for arch, metadata in images[executable].items():
        if metadata["filetype"] != "EXECUTE":
            errors.append("Contents/MacOS/realsense-viewer ({}) is not MH_EXECUTE".format(arch))
    # Also inspect unreferenced images (potential plugins) in the main app's
    # context. Reached images are already checked in every loading chain.
    for image, slices in images.items():
        for arch in slices:
            if not any(key[:2] == (image, arch) for key in visited):
                main_metadata = images[executable].get(arch, {"rpaths": []})
                main_runpaths = [(raw, executable, executable) for raw in main_metadata["rpaths"]]
                visit(image, arch, executable, main_runpaths)

    if errors:
        unique_errors = list(dict.fromkeys(errors))
        raise BundleCheckError("\n".join(unique_errors))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", help="path to realsense-viewer.app")
    args = parser.parse_args(argv)
    try:
        validate_bundle(args.bundle)
    except BundleCheckError as error:
        print("Bundle check failed: {}".format(error), file=sys.stderr)
        return 1
    print("Bundle dependencies are self-contained: {}".format(args.bundle))
    return 0


if __name__ == "__main__":
    sys.exit(main())
