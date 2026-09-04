import json
import tomllib
from pathlib import Path

import photoframe

ROOT = Path(__file__).parents[1]


def test_release_versions_and_lockfile_are_consistent():
    manifest = json.loads((ROOT / ".release-please-manifest.json").read_text())
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    lockfile = tomllib.loads((ROOT / "uv.lock").read_text())
    locked_project = next(
        package for package in lockfile["package"] if package["name"] == "photoframe"
    )

    expected = pyproject["project"]["version"]
    assert manifest["."] == expected
    assert photoframe.__version__ == expected
    assert locked_project["version"] == expected


def test_release_please_targets_the_photoframe_lock_entry():
    config = json.loads((ROOT / "release-please-config.json").read_text())

    assert config["packages"]["."]["extra-files"] == [
        {
            "type": "toml",
            "path": "uv.lock",
            "jsonpath": "$.package[?(@.name.value=='photoframe')].version",
        }
    ]
