#!/usr/bin/env python3
"""Validate that a GitHub release is consistent before publishing to PyPI.

This script enforces that:
  1. The release tag uses the v<version> convention with a final
     PEP 440 version (no dev/pre/post segments).
  2. The tag version exactly matches mechanicalsoup/__version__.py.
  3. The dist directory contains exactly one wheel and one sdist, and
     every artifact\'s embedded package name and version match
     MechanicalSoup and the release tag.
  4. The released commit is contained in the main branch history.

Any failure exits non-zero before the PyPI upload step.
"""

import argparse
import os
import re
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

from packaging.version import Version, InvalidVersion


PACKAGE_NAME = "MechanicalSoup"
# Normalised name as it appears in wheel filenames and metadata
PACKAGE_NAME_NORMALIZED = "mechanicalsoup"


def _fail(msg):
    """Print an error and exit."""
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def validate_tag_format(tag):
    """Return the PEP 440 Version embedded in *tag*, or fail."""
    if not tag.startswith("v"):
        _fail(f"Tag {tag!r} does not start with \'v\'. "
              "Expected format: v<version>")
    version_str = tag[1:]
    if not version_str:
        _fail(f"Tag {tag!r} has no version after \'v\'.")
    try:
        version = Version(version_str)
    except InvalidVersion:
        _fail(f"Tag version {version_str!r} is not valid PEP 440.")
    if version.is_prerelease or version.is_devrelease or version.is_postrelease:
        _fail(f"Tag version {version_str!r} is not a final release version "
              "(dev/pre/post segments are not allowed).")
    return version


def read_source_version(repo_root):
    """Read __version__ from mechanicalsoup/__version__.py."""
    version_file = Path(repo_root) / "mechanicalsoup" / "__version__.py"
    if not version_file.is_file():
        _fail(f"Version file not found: {version_file}")
    try:
        text = version_file.read_text(encoding="utf-8")
    except OSError as exc:
        _fail(f"Cannot read version file {version_file}: {exc}")
    match = re.search(
        r"^__version__\s*=\s*[\'\"]([^\'\"]+)[\'\"]", text, re.MULTILINE
    )
    if not match:
        _fail(f"Cannot parse __version__ from {version_file}")
    raw = match.group(1)
    try:
        return Version(raw)
    except InvalidVersion:
        _fail(f"Source version {raw!r} in {version_file} is not valid PEP 440.")


def validate_source_version(tag_version, repo_root):
    """Ensure the tag version matches the source version exactly."""
    source_version = read_source_version(repo_root)
    if tag_version != source_version:
        _fail(
            f"Tag version {tag_version} does not match source version "
            f"{source_version} in mechanicalsoup/__version__.py"
        )
    return source_version


def _extract_wheel_metadata(dist_path):
    """Return (name, version) from a .whl file's METADATA."""
    try:
        with zipfile.ZipFile(dist_path) as zf:
            # Find the METADATA file inside *.dist-info/
            metadata_files = [
                n for n in zf.namelist()
                if n.endswith(".dist-info/METADATA")
            ]
            if len(metadata_files) != 1:
                _fail(
                    f"Wheel {dist_path.name} has {len(metadata_files)} "
                    "METADATA files (expected exactly 1)."
                )
            text = zf.read(metadata_files[0]).decode("utf-8")
    except (zipfile.BadZipFile, OSError) as exc:
        _fail(f"Cannot read wheel {dist_path.name}: {exc}")

    name = version = None
    for line in text.splitlines():
        if line.startswith("Name:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("Version:"):
            version = line.split(":", 1)[1].strip()
        if name and version:
            break
    if not name or not version:
        _fail(f"Wheel {dist_path.name} is missing Name or Version metadata.")
    return name, version


def _extract_sdist_metadata(dist_path):
    """Return (name, version) from a .tar.gz sdist's PKG-INFO."""
    try:
        with tarfile.open(dist_path, "r:gz") as tf:
            # PKG-INFO is at <name>-<version>/PKG-INFO
            pkginfo_files = [
                m for m in tf.getmembers()
                if m.name.endswith("/PKG-INFO") and m.isfile()
            ]
            if len(pkginfo_files) != 1:
                _fail(
                    f"Sdist {dist_path.name} has {len(pkginfo_files)} "
                    "PKG-INFO files (expected exactly 1)."
                )
            fobj = tf.extractfile(pkginfo_files[0])
            if fobj is None:
                _fail(
                    f"Cannot extract PKG-INFO from sdist {dist_path.name}."
                )
            text = fobj.read().decode("utf-8")
    except (tarfile.TarError, OSError) as exc:
        _fail(f"Cannot read sdist {dist_path.name}: {exc}")

    name = version = None
    for line in text.splitlines():
        if line.startswith("Name:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("Version:"):
            version = line.split(":", 1)[1].strip()
        if name and version:
            break
    if not name or not version:
        _fail(f"Sdist {dist_path.name} is missing Name or Version metadata.")
    return name, version


def validate_artifacts(dist_dir, tag_version):
    """Ensure dist has exactly one wheel and one sdist, all metadata correct."""
    dist_path = Path(dist_dir)
    if not dist_path.is_dir():
        _fail(f"Dist directory does not exist: {dist_dir}")

    wheels = sorted(dist_path.glob("*.whl"))
    sdists = sorted(dist_path.glob("*.tar.gz"))

    if len(wheels) == 0:
        _fail("No wheel (.whl) found in dist directory.")
    if len(sdists) == 0:
        _fail("No source distribution (.tar.gz) found in dist directory.")
    if len(wheels) > 1:
        names = [w.name for w in wheels]
        _fail(
            f"Multiple wheels found in dist directory: {names}. "
            "Expected exactly one."
        )
    if len(sdists) > 1:
        names = [s.name for s in sdists]
        _fail(
            f"Multiple sdists found in dist directory: {names}. "
            "Expected exactly one."
        )

    expected_version_str = str(tag_version)

    # Validate wheel
    whl_name, whl_version = _extract_wheel_metadata(wheels[0])
    if whl_name.lower() != PACKAGE_NAME_NORMALIZED:
        _fail(
            f"Wheel package name {whl_name!r} does not match "
            f"expected {PACKAGE_NAME!r}."
        )
    if whl_version != expected_version_str:
        _fail(
            f"Wheel version {whl_version!r} does not match "
            f"tag version {expected_version_str!r}."
        )

    # Validate sdist
    sdist_name, sdist_version = _extract_sdist_metadata(sdists[0])
    if sdist_name.lower() != PACKAGE_NAME_NORMALIZED:
        _fail(
            f"Sdist package name {sdist_name!r} does not match "
            f"expected {PACKAGE_NAME!r}."
        )
    if sdist_version != expected_version_str:
        _fail(
            f"Sdist version {sdist_version!r} does not match "
            f"tag version {expected_version_str!r}."
        )

    print(f"  Artifacts OK: {wheels[0].name}, {sdists[0].name}")
    return wheels, sdists


def validate_commit_in_main(repo_root):
    """Ensure the current HEAD is an ancestor of (or equal to) main."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, cwd=repo_root,
        )
        if result.returncode != 0:
            _fail(f"Cannot determine HEAD: {result.stderr.strip()}")
        head_sha = result.stdout.strip()

        # Resolve main -- could be refs/heads/main or origin/main
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "main"],
            capture_output=True, text=True, cwd=repo_root,
        )
        if result.returncode != 0:
            result = subprocess.run(
                ["git", "rev-parse", "--verify", "origin/main"],
                capture_output=True, text=True, cwd=repo_root,
            )
            if result.returncode != 0:
                _fail("Cannot resolve 'main' or 'origin/main' branch.")

        main_sha = result.stdout.strip()

        # Check if HEAD is contained in main's history
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", head_sha, main_sha],
            capture_output=True, text=True, cwd=repo_root,
        )
        if result.returncode != 0:
            _fail(
                f"Released commit {head_sha[:12]} is NOT contained in "
                f"the main branch (tip {main_sha[:12]}). "
                "Refusing to publish."
            )
        print(f"  Commit {head_sha[:12]} is in main branch history.")
    except FileNotFoundError:
        _fail("git executable not found on PATH.")


def main():
    parser = argparse.ArgumentParser(
        description="Validate a MechanicalSoup release before PyPI upload."
    )
    parser.add_argument(
        "--tag", required=True,
        help="The GitHub release tag (e.g. v1.5.0)."
    )
    parser.add_argument(
        "--dist-dir", required=True,
        help="Path to the dist/ directory containing built artifacts."
    )
    parser.add_argument(
        "--repo-root", default=".",
        help="Path to the repository root (default: current directory)."
    )
    parser.add_argument(
        "--skip-commit-check", action="store_true",
        help="Skip the git ancestry check (for testing outside a repo)."
    )
    args = parser.parse_args()

    print(f"Validating release for tag: {args.tag}")

    # Step 1: Validate tag format and extract version
    tag_version = validate_tag_format(args.tag)
    print(f"  Tag version: {tag_version}")

    # Step 2: Validate source version matches
    validate_source_version(tag_version, args.repo_root)
    print(f"  Source version matches tag: {tag_version}")

    # Step 3: Validate built artifacts
    validate_artifacts(args.dist_dir, tag_version)

    # Step 4: Validate commit is in main
    if not args.skip_commit_check:
        validate_commit_in_main(args.repo_root)

    print("All release validations passed.")


if __name__ == "__main__":
    main()
