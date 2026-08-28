from pathlib import Path

import pytest

from photoframe.models import CertificateMode, NetworkSettings, WebProtocol
from photoframe.tls import generate_local_certificate, tls_paths, validate_certificate_pair


def test_generated_pair_is_reused_without_replacing_private_key(tmp_path: Path):
    certificate, private_key = generate_local_certificate(tmp_path)
    original_key = private_key.read_bytes()

    assert generate_local_certificate(tmp_path) == (certificate, private_key)
    assert private_key.read_bytes() == original_key
    validate_certificate_pair(certificate, private_key)


def test_incomplete_generated_pair_is_not_overwritten(tmp_path: Path):
    tls_dir = tmp_path / "tls"
    tls_dir.mkdir()
    key = tls_dir / "photoframe-local.key"
    key.write_text("user data")

    with pytest.raises(ValueError, match="incomplete"):
        generate_local_certificate(tmp_path)

    assert key.read_text() == "user data"


def test_supplied_pair_is_validated_and_never_modified(tmp_path: Path):
    source = tmp_path / "source"
    certificate, private_key = generate_local_certificate(source)
    before = (certificate.read_bytes(), private_key.read_bytes())
    settings = NetworkSettings(
        protocol=WebProtocol.HTTPS,
        certificate_mode=CertificateMode.SUPPLIED,
        certificate_path=str(certificate),
        private_key_path=str(private_key),
    )

    assert tls_paths(tmp_path / "app-data", settings) == (
        certificate.resolve(),
        private_key.resolve(),
    )
    assert (certificate.read_bytes(), private_key.read_bytes()) == before


def test_supplied_pair_reports_clear_error(tmp_path: Path):
    settings = NetworkSettings(
        protocol=WebProtocol.HTTPS,
        certificate_mode=CertificateMode.SUPPLIED,
        certificate_path=str(tmp_path / "missing.crt"),
        private_key_path=str(tmp_path / "missing.key"),
    )

    with pytest.raises(ValueError, match="Certificate file does not exist"):
        tls_paths(tmp_path, settings)
