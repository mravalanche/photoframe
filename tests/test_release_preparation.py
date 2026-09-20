import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "prepare_release", Path(__file__).parents[1] / "scripts/prepare_release.py"
)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def checkout(tmp_path, version="1.2.1"):
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "photoframe"\nversion = "{version}"\n'
    )
    (tmp_path / "uv.lock").write_text(
        f'[[package]]\nname = "photoframe"\nversion = "{version}"\n[[package]]\nname = "example"\nversion = "3.0.0"\n'
    )
    return tmp_path


def test_develop_stamp_changes_only_project_identity(tmp_path):
    root = checkout(tmp_path)
    assert module.prepare(root, "v1.3.0.dev1", True) == "1.3.0.dev1"
    assert 'version = "1.3.0.dev1"' in (root / "pyproject.toml").read_text()
    assert 'name = "example"\nversion = "3.0.0"' in (root / "uv.lock").read_text()


@pytest.mark.parametrize(
    "tag,prerelease",
    [
        ("v1.3.0.dev1", False),
        ("v1.3.0", True),
        ("v1.2.1.dev1", True),
        ("v1.3.0.dev01", True),
        ("v1.3.0", False),
    ],
)
def test_release_identity_is_not_silently_reclassified(tmp_path, tag, prerelease):
    with pytest.raises(ValueError):
        module.prepare(checkout(tmp_path), tag, prerelease)


def test_stable_release_requires_committed_version(tmp_path):
    root = checkout(tmp_path, "1.3.0")
    before = (root / "uv.lock").read_bytes()
    module.prepare(root, "v1.3.0", False)
    assert (root / "uv.lock").read_bytes() == before
