import os
import stat
import tempfile
import tomllib
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from threading import RLock

import tomli_w
from cryptography.fernet import Fernet, InvalidToken

from .models import AppSettings


def _private(path: Path) -> None:
    with suppress(OSError):
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _private(Path(temporary))
        os.replace(temporary, path)
        _private(path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class SettingsRepository:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.path = data_dir / "settings.toml"
        self._lock = RLock()

    def load(self) -> AppSettings:
        with self._lock:
            if not self.path.exists():
                settings = AppSettings()
                self.save(settings)
                return settings
            with self.path.open("rb") as handle:
                return AppSettings.model_validate(tomllib.load(handle))

    def save(self, settings: AppSettings) -> None:
        with self._lock:
            validated = AppSettings.model_validate(settings)
            payload = tomli_w.dumps(validated.model_dump(mode="json", exclude_none=True)).encode()
            _atomic_write(self.path, payload)

    def update(self, change: Callable[[AppSettings], None]) -> AppSettings:
        """Apply one validated read-modify-write transaction."""
        with self._lock:
            settings = self.load()
            change(settings)
            self.save(settings)
            return settings


class SecretStore:
    """Small local encrypted store; secrets are deliberately separate from editable settings."""

    def __init__(self, data_dir: Path):
        self.key_path = data_dir / ".secret-key"
        self.value_path = data_dir / "secrets.bin"

    def _fernet(self) -> Fernet:
        if not self.key_path.exists():
            _atomic_write(self.key_path, Fernet.generate_key())
        return Fernet(self.key_path.read_bytes())

    def set_api_key(self, value: str) -> None:
        if not value.strip():
            raise ValueError("API key cannot be empty")
        _atomic_write(self.value_path, self._fernet().encrypt(value.strip().encode()))

    def get_api_key(self) -> str | None:
        if not self.value_path.exists():
            return None
        try:
            return self._fernet().decrypt(self.value_path.read_bytes()).decode()
        except InvalidToken as exc:
            raise RuntimeError("Stored credential cannot be decrypted") from exc

    def exists(self) -> bool:
        return self.value_path.exists()

    def clear(self) -> None:
        """Remove the saved credential and its local encryption key."""
        with suppress(FileNotFoundError):
            self.value_path.unlink()
        with suppress(FileNotFoundError):
            self.key_path.unlink()
