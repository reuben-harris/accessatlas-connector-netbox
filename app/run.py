import os

import uvicorn

from app.config import get_settings

DEFAULT_PORT = 8000


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=int(os.getenv("PORT", str(DEFAULT_PORT))),
        log_level="debug" if settings.debug else "info",
        reload=True,
    )


if __name__ == "__main__":
    main()
