import os

bind = "0.0.0.0:8000"
workers = 2
worker_class = "uvicorn.workers.UvicornWorker"
timeout = 30
graceful_timeout = 30
accesslog = "-"
errorlog = "-"
loglevel = (
    "debug" if os.getenv("DEBUG", "").lower() in {"1", "true", "yes", "on"} else "info"
)
