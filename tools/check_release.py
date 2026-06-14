"""Release-time safety checks before publishing MechanicalSoup to PyPI.

This script is executed only by the release workflow
(``.github/workflows/python-publish.yml``) after the distribution has
been built and *before* it is uploaded to PyPI.  It refuses to let a
release be published unless every observable check below holds:

* the release tag follows the documented ``v<version>`` convention and
  contains a final (non development) PEP 440 version;
* the tag version exactly matches ``mechanicalsoup/__version__.py``;
* the build produced both a wheel and a source distribution, and every
  artifact's embedded name/version metadata matches MechanicalSoup and
  the release tag;
* the released commit is contained in the current ``main`` history.

The script *fails closed*: any missing, malformed, unreadable,
duplicated or inconsistent input aborts publishing with a non-zero
exit status.
"""

import argparse
import email
import os
import re
import subprocess
import sys
import tarfile
import zipfile

from packaging.version import InvalidVersion, Version

DEFAULT_VERSION_FILE = os.path.join("mechanicalsoup", "__version__.py")
DEFAULT_MAIN_REF = "origin/main"

TAG_RE = re.compile(r"^v(?P<version>.+)$")
WHEEL_METADATA_RE = re.compile(r"^[^/]+\.dist-info/METADATA$")
SDIST_PKGINFO_RE = re.compile(r"^[^/]+/PKG-INFO$")


class ReleaseCheckError(Exception):
    """Raised when a release fails a publishing safety check."""


def normalize_name(name):
    """Return the PEP 503 normalized form of a distribution name."""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def parse_version(text, label):
    """Parse ``text`` as a PEP 440 version or raise ReleaseCheckError."""
    try:
        return Version(text)
    except InvalidVersion:
        raise ReleaseCheckError(
            "%s %r is not a valid PEP 440 version" % (label, text))


def parse_final_version(text, label):
    """Parse a version and reject dev, pre-release or local versions."""
    version = parse_version(text, label)
    if version.is_devrelease:
        raise ReleaseCheckError(
            "%s %r is a development version, not a final release"
            % (label, text))
    if version.is_prerelease:
        raise ReleaseCheckError(
            "%s %r is a pre-release, not a final release" % (label, text))
    if version.local is not None:
        raise ReleaseCheckError(
            "%s %r has a local version segment" % (label, text))
    return version


def check_tag(tag, source_version):
    """Validate the release tag against the documented convention.

    Returns the version string carried by the tag.
    """
    if not tag:
        raise ReleaseCheckError("no release tag was provided")
    tag = tag.strip()
    match = TAG_RE.match(tag)
    if match is None:
        raise ReleaseCheckError(
            "release tag %r does not follow the 'v<version>' convention"
            % tag)
    tag_version = match.group("version")
    parse_final_version(tag_version, "release tag version")
    if not source_version:
        raise ReleaseCheckError(
            "source version (%s) is empty" % DEFAULT_VERSION_FILE)
    if tag_version != source_version:
        raise ReleaseCheckError(
            "release tag version %r does not match source version %r"
            % (tag_version, source_version))
    # The exact match above already ties the two together; re-check the
    # source version on its own so an inconsistent source value cannot
    # slip through with a matching but invalid tag.
    parse_final_version(source_version, "source version")
    return tag_version


def _read_text(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError as exc:
        raise ReleaseCheckError("could not read %s: %s" % (path, exc))


def _extract_dunder(text, name, path):
    match = re.search(
        r"^%s\s*=\s*['\"]([^'\"]+)['\"]" % re.escape(name),
        text, re.MULTILINE)
    if match is None:
        raise ReleaseCheckError("could not find %s in %s" % (name, path))
    return match.group(1).strip()


def read_source_version(path):
    """Read ``__version__`` from a ``__version__.py``-style file."""
    return _extract_dunder(_read_text(path), "__version__", path)


def read_source_title(path):
    """Read ``__title__`` from a ``__version__.py``-style file."""
    return _extract_dunder(_read_text(path), "__title__", path)


def _parse_metadata(content, source):
    message = email.message_from_string(content)
    name = message.get("Name")
    version = message.get("Version")
    if not name or not version:
        raise ReleaseCheckError(
            "%s is missing Name or Version metadata" % source)
    return name.strip(), version.strip()


def read_wheel_metadata(path):
    """Return (name, version) from a wheel's embedded METADATA."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = [n for n in archive.namelist()
                     if WHEEL_METADATA_RE.match(n)]
            if not names:
                raise ReleaseCheckError(
                    "wheel %s has no METADATA file" % path)
            if len(names) > 1:
                raise ReleaseCheckError(
                    "wheel %s has multiple METADATA files" % path)
            content = archive.read(names[0]).decode("utf-8")
    except zipfile.BadZipFile:
        raise ReleaseCheckError(
            "wheel %s is not a valid zip archive" % path)
    except OSError as exc:
        raise ReleaseCheckError(
            "could not read wheel %s: %s" % (path, exc))
    return _parse_metadata(content, "wheel %s" % path)


def read_sdist_metadata(path):
    """Return (name, version) from an sdist's embedded PKG-INFO."""
    try:
        with tarfile.open(path, "r:gz") as archive:
            members = [m for m in archive.getmembers()
                       if m.isfile() and SDIST_PKGINFO_RE.match(m.name)]
            if not members:
                raise ReleaseCheckError(
                    "sdist %s has no PKG-INFO file" % path)
            if len(members) > 1:
                raise ReleaseCheckError(
                    "sdist %s has multiple PKG-INFO files" % path)
            handle = archive.extractfile(members[0])
            if handle is None:
                raise ReleaseCheckError(
                    "could not read PKG-INFO from sdist %s" % path)
            content = handle.read().decode("utf-8")
    except tarfile.TarError:
        raise ReleaseCheckError(
            "sdist %s is not a valid tar.gz archive" % path)
    except OSError as exc:
        raise ReleaseCheckError(
            "could not read sdist %s: %s" % (path, exc))
    return _parse_metadata(content, "sdist %s" % path)


def _check_metadata(filename, name, version, expected_norm,
                    expected_version):
    if normalize_name(name) != expected_norm:
        raise ReleaseCheckError(
            "artifact %s has package name %r, expected %r"
            % (filename, normalize_name(name), expected_norm))
    actual = parse_version(version, "artifact %s version" % filename)
    if actual != Version(expected_version):
        raise ReleaseCheckError(
            "artifact %s has version %r, expected %r"
            % (filename, version, expected_version))


def check_artifacts(dist_dir, expected_name, expected_version):
    """Ensure dist_dir holds a consistent wheel and sdist pair.

    Both a wheel and a source distribution must be present, and the
    embedded name/version metadata of *every* artifact must match the
    expected package name and version.
    """
    if not os.path.isdir(dist_dir):
        raise ReleaseCheckError(
            "distribution directory %r does not exist" % dist_dir)
    entries = sorted(os.listdir(dist_dir))
    wheels = [f for f in entries if f.endswith(".whl")]
    sdists = [f for f in entries if f.endswith(".tar.gz")]
    if not wheels:
        raise ReleaseCheckError(
            "no wheel (*.whl) was produced in %r" % dist_dir)
    if not sdists:
        raise ReleaseCheckError(
            "no source distribution (*.tar.gz) was produced in %r"
            % dist_dir)
    expected_norm = normalize_name(expected_name)
    parse_final_version(expected_version, "expected version")
    for filename in wheels:
        name, version = read_wheel_metadata(
            os.path.join(dist_dir, filename))
        _check_metadata(filename, name, version, expected_norm,
                        expected_version)
    for filename in sdists:
        name, version = read_sdist_metadata(
            os.path.join(dist_dir, filename))
        _check_metadata(filename, name, version, expected_norm,
                        expected_version)


def ensure_commit_in_main(commit, commit_in_main):
    """Fail unless the released commit is contained in main."""
    if not commit:
        raise ReleaseCheckError("released commit is unknown")
    if not commit_in_main:
        raise ReleaseCheckError(
            "released commit %s is not contained in the main branch"
            % commit)


def validate_release(tag, source_version, dist_dir, package_name,
                     commit, commit_in_main):
    """Run all release checks; raise ReleaseCheckError on any failure."""
    version = check_tag(tag, source_version)
    check_artifacts(dist_dir, package_name, version)
    ensure_commit_in_main(commit, commit_in_main)
    return version


def _git(args):
    return subprocess.run(["git"] + args, capture_output=True, text=True)


def resolve_commit(ref="HEAD"):
    """Resolve a git ref to a commit SHA (fails closed on error)."""
    result = _git(["rev-parse", "%s^{commit}" % ref])
    if result.returncode != 0:
        raise ReleaseCheckError(
            "could not resolve commit for %r: %s"
            % (ref, result.stderr.strip()))
    return result.stdout.strip()


def commit_in_branch(commit, main_ref):
    """Return True iff commit is an ancestor of main_ref."""
    result = _git(["merge-base", "--is-ancestor", commit, main_ref])
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise ReleaseCheckError(
        "could not determine whether %s is contained in %s: %s"
        % (commit, main_ref, result.stderr.strip()))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Validate a MechanicalSoup release before publishing.")
    parser.add_argument(
        "--tag", default=os.environ.get("RELEASE_TAG"),
        help="release tag to validate (default: $RELEASE_TAG)")
    parser.add_argument(
        "--dist-dir", default="dist",
        help="directory containing built artifacts (default: dist)")
    parser.add_argument(
        "--version-file", default=DEFAULT_VERSION_FILE,
        help="path to __version__.py (default: %s)" % DEFAULT_VERSION_FILE)
    parser.add_argument(
        "--commit", default=None,
        help="released commit SHA (default: resolved from HEAD)")
    parser.add_argument(
        "--main-ref", default=DEFAULT_MAIN_REF,
        help="git ref for the main branch (default: %s)"
        % DEFAULT_MAIN_REF)
    args = parser.parse_args(argv)
    try:
        source_version = read_source_version(args.version_file)
        package_name = read_source_title(args.version_file)
        commit = args.commit or resolve_commit("HEAD")
        in_main = commit_in_branch(commit, args.main_ref)
        version = validate_release(
            tag=args.tag,
            source_version=source_version,
            dist_dir=args.dist_dir,
            package_name=package_name,
            commit=commit,
            commit_in_main=in_main,
        )
    except ReleaseCheckError as exc:
        print("Release check failed: %s" % exc, file=sys.stderr)
        return 1
    print("Release checks passed for %s (commit %s)" % (version, commit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
