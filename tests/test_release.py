"""Tests for the release-publishing safety checks (tools/check_release).

These tests are fully local: they fabricate wheel and sdist archives in
a temporary directory and never touch the network or PyPI.
"""

import io
import os
import sys
import tarfile
import zipfile

import pytest

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), os.pardir, "tools"))

import check_release  # noqa: E402

NAME = "MechanicalSoup"
VERSION = "1.4.0"
TAG = "v1.4.0"
COMMIT = "0123456789abcdef0123456789abcdef01234567"


def _metadata(name, version):
    return (
        "Metadata-Version: 2.1\n"
        "Name: %s\n"
        "Version: %s\n"
        "\n" % (name, version)
    )


def make_wheel(directory, name=NAME, version=VERSION, metadata=None,
               include_metadata=True, valid_zip=True):
    base = check_release.normalize_name(name).replace("-", "_")
    path = os.path.join(str(directory), "%s-%s-py3-none-any.whl"
                        % (base, version))
    if not valid_zip:
        with open(path, "wb") as handle:
            handle.write(b"this is not a zip file")
        return path
    with zipfile.ZipFile(path, "w") as archive:
        if include_metadata:
            member = "%s-%s.dist-info/METADATA" % (name, version)
            content = (metadata if metadata is not None
                       else _metadata(name, version))
            archive.writestr(member, content)
        else:
            archive.writestr("dummy.txt", "no metadata here")
    return path


def make_sdist(directory, name=NAME, version=VERSION, metadata=None,
               include_pkginfo=True, valid_gz=True):
    fname = "%s-%s.tar.gz" % (check_release.normalize_name(name), version)
    path = os.path.join(str(directory), fname)
    if not valid_gz:
        with open(path, "wb") as handle:
            handle.write(b"this is not a gzip tar file")
        return path
    with tarfile.open(path, "w:gz") as archive:
        if include_pkginfo:
            text = (metadata if metadata is not None
                    else _metadata(name, version))
            data = text.encode("utf-8")
            member = "%s-%s/PKG-INFO" % (name, version)
        else:
            data = b"placeholder"
            member = "%s-%s/setup.py" % (name, version)
        info = tarfile.TarInfo(member)
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    return path


def good_dist(directory):
    make_wheel(directory)
    make_sdist(directory)
    return str(directory)


# -- Valid release -----------------------------------------------------

def test_valid_release(tmp_path):
    dist = good_dist(tmp_path)
    version = check_release.validate_release(
        tag=TAG, source_version=VERSION, dist_dir=dist,
        package_name=NAME, commit=COMMIT, commit_in_main=True)
    assert version == VERSION


# -- Malformed or mismatched tags --------------------------------------

def test_tag_missing_v_prefix(tmp_path):
    dist = good_dist(tmp_path)
    with pytest.raises(check_release.ReleaseCheckError):
        check_release.validate_release(
            tag="1.4.0", source_version=VERSION, dist_dir=dist,
            package_name=NAME, commit=COMMIT, commit_in_main=True)


def test_tag_not_a_version(tmp_path):
    dist = good_dist(tmp_path)
    with pytest.raises(check_release.ReleaseCheckError):
        check_release.validate_release(
            tag="vfoobar", source_version=VERSION, dist_dir=dist,
            package_name=NAME, commit=COMMIT, commit_in_main=True)


def test_tag_version_mismatch(tmp_path):
    dist = good_dist(tmp_path)
    with pytest.raises(check_release.ReleaseCheckError):
        check_release.validate_release(
            tag="v1.4.1", source_version=VERSION, dist_dir=dist,
            package_name=NAME, commit=COMMIT, commit_in_main=True)


def test_empty_tag():
    with pytest.raises(check_release.ReleaseCheckError):
        check_release.check_tag("", VERSION)


# -- Development versions ----------------------------------------------

def test_development_tag(tmp_path):
    dist = good_dist(tmp_path)
    with pytest.raises(check_release.ReleaseCheckError):
        check_release.validate_release(
            tag="v1.4.0.dev1", source_version="1.4.0.dev1",
            dist_dir=dist, package_name=NAME, commit=COMMIT,
            commit_in_main=True)


def test_development_tag_dash_dev():
    with pytest.raises(check_release.ReleaseCheckError):
        check_release.check_tag("v1.5.0-dev", "1.5.0-dev")


def test_parse_final_version_rejects_dev():
    with pytest.raises(check_release.ReleaseCheckError):
        check_release.parse_final_version("1.0.0.dev0", "version")


def test_parse_final_version_rejects_invalid():
    with pytest.raises(check_release.ReleaseCheckError):
        check_release.parse_final_version("not-a-version", "version")


# -- Inconsistent source versions --------------------------------------

def test_inconsistent_source_version(tmp_path):
    dist = good_dist(tmp_path)
    with pytest.raises(check_release.ReleaseCheckError):
        check_release.validate_release(
            tag=TAG, source_version="9.9.9", dist_dir=dist,
            package_name=NAME, commit=COMMIT, commit_in_main=True)


def test_read_source_version_ok(tmp_path):
    path = tmp_path / "version.py"
    path.write_text(
        "__title__ = 'MechanicalSoup'\n__version__ = '1.4.0'\n")
    assert check_release.read_source_version(str(path)) == "1.4.0"
    assert check_release.read_source_title(str(path)) == "MechanicalSoup"


def test_read_source_version_missing_field(tmp_path):
    path = tmp_path / "version.py"
    path.write_text("__title__ = 'MechanicalSoup'\n")
    with pytest.raises(check_release.ReleaseCheckError):
        check_release.read_source_version(str(path))


def test_read_source_version_unreadable(tmp_path):
    missing = tmp_path / "does_not_exist.py"
    with pytest.raises(check_release.ReleaseCheckError):
        check_release.read_source_version(str(missing))


# -- Missing or invalid wheel/sdist metadata ---------------------------

def test_dist_dir_missing(tmp_path):
    missing = os.path.join(str(tmp_path), "nope")
    with pytest.raises(check_release.ReleaseCheckError):
        check_release.check_artifacts(missing, NAME, VERSION)


def test_missing_wheel(tmp_path):
    make_sdist(tmp_path)
    with pytest.raises(check_release.ReleaseCheckError):
        check_release.check_artifacts(str(tmp_path), NAME, VERSION)


def test_missing_sdist(tmp_path):
    make_wheel(tmp_path)
    with pytest.raises(check_release.ReleaseCheckError):
        check_release.check_artifacts(str(tmp_path), NAME, VERSION)


def test_wheel_without_metadata(tmp_path):
    make_wheel(tmp_path, include_metadata=False)
    make_sdist(tmp_path)
    with pytest.raises(check_release.ReleaseCheckError):
        check_release.check_artifacts(str(tmp_path), NAME, VERSION)


def test_wheel_invalid_zip(tmp_path):
    make_wheel(tmp_path, valid_zip=False)
    make_sdist(tmp_path)
    with pytest.raises(check_release.ReleaseCheckError):
        check_release.check_artifacts(str(tmp_path), NAME, VERSION)


def test_sdist_without_pkginfo(tmp_path):
    make_wheel(tmp_path)
    make_sdist(tmp_path, include_pkginfo=False)
    with pytest.raises(check_release.ReleaseCheckError):
        check_release.check_artifacts(str(tmp_path), NAME, VERSION)


def test_sdist_invalid_gz(tmp_path):
    make_wheel(tmp_path)
    make_sdist(tmp_path, valid_gz=False)
    with pytest.raises(check_release.ReleaseCheckError):
        check_release.check_artifacts(str(tmp_path), NAME, VERSION)


def test_wheel_wrong_name(tmp_path):
    make_wheel(tmp_path, name="EvilPackage")
    make_sdist(tmp_path)
    with pytest.raises(check_release.ReleaseCheckError):
        check_release.check_artifacts(str(tmp_path), NAME, VERSION)


def test_wheel_wrong_version(tmp_path):
    make_wheel(tmp_path, version="9.9.9")
    make_sdist(tmp_path)
    with pytest.raises(check_release.ReleaseCheckError):
        check_release.check_artifacts(str(tmp_path), NAME, VERSION)


def test_sdist_wrong_version(tmp_path):
    make_wheel(tmp_path)
    make_sdist(tmp_path, version="9.9.9")
    with pytest.raises(check_release.ReleaseCheckError):
        check_release.check_artifacts(str(tmp_path), NAME, VERSION)


# -- Multiple inconsistent artifacts -----------------------------------

def test_multiple_inconsistent_wheels(tmp_path):
    make_wheel(tmp_path, version=VERSION)
    make_wheel(tmp_path, version="9.9.9")
    make_sdist(tmp_path)
    with pytest.raises(check_release.ReleaseCheckError):
        check_release.check_artifacts(str(tmp_path), NAME, VERSION)


def test_name_normalization():
    # PEP 503 normalization collapses separators and lowercases.
    assert check_release.normalize_name("Mechanical_Soup.X") == (
        "mechanical-soup-x")
    assert check_release.normalize_name("MechanicalSoup") == (
        "mechanicalsoup")


def test_name_normalization_accepts_case(tmp_path):
    # The embedded Name only needs to match after PEP 503
    # normalization, so a different casing of MechanicalSoup is fine.
    make_wheel(tmp_path, metadata=_metadata("mechanicalsoup", VERSION))
    make_sdist(tmp_path, metadata=_metadata("MECHANICALSOUP", VERSION))
    check_release.check_artifacts(str(tmp_path), NAME, VERSION)


# -- Release commit outside main ---------------------------------------

def test_commit_outside_main(tmp_path):
    dist = good_dist(tmp_path)
    with pytest.raises(check_release.ReleaseCheckError):
        check_release.validate_release(
            tag=TAG, source_version=VERSION, dist_dir=dist,
            package_name=NAME, commit=COMMIT, commit_in_main=False)


def test_commit_unknown():
    with pytest.raises(check_release.ReleaseCheckError):
        check_release.ensure_commit_in_main("", True)
