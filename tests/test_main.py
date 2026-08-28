from pathlib import Path

import pytest

from photoframe.__main__ import run_server, server_bind, server_configuration
from photoframe.models import NetworkAccess, WebProtocol
from photoframe.settings import SettingsRepository


def test_server_is_localhost_http_only_by_default(tmp_path: Path):
    assert server_bind(tmp_path) == ("127.0.0.1", 8000)
    assert server_configuration(tmp_path).ssl_certfile is None
    assert server_configuration(tmp_path).ssl_keyfile is None


def test_server_reads_saved_network_configuration(tmp_path: Path):
    repository = SettingsRepository(tmp_path)
    settings = repository.load()
    settings.network.access = NetworkAccess.LOCAL_NETWORK
    settings.network.port = 8123
    repository.save(settings)

    assert server_bind(tmp_path) == ("0.0.0.0", 8123)


def test_server_reads_minimal_headless_network_configuration(tmp_path: Path):
    (tmp_path / "settings.toml").write_text(
        """[network]
access = "local_network"
port = 8080
protocol = "http"
certificate_mode = "automatic"
"""
    )

    configuration = server_configuration(tmp_path)

    assert configuration.host == "0.0.0.0"
    assert configuration.port == 8080
    assert configuration.ssl_certfile is None
    assert configuration.ssl_keyfile is None


def test_server_prepares_automatic_https_material(tmp_path: Path):
    repository = SettingsRepository(tmp_path)
    settings = repository.load()
    settings.network.protocol = WebProtocol.HTTPS
    repository.save(settings)

    configuration = server_configuration(tmp_path)

    assert configuration.host == "127.0.0.1"
    assert configuration.ssl_certfile
    assert configuration.ssl_keyfile
    assert Path(configuration.ssl_certfile).is_file()
    assert Path(configuration.ssl_keyfile).is_file()


def test_intentional_keyboard_interrupt_stops_without_escaping():
    class InterruptedServer:
        def run(self):
            raise KeyboardInterrupt

    assert run_server(InterruptedServer()) is False  # type: ignore[arg-type]


def test_real_server_error_is_not_hidden():
    class FailedServer:
        def run(self):
            raise RuntimeError("bind failed")

    with pytest.raises(RuntimeError, match="bind failed"):
        run_server(FailedServer())  # type: ignore[arg-type]
