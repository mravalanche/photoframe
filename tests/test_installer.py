import shutil
import subprocess
from pathlib import Path

import pytest


def test_pi_installer_and_service_use_locked_secure_environment():
    installer = Path("scripts/install.sh").read_text()
    service = Path("systemd/photoframe.service.template").read_text()
    assert '"$UV_BIN" sync --frozen --extra inky' in installer.replace("\\\n  ", "")
    assert '[[ "$APP_USER" != root ]]' in installer
    assert "refusing unsafe data directory" in installer
    assert "User=@APP_USER@" in service
    assert "Environment=UV_PROJECT_ENVIRONMENT=@DATA_DIR@/venv" in service
    assert "ExecStart=@UV_BIN@ run --no-sync photoframe" in service
    assert "UMask=0077" in service
    assert "running at http://127.0.0.1:8000" not in installer
    assert "saved listener configuration" in installer
    assert "manage the address in Advanced settings" in installer


def test_headless_install_persists_expected_listener_and_tls_defaults():
    installer = Path("scripts/install.sh").read_text()

    assert "--headless" in installer
    assert "settings.network.access = NetworkAccess.LOCAL_NETWORK" in installer
    assert "settings.network.port = 8123" in installer
    assert "settings.network.protocol = WebProtocol.HTTPS" in installer
    assert "settings.network.certificate_mode = CertificateMode.AUTOMATIC" in installer
    assert "generate_local_certificate(data_dir)" in installer


def test_firewall_defaults_to_local_subnets_and_preserves_ssh_before_enable():
    installer = Path("scripts/install.sh").read_text()

    assert "FIREWALL_SOURCE=local" in installer
    assert "ip -o -4 route show scope link" in installer
    assert 'ufw allow from "$source" to any port 8123 proto tcp' in installer
    assert installer.index('ufw allow "$SSH_PORT/tcp"') < installer.index("ufw --force enable")
    assert "--firewall-source any" in installer
    assert "UFW remains inactive" in installer


def test_installer_has_valid_bash_syntax():
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is not available on this development host")
    subprocess.run([bash, "-n", "scripts/install.sh"], check=True)
