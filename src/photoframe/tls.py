import ipaddress
import os
import socket
import ssl
import stat
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from .models import CertificateMode, NetworkSettings, WebProtocol

CERTIFICATE_NAME = "photoframe-local.crt"
PRIVATE_KEY_NAME = "photoframe-local.key"  # pragma: allowlist secret


def _readable_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise ValueError(f"{label} does not exist or is not a file: {path}")
    try:
        with path.open("rb"):
            pass
    except OSError as exc:
        raise ValueError(f"{label} cannot be read: {path}") from exc


def validate_certificate_pair(certificate: Path, private_key: Path) -> None:
    """Validate that two readable PEM files form a loadable server identity."""
    _readable_file(certificate, "Certificate file")
    _readable_file(private_key, "Private-key file")
    try:
        parsed = x509.load_pem_x509_certificate(certificate.read_bytes())
    except (OSError, ValueError) as exc:
        raise ValueError("The certificate is not a valid PEM certificate") from exc
    now = datetime.now(UTC)
    if now < parsed.not_valid_before_utc:
        raise ValueError("The certificate is not valid yet")
    if now > parsed.not_valid_after_utc:
        raise ValueError("The certificate has expired")
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        # An explicit empty password prevents OpenSSL from prompting on an
        # encrypted key; unattended server keys must be unencrypted.
        context.load_cert_chain(certificate, private_key, password=b"")
    except (OSError, ssl.SSLError) as exc:
        raise ValueError(
            "The certificate and private key are not a valid matching, unencrypted PEM pair"
        ) from exc


def _local_names() -> tuple[list[x509.DNSName], list[x509.IPAddress]]:
    hostname = socket.gethostname().strip()
    dns_names = {"localhost", "photoframe.local"}
    if hostname:
        dns_names.add(hostname)
    addresses = {ipaddress.ip_address("127.0.0.1")}
    with suppress(OSError):
        for result in socket.getaddrinfo(hostname, None):
            address = result[4][0].split("%", 1)[0]
            with suppress(ValueError):
                addresses.add(ipaddress.ip_address(address))
    return (
        [x509.DNSName(name) for name in sorted(dns_names)],
        [x509.IPAddress(address) for address in sorted(addresses, key=str)],
    )


def _write_new_private(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    descriptor = os.open(path, flags, stat.S_IRUSR | stat.S_IWUSR)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        with suppress(OSError):
            path.unlink()
        raise
    with suppress(OSError):
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def generate_local_certificate(data_dir: Path) -> tuple[Path, Path]:
    """Create the installation-owned certificate pair once, without replacing files."""
    tls_dir = data_dir / "tls"
    certificate = tls_dir / CERTIFICATE_NAME
    private_key = tls_dir / PRIVATE_KEY_NAME
    if certificate.exists() or private_key.exists():
        if not certificate.exists() or not private_key.exists():
            raise ValueError(
                "Automatic HTTPS material is incomplete; remove the app-generated TLS directory "
                "or supply a certificate and key"
            )
        validate_certificate_pair(certificate, private_key)
        return certificate, private_key

    tls_dir.mkdir(parents=True, exist_ok=True)
    with suppress(OSError):
        tls_dir.chmod(stat.S_IRWXU)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "Photoframe local certificate")]
    )
    now = datetime.now(UTC)
    dns_names, addresses = _local_names()
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName([*dns_names, *addresses]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    key_bytes = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    cert_bytes = cert.public_bytes(serialization.Encoding.PEM)
    _write_new_private(private_key, key_bytes)
    try:
        _write_new_private(certificate, cert_bytes)
    except Exception:
        with suppress(OSError):
            private_key.unlink()
        raise
    validate_certificate_pair(certificate, private_key)
    return certificate, private_key


def tls_paths(data_dir: Path, settings: NetworkSettings) -> tuple[Path | None, Path | None]:
    if settings.protocol == WebProtocol.HTTP:
        return None, None
    if settings.certificate_mode == CertificateMode.AUTOMATIC:
        return generate_local_certificate(data_dir)
    certificate = Path(settings.certificate_path or "").expanduser().resolve()
    private_key = Path(settings.private_key_path or "").expanduser().resolve()
    validate_certificate_pair(certificate, private_key)
    return certificate, private_key
