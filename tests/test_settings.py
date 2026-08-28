from pathlib import Path
from threading import Barrier, Thread

from photoframe.models import AppSettings, NetworkAccess
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


def test_local_network_access_round_trips_with_current_value(tmp_path: Path):
    repository = SettingsRepository(tmp_path)
    settings = AppSettings()
    settings.network.access = NetworkAccess.LOCAL_NETWORK

    repository.save(settings)

    assert repository.load().network.access == NetworkAccess.LOCAL_NETWORK
    assert 'access = "local_network"' in repository.path.read_text()
    assert 'access = "home_network"' not in repository.path.read_text()


def test_legacy_home_network_access_normalizes_on_save(tmp_path: Path):
    repository = SettingsRepository(tmp_path)
    repository.path.write_text('[network]\naccess = "home_network"\n')

    settings = repository.load()

    assert settings.network.access == NetworkAccess.LOCAL_NETWORK
    assert settings.network.bind_address == "0.0.0.0"

    repository.save(settings)

    assert 'access = "local_network"' in repository.path.read_text()
    assert 'access = "home_network"' not in repository.path.read_text()


def test_repository_update_preserves_prior_transaction(tmp_path: Path):
    repository = SettingsRepository(tmp_path)
    repository.load()
    repository.update(lambda settings: setattr(settings.frame, "album_name", "One"))
    repository.update(lambda settings: setattr(settings.verification, "message", "Checked"))
    loaded = repository.load()
    assert loaded.frame.album_name == "One"
    assert loaded.verification.message == "Checked"


def test_concurrent_transactions_do_not_lose_updates(tmp_path: Path):
    repository = SettingsRepository(tmp_path)
    repository.load()
    start = Barrier(3)

    def album_update():
        start.wait()
        repository.update(lambda settings: setattr(settings.frame, "album_name", "Concurrent"))

    def verification_update():
        start.wait()
        repository.update(lambda settings: setattr(settings.verification, "message", "Current"))

    threads = [Thread(target=album_update), Thread(target=verification_update)]
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join()
    loaded = repository.load()
    assert loaded.frame.album_name == "Concurrent"
    assert loaded.verification.message == "Current"
