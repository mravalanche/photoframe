"""Publish new assets to the triggering release; never replace existing release bytes."""

import os
import sys
from pathlib import Path

import httpx


def main() -> None:
    release_id = os.environ["RELEASE_ID"]
    if not release_id.isdecimal():
        raise ValueError("invalid release identity")
    with httpx.Client(
        headers={
            "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
            "Accept": "application/vnd.github+json",
        },
        timeout=120,
    ) as client:
        base = f"https://api.github.com/repos/mravalanche/photoframe/releases/{release_id}"
        response = client.get(base + "/assets")
        response.raise_for_status()
        existing = {asset["name"] for asset in response.json()}
        files = sorted(Path(sys.argv[1]).iterdir())
        if any(path.name in existing for path in files):
            raise ValueError("release already has managed assets; refusing to overwrite")
        for path in files:
            response = client.post(
                f"https://uploads.github.com/repos/mravalanche/photoframe/releases/{release_id}/assets",
                params={"name": path.name},
                headers={"Content-Type": "application/octet-stream"},
                content=path.read_bytes(),
            )
            response.raise_for_status()


if __name__ == "__main__":
    main()
