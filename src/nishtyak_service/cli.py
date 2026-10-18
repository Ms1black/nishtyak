from __future__ import annotations

import argparse
import signal
from dataclasses import replace

from .config import Settings
from .dispatcher import Dispatcher
from .execution import DockerExecutor, SubprocessExecutor
from .storage import SQLiteJobRepository


def main() -> None:
    parser = argparse.ArgumentParser(description="Internal reproducibility service")
    parser.add_argument("command", choices=("serve", "dispatch", "dev"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8010)
    args = parser.parse_args()
    try:
        settings = Settings.from_env()
    except ValueError as error:
        parser.error(str(error))
    if args.command in {"serve", "dev"} and not settings.api_token:
        parser.error("NISHTYAK_SERVICE_TOKEN is required for the HTTP service")
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535")
    if args.command == "dev":
        settings = replace(settings, runner="subprocess")
    repository = SQLiteJobRepository(settings)
    executor = (
        DockerExecutor(policy=settings.job_policy, image=settings.analyzer_image)
        if settings.runner == "docker"
        else SubprocessExecutor(policy=settings.job_policy)
    )
    dispatcher = Dispatcher(repository, executor)
    if args.command == "dispatch":

        def shutdown(signum, frame) -> None:
            dispatcher.stop(timeout=10)

        signal.signal(signal.SIGINT, shutdown)
        signal.signal(signal.SIGTERM, shutdown)
        dispatcher.run_forever()
        return

    import uvicorn

    from .api import create_app
    from .service import LogicService

    if args.command == "dev":
        dispatcher.start()
    try:
        uvicorn.run(
            create_app(settings, LogicService(settings, repository)),
            host=args.host,
            port=args.port,
            workers=1,
            access_log=False,
            server_header=False,
            limit_concurrency=settings.max_http_requests + 2,
            timeout_keep_alive=5,
            timeout_graceful_shutdown=30,
        )
    finally:
        if args.command == "dev":
            dispatcher.stop(timeout=10)


if __name__ == "__main__":
    main()
