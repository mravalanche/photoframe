from pathlib import Path


def test_pi_installer_and_service_use_locked_inky_environment():
    installer = Path("scripts/install.sh").read_text()
    service = Path("systemd/photoframe.service.template").read_text()
    assert '"$UV_BIN" sync --frozen --extra inky' in installer
    assert '[[ "$APP_USER" != root ]]' in installer
    assert "User=@APP_USER@" in service
    assert "ExecStart=@UV_BIN@ run --no-sync photoframe" in service
