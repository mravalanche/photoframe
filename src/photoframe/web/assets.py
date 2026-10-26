"""Content versions for browser assets, including source-checkout previews."""

from hashlib import sha256
from pathlib import Path


def ui_asset_revision(directory: Path) -> str:
    digest = sha256()
    for asset in [directory / "v2.css", *sorted(directory.glob("*.js"))]:
        digest.update(asset.name.encode())
        digest.update(asset.read_bytes())
    return digest.hexdigest()[:12]
