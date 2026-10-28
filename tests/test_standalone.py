import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "run_standalone", Path(__file__).parents[1] / "scripts/run_standalone.py"
)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_standalone_directory_can_be_reused_without_clearing_state(tmp_path: Path):
    target = module.prepare_data_directory(tmp_path / "demo")
    assert (target / module.DEMO_MARKER).is_file()
    state = target / "diagnostics.json"
    state.write_text("saved history")
    assert module.prepare_data_directory(target) == target
    assert state.read_text() == "saved history"


def test_standalone_refuses_existing_unmarked_data(tmp_path: Path):
    settings = tmp_path / "settings.toml"
    settings.write_text("existing configuration")
    with pytest.raises(ValueError, match="empty directory"):
        module.prepare_data_directory(tmp_path)
    assert settings.read_text() == "existing configuration"
    assert not (tmp_path / module.DEMO_MARKER).exists()


def test_standalone_help_does_not_construct_an_app_or_create_data(tmp_path: Path):
    result = subprocess.run(
        [sys.executable, str(Path(module.__file__)), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "--data-dir" in result.stdout
    assert not list(tmp_path.iterdir())
