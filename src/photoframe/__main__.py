import os
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event

import uvicorn

from .settings import SettingsRepository
from .tls import tls_paths


@dataclass(frozen=True)
class ServerConfiguration:
    host: str
    port: int
    ssl_certfile: str | None = None
    ssl_keyfile: str | None = None


@dataclass
class ServerRestart:
    requested: Event = field(default_factory=Event)
    server: uvicorn.Server | None = None

    def request(self) -> None:
        self.requested.set()
        if self.server:
            self.server.should_exit = True


def data_directory() -> Path:
    return Path(os.getenv("PHOTOFRAME_DATA_DIR", "data"))


def server_configuration(target: Path | None = None) -> ServerConfiguration:
    """Resolve the saved and fully validated Uvicorn listener configuration."""
    data_dir = target or data_directory()
    network = SettingsRepository(data_dir).load().network
    certificate, private_key = tls_paths(data_dir, network)
    return ServerConfiguration(
        host=network.bind_address,
        port=network.port,
        ssl_certfile=str(certificate) if certificate else None,
        ssl_keyfile=str(private_key) if private_key else None,
    )


def server_bind(target: Path | None = None) -> tuple[str, int]:
    config = server_configuration(target)
    return config.host, config.port


def main() -> None:
    from .web import create_app

    target = data_directory()
    while True:
        configuration = server_configuration(target)
        restart = ServerRestart()
        application = create_app(target, restart_callback=restart.request)
        server = uvicorn.Server(
            uvicorn.Config(
                application,
                host=configuration.host,
                port=configuration.port,
                reload=False,
                ssl_certfile=configuration.ssl_certfile,
                ssl_keyfile=configuration.ssl_keyfile,
            )
        )
        restart.server = server
        server.run()
        if not restart.requested.is_set():
            break


if __name__ == "__main__":
    main()
