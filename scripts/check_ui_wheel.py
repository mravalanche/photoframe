"""Confirm the locally built stylesheet is included in the installable wheel."""

import sys
from pathlib import Path
from zipfile import ZipFile


def main(directory: Path) -> None:
    wheels = list(directory.glob("photoframe-*.whl"))
    if len(wheels) != 1:
        raise SystemExit(f"Expected one Photoframe wheel in {directory}; found {len(wheels)}")
    with ZipFile(wheels[0]) as archive:
        stylesheet = archive.read("photoframe/static/v2.css")
        packaged = set(archive.namelist())
    if len(stylesheet) < 1000:
        raise SystemExit("The packaged v2 stylesheet is missing or unexpectedly small")
    required_fonts = {
        "photoframe/static/fonts/inter-latin-wght-normal.woff2",
        "photoframe/static/fonts/newsreader-latin-wght-normal.woff2",
        "photoframe/static/fonts/inter-OFL.txt",
        "photoframe/static/fonts/newsreader-OFL.txt",
    }
    missing = required_fonts - packaged
    if missing:
        raise SystemExit(f"The packaged UI is missing fonts or licenses: {sorted(missing)}")
    print(f"Packaged v2 stylesheet: {len(stylesheet):,} bytes")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
