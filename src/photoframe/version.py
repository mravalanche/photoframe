"""Canonical runtime version sourced from installed distribution metadata."""

from importlib.metadata import PackageNotFoundError, version

DEVELOPMENT_VERSION = "0.0.0+development"


def package_version() -> str:
    """Return the running wheel version, with an explicit source-tree fallback."""
    try:
        return version("photoframe")
    except PackageNotFoundError:
        return DEVELOPMENT_VERSION
