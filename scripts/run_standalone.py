"""Run an isolated local demo for interactive feature-branch review."""

import argparse
import os
from pathlib import Path

import uvicorn

DEMO_MARKER = ".photoframe-standalone-demo"


def prepare_data_directory(target: Path) -> Path:
    target = target.resolve()
    target.mkdir(parents=True, exist_ok=True)
    marker = target / DEMO_MARKER
    if not marker.is_file():
        if any(target.iterdir()):
            raise ValueError("Choose an empty directory or an existing standalone demo directory.")
        marker.write_text("Photoframe standalone demo data\n", encoding="utf-8")
    return target


def port_number(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("Port must be between 1 and 65535.")
    return port


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=port_number, default=8765)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / ".standalone-data",
        help="Isolated demo state; defaults to .standalone-data in this checkout.",
    )
    args = parser.parse_args()
    try:
        target = prepare_data_directory(args.data_dir)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    # The web module constructs its default app when imported. Set its isolated
    # configuration before Uvicorn imports it, including for hardware detection.
    os.environ["PHOTOFRAME_DATA_DIR"] = str(target)
    os.environ["PHOTOFRAME_DEMO_MODE"] = "1"
    for marker in ("PHOTOFRAME_MANAGED_ROOT", "PHOTOFRAME_RELEASE_SLOT"):
        os.environ.pop(marker, None)
    print(f"Standalone demo: http://127.0.0.1:{args.port}/photos")
    print(f"Demo data: {target}")
    uvicorn.run("photoframe.web.routes:app", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
