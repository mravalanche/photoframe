from pathlib import Path

from photoframe.models import AppSettings
from photoframe.settings import SecretStore, SettingsRepository


def test_settings_round_trip_and_secret_is_separate(tmp_path: Path):
    repository = SettingsRepository(tmp_path)
    settings = AppSettings()
    settings.frame.album_id = "album-1"
    repository.save(settings)
    secrets = SecretStore(tmp_path)
    secrets.set_api_key("super-secret")
    assert repository.load().frame.album_id == "album-1"
    assert "super-secret" not in repository.path.read_text()
    assert "super-secret" not in secrets.value_path.read_text()
    assert secrets.get_api_key() == "super-secret"


def test_invalid_human_edit_is_rejected(tmp_path: Path):
    repository = SettingsRepository(tmp_path)
    repository.path.write_text("[frame]\nrotation_seconds = 1\n")
    try:
        repository.load()
    except ValueError:
        pass
    else:
        raise AssertionError("invalid TOML settings should fail validation")
