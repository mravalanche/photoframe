from importlib.metadata import PackageNotFoundError

from fastapi.testclient import TestClient

import photoframe
from photoframe import version
from photoframe.web import create_app


def test_package_version_uses_distribution_metadata(monkeypatch) -> None:
    monkeypatch.setattr(version, "version", lambda _name: "9.8.7")
    assert version.package_version() == "9.8.7"


def test_package_version_has_explicit_development_fallback(monkeypatch) -> None:
    def missing(_name: str) -> str:
        raise PackageNotFoundError

    monkeypatch.setattr(version, "version", missing)
    assert version.package_version() == "0.0.0+development"


def test_openapi_health_and_footer_report_the_running_distribution_version(tmp_path) -> None:
    app = create_app(tmp_path, demo_mode=True)
    with TestClient(app) as client:
        assert app.openapi()["info"]["version"] == photoframe.__version__
        assert client.get("/health").json()["version"] == photoframe.__version__
        assert f"Photoframe {photoframe.__version__}" in client.get("/partials/workspace").text
