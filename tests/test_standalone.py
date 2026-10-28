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


def test_standalone_disables_inherited_managed_installation(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PHOTOFRAME_DATA_DIR", "/unused/old-data")
    monkeypatch.setenv("PHOTOFRAME_DEMO_MODE", "0")
    for name in ("PHOTOFRAME_MANAGED_ROOT", "PHOTOFRAME_RELEASE_SLOT"):
        monkeypatch.setenv(name, "/unused/managed-marker")
    monkeypatch.setattr(sys, "argv", ["run_standalone.py", "--data-dir", str(tmp_path)])
    calls = []
    monkeypatch.setattr(module.uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    module.main()
    assert module.os.environ["PHOTOFRAME_DEMO_MODE"] == "1"
    assert module.os.environ["PHOTOFRAME_DATA_DIR"] == str(tmp_path)
    assert "PHOTOFRAME_MANAGED_ROOT" not in module.os.environ
    assert "PHOTOFRAME_RELEASE_SLOT" not in module.os.environ
    assert calls == [(("photoframe.web.routes:app",), {"host": "127.0.0.1", "port": 8765})]


def test_demo_display_setup_never_probes_physical_hardware(tmp_path: Path, monkeypatch):
    from photoframe.providers import DemoProvider
    from photoframe.services.runtime import Runtime
    from photoframe.settings import SecretStore, SettingsRepository

    monkeypatch.setattr(
        "photoframe.services.runtime.discover_inky",
        lambda device: pytest.fail("Demo must never probe physical display hardware"),
    )
    runtime = Runtime(
        SettingsRepository(tmp_path),
        SecretStore(tmp_path),
        lambda kind: DemoProvider(),
        DemoProvider(),
    )
    runtime.initialise_display()
    assert not runtime.has_display()
