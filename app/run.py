import os

import uvicorn

DEFAULT_PORT = 8000
DEFAULT_LOG_LEVEL = "info"


def main() -> None:
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=int(os.getenv("PORT", str(DEFAULT_PORT))),
        log_level=os.getenv("LOG_LEVEL", DEFAULT_LOG_LEVEL),
        reload=True,
    )


if __name__ == "__main__":
    main()
