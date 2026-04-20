"""Entry point for the Personal Assistant PWA web server."""
import os

import uvicorn
from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    host = os.getenv("WEB_HOST", "0.0.0.0")
    port = int(os.getenv("WEB_PORT", "8001"))
    reload_flag = os.getenv("WEB_RELOAD", "0").strip().lower() in {"1", "true", "yes"}

    uvicorn.run(
        "web_app.app:app",
        host=host,
        port=port,
        reload=reload_flag,
        log_level="info",
    )


if __name__ == "__main__":
    main()
