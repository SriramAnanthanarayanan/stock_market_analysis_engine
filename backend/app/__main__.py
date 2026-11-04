"""``python -m app``: start the API on 127.0.0.1:8031 (PAS_PORT overrides)."""

import uvicorn

from .config import load_settings


def main() -> None:
    settings = load_settings()
    uvicorn.run("app.main:create_app", factory=True, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
