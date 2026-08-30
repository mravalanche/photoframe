import os
from pathlib import Path

import pytest

from photoframe import persistence


def test_atomic_write_replaces_after_file_fsync_and_syncs_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    target = tmp_path / "state.bin"
    target.write_bytes(b"old")
    events: list[tuple[str, Path]] = []
    original_replace = os.replace

    def replace(source: Path, destination: Path) -> None:
        source_path, destination_path = Path(source), Path(destination)
        assert source_path.parent == target.parent
        events.append(("replace", destination_path))
        original_replace(source, destination)

    def sync_directory(directory: Path) -> None:
        assert target.read_bytes() == b"new"
        events.append(("directory-fsync", directory))

    monkeypatch.setattr(persistence.os, "replace", replace)
    monkeypatch.setattr(persistence, "_fsync_directory", sync_directory)

    persistence.atomic_write(target, b"new")

    assert target.read_bytes() == b"new"
    assert events == [("replace", target), ("directory-fsync", tmp_path)]
    assert list(tmp_path.glob(".*.tmp")) == []


def test_atomic_write_cleans_temporary_file_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    target = tmp_path / "state.bin"
    target.write_bytes(b"old")

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(persistence.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        persistence.atomic_write(target, b"new")

    assert target.read_bytes() == b"old"
    assert list(tmp_path.glob(".*.tmp")) == []


def test_atomic_write_propagates_directory_fsync_failure_after_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    target = tmp_path / "state.bin"

    def fail_directory_fsync(_directory: Path) -> None:
        raise OSError("directory fsync failed")

    monkeypatch.setattr(persistence, "_fsync_directory", fail_directory_fsync)

    with pytest.raises(OSError, match="directory fsync failed"):
        persistence.atomic_write(target, b"durable file")

    assert target.read_bytes() == b"durable file"
    assert list(tmp_path.glob(".*.tmp")) == []


def test_directory_fsync_is_skipped_where_unsupported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(persistence.os, "name", "nt")
    monkeypatch.setattr(
        persistence.os,
        "open",
        lambda *_args, **_kwargs: pytest.fail("directory should not be opened"),
    )

    persistence._fsync_directory(tmp_path)


@pytest.mark.skipif(os.name != "posix", reason="directory descriptors are POSIX-only")
def test_directory_fsync_uses_and_closes_directory_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    events: list[tuple[str, object]] = []

    def open_directory(path: Path, flags: int) -> int:
        events.append(("open", (path, flags)))
        return 42

    monkeypatch.setattr(persistence.os, "open", open_directory)
    monkeypatch.setattr(
        persistence.os, "fsync", lambda descriptor: events.append(("fsync", descriptor))
    )
    monkeypatch.setattr(
        persistence.os, "close", lambda descriptor: events.append(("close", descriptor))
    )

    persistence._fsync_directory(tmp_path)

    assert events[0][0] == "open"
    assert events[1:] == [("fsync", 42), ("close", 42)]
