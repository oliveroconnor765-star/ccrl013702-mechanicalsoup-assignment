"""Tests for the release validation script.

These tests verify that the release gate blocks malformed, mismatched,
or otherwise invalid releases before they reach the PyPI upload step.
"""

import io
import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import validate_release as vr


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_wheel(dist_dir, name="MechanicalSoup", version="1.5.0",
                filename=None):
    """Create a minimal .whl with a METADATA file."""
    dist_dir = Path(dist_dir)
    dist_dir.mkdir(parents=True, exist_ok=True)
    if filename is None:
        norm = name.lower().replace("-", "_")
        filename = f"{norm}-{version}-py3-none-any.whl"
    whl_path = dist_dir / filename
    with zipfile.ZipFile(whl_path, "w") as zf:
        norm = name.lower().replace("-", "_")
        metadata = f"Metadata-Version: 2.1
Name: {name}
Version: {version}
"
        zf.writestr(f"{norm}-{version}.dist-info/METADATA", metadata)
    return whl_path


def _make_sdist(dist_dir, name="MechanicalSoup", version="1.5.0",
                filename=None):
    """Create a minimal .tar.gz with a PKG-INFO file."""
    dist_dir = Path(dist_dir)
    dist_dir.mkdir(parents=True, exist_ok=True)
    if filename is None:
        norm = name.lower().replace("-", "_")
        filename = f"{norm}-{version}.tar.gz"
    sdist_path = dist_dir / filename
    pkginfo = f"Metadata-Version: 2.1
Name: {name}
Version: {version}
"
    data = pkginfo.encode("utf-8")
    with tarfile.open(sdist_path, "w:gz") as tf:
        info = tarfile.TarInfo(name=f"{name.lower()}-{version}/PKG-INFO")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return sdist_path


def _make_version_file(repo_root, version):
    """Write a mechanicalsoup/__version__.py into a temp repo."""
    pkg_dir = Path(repo_root) / "mechanicalsoup"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    vf = pkg_dir / "__version__.py"
    vf.write_text(f"__version__ = '{version}'
", encoding="utf-8")
    return vf


# ---------------------------------------------------------------------------
# Tag format tests
# ---------------------------------------------------------------------------


class TestValidateTagFormat:
    def test_valid_release(self):
        v = vr.validate_tag_format("v1.5.0")
        assert str(v) == "1.5.0"

    def test_valid_patch(self):
        v = vr.validate_tag_format("v1.5.1")
        assert str(v) == "1.5.1"

    def test_no_v_prefix(self):
        with pytest.raises(SystemExit, match="does not start") as exc_info:
            vr.validate_tag_format("1.5.0")
        assert exc_info.value.code == 1

    def test_empty_after_v(self):
        with pytest.raises(SystemExit, match="has no version"):
            vr.validate_tag_format("v")

    def test_invalid_pep440(self):
        with pytest.raises(SystemExit, match="not valid PEP 440"):
            vr.validate_tag_format("v1.5.0.0.0.invalid")

    def test_dev_version_rejected(self):
        with pytest.raises(SystemExit, match="not a final release"):
            vr.validate_tag_format("v1.5.0.dev1")

    def test_alpha_version_rejected(self):
        with pytest.raises(SystemExit, match="not a final release"):
            vr.validate_tag_format("v1.5.0a1")

    def test_beta_version_rejected(self):
        with pytest.raises(SystemExit, match="not a final release"):
            vr.validate_tag_format("v1.5.0b2")

    def test_rc_version_rejected(self):
        with pytest.raises(SystemExit, match="not a final release"):
            vr.validate_tag_format("v1.5.0rc1")

    def test_post_version_rejected(self):
        with pytest.raises(SystemExit, match="not a final release"):
            vr.validate_tag_format("v1.5.0.post1")

    def test_completely_malformed(self):
        with pytest.raises(SystemExit):
            vr.validate_tag_format("release-2024")


# ---------------------------------------------------------------------------
# Source version tests
# ---------------------------------------------------------------------------


class TestValidateSourceVersion:
    def test_matching_version(self, tmp_path):
        _make_version_file(tmp_path, "1.5.0")
        from packaging.version import Version
        v = vr.validate_source_version(Version("1.5.0"), str(tmp_path))
        assert str(v) == "1.5.0"

    def test_mismatched_version(self, tmp_path):
        _make_version_file(tmp_path, "1.4.0")
        from packaging.version import Version
        with pytest.raises(SystemExit, match="does not match source version"):
            vr.validate_source_version(Version("1.5.0"), str(tmp_path))

    def test_dev_source_version(self, tmp_path):
        _make_version_file(tmp_path, "1.5.0-dev")
        from packaging.version import Version
        with pytest.raises(SystemExit, match="does not match source version"):
            vr.validate_source_version(Version("1.5.0"), str(tmp_path))

    def test_missing_version_file(self, tmp_path):
        from packaging.version import Version
        with pytest.raises(SystemExit, match="Version file not found"):
            vr.validate_source_version(Version("1.5.0"), str(tmp_path))

    def test_unparseable_version_file(self, tmp_path):
        pkg_dir = tmp_path / "mechanicalsoup"
        pkg_dir.mkdir()
        (pkg_dir / "__version__.py").write_text(
            "# no version here
", encoding="utf-8"
        )
        from packaging.version import Version
        with pytest.raises(SystemExit, match="Cannot parse __version__"):
            vr.validate_source_version(Version("1.5.0"), str(tmp_path))

    def test_invalid_pep440_in_source(self, tmp_path):
        pkg_dir = tmp_path / "mechanicalsoup"
        pkg_dir.mkdir()
        (pkg_dir / "__version__.py").write_text(
            "__version__ = 'not-a-version'
", encoding="utf-8"
        )
        from packaging.version import Version
        with pytest.raises(SystemExit, match="not valid PEP 440"):
            vr.validate_source_version(Version("1.5.0"), str(tmp_path))


# ---------------------------------------------------------------------------
# Artifact validation tests
# ---------------------------------------------------------------------------


class TestValidateArtifacts:
    def test_valid_artifacts(self, tmp_path):
        from packaging.version import Version
        dist = tmp_path / "dist"
        _make_wheel(dist, version="1.5.0")
        _make_sdist(dist, version="1.5.0")
        wheels, sdists = vr.validate_artifacts(str(dist), Version("1.5.0"))
        assert len(wheels) == 1
        assert len(sdists) == 1

    def test_missing_wheel(self, tmp_path):
        from packaging.version import Version
        dist = tmp_path / "dist"
        _make_sdist(dist, version="1.5.0")
        with pytest.raises(SystemExit, match="No wheel") as exc_info:
            vr.validate_artifacts(str(dist), Version("1.5.0"))
        assert exc_info.value.code == 1

    def test_missing_sdist(self, tmp_path):
        from packaging.version import Version
        dist = tmp_path / "dist"
        _make_wheel(dist, version="1.5.0")
        with pytest.raises(SystemExit, match="No source distribution"):
            vr.validate_artifacts(str(dist), Version("1.5.0"))

    def test_missing_dist_dir(self, tmp_path):
        from packaging.version import Version
        with pytest.raises(SystemExit, match="Dist directory does not exist"):
            vr.validate_artifacts(str(tmp_path / "nonexistent"),
                                  Version("1.5.0"))

    def test_wheel_wrong_name(self, tmp_path):
        from packaging.version import Version
        dist = tmp_path / "dist"
        _make_wheel(dist, name="WrongPackage", version="1.5.0")
        _make_sdist(dist, version="1.5.0")
        with pytest.raises(SystemExit, match="Wheel package name"):
            vr.validate_artifacts(str(dist), Version("1.5.0"))

    def test_wheel_wrong_version(self, tmp_path):
        from packaging.version import Version
        dist = tmp_path / "dist"
        _make_wheel(dist, version="1.4.0")
        _make_sdist(dist, version="1.5.0")
        with pytest.raises(SystemExit, match="Wheel version"):
            vr.validate_artifacts(str(dist), Version("1.5.0"))

    def test_sdist_wrong_name(self, tmp_path):
        from packaging.version import Version
        dist = tmp_path / "dist"
        _make_wheel(dist, version="1.5.0")
        _make_sdist(dist, name="WrongPackage", version="1.5.0")
        with pytest.raises(SystemExit, match="Sdist package name"):
            vr.validate_artifacts(str(dist), Version("1.5.0"))

    def test_sdist_wrong_version(self, tmp_path):
        from packaging.version import Version
        dist = tmp_path / "dist"
        _make_wheel(dist, version="1.5.0")
        _make_sdist(dist, version="1.4.0")
        with pytest.raises(SystemExit, match="Sdist version"):
            vr.validate_artifacts(str(dist), Version("1.5.0"))

    def test_multiple_wheels(self, tmp_path):
        from packaging.version import Version
        dist = tmp_path / "dist"
        _make_wheel(dist, version="1.5.0",
                    filename="mechanicalsoup-1.5.0-py3-none-any.whl")
        _make_wheel(dist, version="1.5.0",
                    filename="mechanicalsoup-1.5.0-py2-none-any.whl")
        _make_sdist(dist, version="1.5.0")
        with pytest.raises(SystemExit, match="Multiple wheels"):
            vr.validate_artifacts(str(dist), Version("1.5.0"))

    def test_multiple_sdists(self, tmp_path):
        from packaging.version import Version
        dist = tmp_path / "dist"
        _make_wheel(dist, version="1.5.0")
        _make_sdist(dist, version="1.5.0",
                    filename="mechanicalsoup-1.5.0.tar.gz")
        _make_sdist(dist, version="1.5.0",
                    filename="mechanicalsoup-1.5.0-extra.tar.gz")
        with pytest.raises(SystemExit, match="Multiple sdists"):
            vr.validate_artifacts(str(dist), Version("1.5.0"))

    def test_multiple_inconsistent_artifacts(self, tmp_path):
        """Multiple wheels with different versions both fail."""
        from packaging.version import Version
        dist = tmp_path / "dist"
        _make_wheel(dist, version="1.5.0",
                    filename="mechanicalsoup-1.5.0-py3-none-any.whl")
        _make_wheel(dist, version="1.4.0",
                    filename="mechanicalsoup-1.4.0-py3-none-any.whl")
        _make_sdist(dist, version="1.5.0")
        with pytest.raises(SystemExit, match="Multiple wheels"):
            vr.validate_artifacts(str(dist), Version("1.5.0"))

    def test_empty_dist_dir(self, tmp_path):
        from packaging.version import Version
        dist = tmp_path / "dist"
        dist.mkdir()
        with pytest.raises(SystemExit, match="No wheel"):
            vr.validate_artifacts(str(dist), Version("1.5.0"))

    def test_corrupt_wheel(self, tmp_path):
        from packaging.version import Version
        dist = tmp_path / "dist"
        dist.mkdir()
        whl = dist / "mechanicalsoup-1.5.0-py3-none-any.whl"
        whl.write_bytes(b"not a zip file")
        _make_sdist(dist, version="1.5.0")
        with pytest.raises(SystemExit, match="Cannot read wheel"):
            vr.validate_artifacts(str(dist), Version("1.5.0"))

    def test_corrupt_sdist(self, tmp_path):
        from packaging.version import Version
        dist = tmp_path / "dist"
        dist.mkdir()
        _make_wheel(dist, version="1.5.0")
        sd = dist / "mechanicalsoup-1.5.0.tar.gz"
        sd.write_bytes(b"not a tar file")
        with pytest.raises(SystemExit, match="Cannot read sdist"):
            vr.validate_artifacts(str(dist), Version("1.5.0"))


# ---------------------------------------------------------------------------
# Git ancestry tests
# ---------------------------------------------------------------------------


class TestValidateCommitInMain:
    def _init_git_repo(self, repo_root, branch="main"):
        """Initialize a git repo with one commit on the given branch."""
        subprocess.run(
            ["git", "init", "--initial-branch=" + branch, str(repo_root)],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=str(repo_root), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=str(repo_root), check=True, capture_output=True,
        )
        (Path(repo_root) / "README").write_text("init", encoding="utf-8")
        subprocess.run(
            ["git", "add", "."],
            cwd=str(repo_root), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(repo_root), check=True, capture_output=True,
        )

    def test_commit_on_main(self, tmp_path):
        self._init_git_repo(tmp_path)
        # HEAD is on main, so it should pass
        vr.validate_commit_in_main(str(tmp_path))

    def test_commit_not_in_main(self, tmp_path):
        self._init_git_repo(tmp_path)
        # Create a feature branch and commit on it
        subprocess.run(
            ["git", "checkout", "-b", "feature"],
            cwd=str(tmp_path), check=True, capture_output=True,
        )
        (Path(tmp_path) / "new_file").write_text("x", encoding="utf-8")
        subprocess.run(
            ["git", "add", "."],
            cwd=str(tmp_path), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "feature commit"],
            cwd=str(tmp_path), check=True, capture_output=True,
        )
        with pytest.raises(SystemExit, match="NOT contained in") as exc_info:
            vr.validate_commit_in_main(str(tmp_path))
        assert exc_info.value.code == 1

    def test_ancestor_of_main(self, tmp_path):
        self._init_git_repo(tmp_path)
        # Add another commit on main
        (Path(tmp_path) / "file2").write_text("y", encoding="utf-8")
        subprocess.run(
            ["git", "add", "."],
            cwd=str(tmp_path), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "second"],
            cwd=str(tmp_path), check=True, capture_output=True,
        )
        # Now go back to the first commit
        subprocess.run(
            ["git", "checkout", "HEAD~1"],
            cwd=str(tmp_path), check=True, capture_output=True,
        )
        # This commit IS in main history
        vr.validate_commit_in_main(str(tmp_path))


# ---------------------------------------------------------------------------
# Read source version tests
# ---------------------------------------------------------------------------


class TestReadSourceVersion:
    def test_reads_double_quoted(self, tmp_path):
        pkg = tmp_path / "mechanicalsoup"
        pkg.mkdir()
        (pkg / "__version__.py").write_text(
            '__version__ = "2.0.0"
', encoding="utf-8"
        )
        v = vr.read_source_version(str(tmp_path))
        assert str(v) == "2.0.0"

    def test_reads_single_quoted(self, tmp_path):
        pkg = tmp_path / "mechanicalsoup"
        pkg.mkdir()
        (pkg / "__version__.py").write_text(
            "__version__ = '2.0.0'
", encoding="utf-8"
        )
        v = vr.read_source_version(str(tmp_path))
        assert str(v) == "2.0.0"

    def test_reads_real_file(self):
        # Should be able to read the actual repo file
        v = vr.read_source_version(".")
        # Current version is 1.5.0-dev
        assert str(v) == "1.5.0-dev"
