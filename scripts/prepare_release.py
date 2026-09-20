"""Validate immutable release identity and stamp only develop build metadata."""

import json
import os
import re
import tomllib
from pathlib import Path


def prepare(root: Path, tag: str, prerelease: bool) -> str:
    pattern = r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:\.dev(0|[1-9]\d*))?"
    match = re.fullmatch(pattern, tag)
    if not match or (match[4] is not None) != prerelease:
        raise ValueError("tag must match stable/develop release classification")
    version = tag[1:]
    project = root / "pyproject.toml"
    lock = root / "uv.lock"
    current = tomllib.loads(project.read_text(encoding="utf-8"))["project"]["version"]
    base = tuple(int(match[index]) for index in (1, 2, 3))
    if base <= (1, 2, 1):
        raise ValueError("historical releases stay immutable")
    if not prerelease:
        if version != current:
            raise ValueError("stable tag must match the committed package version")
        return version
    if base <= tuple(map(int, current.split("."))):
        raise ValueError("develop builds must target a future stable version")
    # Stamp all local release metadata in the disposable CI checkout. Nothing is
    # committed back, so Release Please continues to track the last stable release.
    release_path = root / ".release-please-manifest.json"
    release_metadata = json.loads(release_path.read_text(encoding="utf-8"))
    if release_metadata.get(".") != current:
        raise ValueError("release metadata does not match source")
    release_metadata["."] = version
    release_path.write_text(json.dumps(release_metadata, indent=2) + "\n", encoding="utf-8")
    # Dependencies remain exactly locked. Only the local project's version changes.
    project_text = project.read_text(encoding="utf-8")
    old = f'version = "{current}"'
    project.write_text(project_text.replace(old, f'version = "{version}"', 1), encoding="utf-8")
    lock_text = lock.read_text(encoding="utf-8")
    old_lock = f'name = "photoframe"\nversion = "{current}"'
    if lock_text.count(old_lock) != 1:
        raise ValueError("locked project version does not match source")
    lock.write_text(
        lock_text.replace(old_lock, f'name = "photoframe"\nversion = "{version}"'), encoding="utf-8"
    )
    return version


if __name__ == "__main__":
    prepare(Path.cwd(), os.environ["RELEASE_TAG"], os.environ["RELEASE_PRERELEASE"] == "true")
