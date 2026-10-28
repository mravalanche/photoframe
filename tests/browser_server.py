"""Local-only deterministic application for browser integration checks."""

from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn

from photoframe.web import create_app

if __name__ == "__main__":
    with TemporaryDirectory(prefix="photoframe-browser-") as directory:
        app = create_app(Path(directory), demo_mode=True)
        uvicorn.run(app, host="127.0.0.1", port=8765)
