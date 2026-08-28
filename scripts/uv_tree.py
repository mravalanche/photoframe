"""Run ``uv tree`` when UV is installed locally or available on PATH."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    bundled_uv = project_root / ".tools" / "uv" / ("uv.exe" if sys.platform == "win32" else "uv")
    uv = shutil.which("uv") or (str(bundled_uv) if bundled_uv.is_file() else None)
    if uv is None:
        print("UV is required: install it or place it in .tools/uv/", file=sys.stderr)
        return 1
    return subprocess.run(
        [uv, "tree", "--all-groups"], cwd=project_root, check=False
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
