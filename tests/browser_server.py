"""Local-only deterministic application for browser integration checks."""

import os
from tempfile import TemporaryDirectory

import uvicorn

if __name__ == "__main__":
    with TemporaryDirectory(prefix="photoframe-browser-") as directory:
        os.environ["PHOTOFRAME_DATA_DIR"] = directory
        os.environ["PHOTOFRAME_DEMO_MODE"] = "1"
        uvicorn.run("photoframe.web.routes:app", host="127.0.0.1", port=8765)
