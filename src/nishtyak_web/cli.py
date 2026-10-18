from __future__ import annotations

import argparse
import ipaddress
import os
from pathlib import Path

from .config import WebSettings


def main() -> None:
    parser = argparse.ArgumentParser(description="Browser gateway for the internal logic service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8020)
    args = parser.parse_args()
    try:
        settings = WebSettings.from_env()
        if settings.mode == "local" and not ipaddress.ip_address(args.host).is_loopback:
            parser.error("Local integration mode must bind a loopback IP address")
        if not 1 <= args.port <= 65535:
            parser.error("Port must be between 1 and 65535")
    except ValueError as error:
        parser.error(str(error))
    import uvicorn

    from .api import create_app

    static_dir = os.environ.get("NISHTYAK_WEB_STATIC_DIR")
    uvicorn.run(
        create_app(settings, static_dir=Path(static_dir) if static_dir else None),
        host=args.host,
        port=args.port,
        workers=1,
        access_log=False,
        server_header=False,
        proxy_headers=False,
        timeout_keep_alive=5,
        timeout_graceful_shutdown=30,
        limit_concurrency=10,
    )


if __name__ == "__main__":
    main()
