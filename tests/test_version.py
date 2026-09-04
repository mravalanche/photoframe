from importlib.metadata import PackageNotFoundError

from photoframe import version


def test_package_version_uses_distribution_metadata(monkeypatch) -> None:
    monkeypatch.setattr(version, "version", lambda _name: "9.8.7")
    assert version.package_version() == "9.8.7"


def test_package_version_has_explicit_development_fallback(monkeypatch) -> None:
    def missing(_name: str) -> str:
        raise PackageNotFoundError

    monkeypatch.setattr(version, "version", missing)
    assert version.package_version() == "0.0.0+development"
