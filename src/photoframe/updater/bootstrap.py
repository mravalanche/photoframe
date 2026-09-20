"""One-time, explicitly root-invoked migration to verified managed releases."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import platform
import re
import secrets
import shutil
import stat
import subprocess  # nosec B404 - administrator-only bootstrap with fixed argv
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization

from ..persistence import atomic_write
from .auth import hash_pin
from .helper import load_public_key, make_release_readable, privileged_environment, safe_extract
from .manifest import SignedManifest
from .state import StateStore, UpdaterState


def validate_path(path: Path) -> Path:
    if not path.is_absolute() or not re.fullmatch(r"/[A-Za-z0-9_./-]+", str(path)):
        raise ValueError("installation paths must be absolute and contain no systemd substitutions")
    if path.is_symlink() or ".." in path.parts:
        raise ValueError("installation paths must not be symbolic links or contain traversal")
    resolved = path.resolve()
    if len(resolved.parts) < 3 or resolved in {Path("/var/lib"), Path("/usr/local")}:
        raise ValueError("refusing an unsafe installation directory")
    return resolved


def trusted_path(path: Path) -> None:
    """A root-owned child is not protected when its parent can be replaced."""
    for item in (path, *path.parents):
        if item.is_symlink():
            raise ValueError(f"trusted installation path contains a symlink: {item}")
        if item.exists():
            info = item.stat()
            if info.st_uid != 0 or info.st_mode & 0o022:
                raise ValueError(
                    f"trusted installation path is writable by a non-root user: {item}"
                )


def check_platform() -> None:
    if sys.platform != "linux" or os.geteuid() != 0:
        raise ValueError("bootstrap must run as root on Linux")
    if platform.machine() not in {"aarch64", "arm64"}:
        raise ValueError("managed release bundles require 64-bit Raspberry Pi OS")
    libc, version = platform.libc_ver()
    if libc != "glibc" or tuple(map(int, version.split(".")[:2])) < (2, 36):
        raise ValueError("managed releases require glibc 2.36 or newer (Bookworm baseline)")


def check_certificate_paths(data: Path) -> None:
    settings = data / "settings.toml"
    if not settings.exists():
        return
    network = tomllib.loads(settings.read_text(encoding="utf-8")).get("network", {})
    for name in ("certificate_path", "private_key_path"):
        value = network.get(name)
        if value and (not isinstance(value, str) or not value.startswith("/")):
            raise ValueError(
                "set supplied TLS certificate and key paths to absolute paths before migration"
            )


def service_units(
    root: Path, data: Path, user: str, group: str, python: Path = Path("/usr/bin/python3.12")
) -> tuple[str, str]:
    for path in (root, data, python):
        validate_path(path)
    for identity in (user, group):
        if not re.fullmatch(r"[a-z_][a-z0-9_-]*[$]?", identity) or identity == "root":
            raise ValueError("a non-root service user and group are required")
    app = f"""[Unit]
Description=Photoframe managed application
After=network-online.target photoframe-updater.service
Wants=network-online.target

[Service]
Type=simple
User={user}
Group={group}
WorkingDirectory={data}
Environment=PHOTOFRAME_DATA_DIR={data}
Environment=PHOTOFRAME_MANAGED_ROOT={root}
Environment=PYTHONUNBUFFERED=1
ExecStart={root}/current/venv/bin/python -m photoframe.updater.launch
Restart=on-failure
RestartSec=5
TimeoutStopSec=120
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths={data}

[Install]
WantedBy=multi-user.target
"""
    helper = f"""[Unit]
Description=Photoframe restricted release updater
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
Group={group}
RuntimeDirectory=photoframe
RuntimeDirectoryMode=0750
Environment=PYTHONUNBUFFERED=1
ExecStart={root}/helper-venv/bin/python -m photoframe.updater.helper --root {root} --data-dir {data} --python {python}
Restart=on-failure
RestartSec=5
TimeoutStopSec=120
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths={root} {data} /var/lib/photoframe-updater /run/photoframe
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6

[Install]
WantedBy=multi-user.target
"""
    return app, helper


def run(argv: list[str]) -> None:
    subprocess.run(argv, check=True, timeout=600, env=privileged_environment())  # nosec B603


def install_environment(python: Path, destination: Path, wheelhouse: Path, version: str) -> None:
    run([str(python), "-m", "venv", str(destination)])
    run(
        [
            str(destination / "bin/python"),
            "-m",
            "pip",
            "--isolated",
            "install",
            "--no-index",
            "--no-cache-dir",
            "--only-binary=:all:",
            "--find-links",
            str(wheelhouse),
            f"photoframe[inky]=={version}",
        ]
    )
    run([str(destination / "bin/python"), "-m", "pip", "--isolated", "check"])


@dataclass(frozen=True)
class InstallLayout:
    config: Path = Path("/etc/photoframe")
    state: Path = Path("/var/lib/photoframe-updater/state.json")
    units: Path = Path("/etc/systemd/system")


def service_enabled(name: str) -> bool:
    result = subprocess.run(  # nosec B603 - fixed systemctl command
        ["/usr/bin/systemctl", "is-enabled", name], capture_output=True, text=True, timeout=30
    )
    return result.returncode == 0


def wait_ready(data: Path, version: str) -> None:
    from .helper import runtime_healthy

    for _ in range(18):
        if runtime_healthy(data, version):
            return
        time.sleep(5)
    raise RuntimeError("managed application failed its version-bound readiness check")


@dataclass(frozen=True)
class SavedFile:
    payload: bytes
    mode: int
    uid: int
    gid: int

    @classmethod
    def read(cls, path: Path) -> SavedFile | None:
        if path.is_symlink():
            raise ValueError(f"refusing symbolic link for migration state: {path}")
        if not path.exists():
            return None
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"migration state is not a regular file: {path}")
        return cls(path.read_bytes(), stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid)

    def restore(self, path: Path) -> None:
        atomic_write(path, self.payload, mode=self.mode)
        getattr(os, "chown")(path, self.uid, self.gid)  # noqa: B009


def bootstrap(args: argparse.Namespace, pin: str, *, layout: InstallLayout | None = None) -> None:
    layout = layout or InstallLayout()
    check_platform()
    import grp
    import pwd

    account = getattr(pwd, "getpwnam")(args.user)  # noqa: B009
    if account.pw_uid == 0:
        raise ValueError("the application must never run as root")
    group = getattr(grp, "getgrgid")(account.pw_gid).gr_name  # noqa: B009
    root, data = validate_path(args.root), validate_path(args.data_dir)
    if root == data or root in data.parents or data in root.parents:
        raise ValueError("application data and immutable releases must be separate")
    trusted_path(args.root)
    for path in (layout.config, layout.state.parent, layout.units):
        trusted_path(path)
    config_names = ("update-signing-key.pem", "updater.token", "update-pin.hash")
    helper_unit = layout.units / "photoframe-updater.service"
    if root.exists() or any(
        (layout.config / name).exists() or (layout.config / name).is_symlink()
        for name in config_names
    ):
        raise ValueError("managed installation already exists; use its updater or recovery guide")
    if any(path.exists() or path.is_symlink() for path in (helper_unit, layout.state)):
        raise ValueError("existing updater unit or state requires manual recovery before migration")
    if not data.is_dir() or data.stat().st_uid != account.pw_uid:
        raise ValueError("existing data directory must belong to the selected service account")
    check_certificate_paths(data)
    python = args.python.resolve(strict=True)
    trusted_path(python)
    run([str(python), "-c", "import sys; assert sys.version_info[:2] == (3, 12)"])
    public_key = load_public_key(args.public_key)
    public_bytes = public_key.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    signed = SignedManifest.verify(args.manifest.read_bytes(), public_key)
    signed.verify_bundle(args.bundle)
    encoded_pin = hash_pin(pin)
    units = service_units(root, data, args.user, group, python)
    unit_path = layout.units / "photoframe.service"
    old_unit = SavedFile.read(unit_path)
    was_enabled = service_enabled("photoframe.service")
    config_existed, state_dir_existed = layout.config.exists(), layout.state.parent.exists()
    if config_existed and (
        layout.config.stat().st_gid != account.pw_gid
        or layout.config.stat().st_mode & 0o050 != 0o050
    ):
        raise ValueError("existing configuration directory must be accessible to the service group")
    version = signed.manifest.version
    slot = root / "versions" / version
    stopped = False
    settings_captured = False
    settings: SavedFile | None = None
    try:
        # Prepare immutable environments while the old service continues to run.
        root.mkdir(mode=0o755)
        root.chmod(0o755)
        # Reverify a private copy: downloaded inputs may live in an app-owned checkout.
        archive = root / ".bootstrap-bundle.tar.gz"
        with args.bundle.open("rb") as source, archive.open("xb") as destination:
            shutil.copyfileobj(source, destination)
        signed.verify_bundle(archive)
        slot.mkdir(parents=True, mode=0o755)
        safe_extract(archive, slot)
        archive.unlink()
        from .bundle import IDENTITY_FIELDS

        identity = {key: signed.manifest.as_dict()[key] for key in IDENTITY_FIELDS}
        if json.loads((slot / "release.json").read_bytes()) != identity:
            raise ValueError("bundle identity disagrees with the signed manifest")
        install_environment(python, slot / "venv", slot / "wheelhouse", version)
        install_environment(python, root / "helper-venv", slot / "wheelhouse", version)
        slot.parent.chmod(0o755)
        make_release_readable(slot)
        atomic_write(slot / ".ready", version.encode(), mode=0o644)
        (root / "current").symlink_to(slot, target_is_directory=True)
        layout.config.mkdir(mode=0o750, exist_ok=True)
        if not config_existed:
            getattr(os, "chown")(layout.config, 0, account.pw_gid)  # noqa: B009
        for name, payload in {
            "update-signing-key.pem": public_bytes,
            "updater.token": secrets.token_hex(32).encode(),
            "update-pin.hash": encoded_pin.encode(),
        }.items():
            path = layout.config / name
            atomic_write(path, payload, mode=0o640)
            getattr(os, "chown")(path, 0, account.pw_gid)  # noqa: B009
        layout.state.parent.mkdir(mode=0o700, exist_ok=True)
        StateStore(layout.state).save(UpdaterState(current_version=version))
        if old_unit is not None:
            atomic_write(root / "previous-service.unit", old_unit.payload, mode=0o600)
        run(["systemctl", "stop", "photoframe.service"])
        stopped = True
        settings = SavedFile.read(data / "settings.toml")
        settings_captured = True
        if settings is not None:
            atomic_write(root / "previous-settings.toml", settings.payload, mode=0o600)
        atomic_write(unit_path, units[0].encode(), mode=0o644)
        atomic_write(helper_unit, units[1].encode(), mode=0o644)
        run(["systemctl", "daemon-reload"])
        run(["systemctl", "enable", "photoframe.service", "photoframe-updater.service"])
        run(["systemctl", "start", "photoframe-updater.service", "photoframe.service"])
        run(["systemctl", "is-active", "photoframe.service", "photoframe-updater.service"])
        wait_ready(data, version)
    except Exception:
        # If recovery itself fails retain the root-owned evidence for manual recovery.
        if stopped:
            run(["systemctl", "stop", "photoframe-updater.service", "photoframe.service"])
            run(["systemctl", "disable", "photoframe-updater.service"])
            helper_unit.unlink(missing_ok=True)
            if old_unit is not None:
                old_unit.restore(unit_path)
            else:
                unit_path.unlink(missing_ok=True)
            if settings is not None:
                settings.restore(data / "settings.toml")
            elif settings_captured:
                (data / "settings.toml").unlink(missing_ok=True)
            run(["systemctl", "daemon-reload"])
            run(["systemctl", "enable" if was_enabled else "disable", "photoframe.service"])
            if old_unit is not None:
                run(["systemctl", "start", "photoframe.service"])
        for name in config_names:
            (layout.config / name).unlink(missing_ok=True)
        layout.state.unlink(missing_ok=True)
        if not config_existed and layout.config.exists():
            layout.config.rmdir()
        if not state_dir_existed and layout.state.parent.exists():
            layout.state.parent.rmdir()
        if root.exists():
            shutil.rmtree(root)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path("/opt/photoframe"))
    parser.add_argument("--python", type=Path, default=Path("/usr/bin/python3.12"))
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--public-key", type=Path, required=True)
    args = parser.parse_args()
    pin = getpass.getpass("New update administrator PIN/passphrase (at least 8 characters): ")
    if pin != getpass.getpass("Confirm PIN/passphrase: "):
        parser.error("PIN entries do not match")
    bootstrap(args, pin)


if __name__ == "__main__":
    main()
