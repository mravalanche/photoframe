import os

import uvicorn


def server_bind() -> tuple[str, int]:
    """Return the validated network bind configured for this installation."""
    default_host = "127.0.0.1"
    host = os.getenv("PHOTOFRAME_HOST", default_host).strip() or default_host
    raw_port = os.getenv("PHOTOFRAME_PORT", "8000").strip()
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ValueError("PHOTOFRAME_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ValueError("PHOTOFRAME_PORT must be between 1 and 65535")
    return host, port


def main() -> None:
    host, port = server_bind()
    uvicorn.run("photoframe.web:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
