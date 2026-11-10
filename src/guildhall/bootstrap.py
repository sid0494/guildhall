"""The composition root: the one place that wires plug-ins into the platform.

The platform never imports plug-ins and plug-ins never import the platform;
entry points such as the worker call these functions to join the two.
"""

import argparse
import asyncio
import json
import signal
import sys
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

import structlog
import uvicorn
from fastapi import FastAPI

from guildhall.api.app import ApiSettings, create_app
from guildhall.contract import SourcePlugin
from guildhall.platform.archive import ArchiveSettings, open_archive
from guildhall.platform.command_jobs import command_routes
from guildhall.platform.command_schema import commands_of, discord_commands
from guildhall.platform.consumer import ConsumerSettings, JobConsumer
from guildhall.platform.database import DatabaseSettings, create_engine
from guildhall.platform.discord import (
    DiscordSettings,
    open_discord,
    register_commands,
)
from guildhall.platform.http import HttpSettings, open_upstream_http
from guildhall.platform.interactions import InteractionPruner, PostgresInteractionLog
from guildhall.platform.logs import LogSettings, configure_logging
from guildhall.platform.queue import QueueSettings, open_job_queue
from guildhall.platform.registry import build_registry
from guildhall.platform.relay import OutboxRelay, RelaySettings
from guildhall.platform.runner import JobRunner, RunnerSettings
from guildhall.platform.scheduler import Scheduler
from guildhall.platform.settings import Settings
from guildhall.platform.worker import Worker, WorkerSettings
from guildhall.plugins import CATALOGUE

_log = structlog.get_logger(__name__)


def load_plugins(settings: Settings) -> Mapping[str, SourcePlugin]:
    """The enabled plug-ins, checked, keyed by name."""
    return build_registry(CATALOGUE, settings.plugins)


async def run_worker(stop: asyncio.Event) -> None:
    """Build a worker from the settings and run it until `stop` is set."""
    plugins = load_plugins(Settings())
    consumer_settings = ConsumerSettings()
    engine = create_engine(DatabaseSettings())
    try:
        async with (
            open_job_queue(QueueSettings()) as queue,
            open_archive(ArchiveSettings()) as archive,
            open_upstream_http(HttpSettings()) as http,
        ):
            runner = JobRunner(plugins, engine, queue, archive, http, RunnerSettings())
            worker = Worker(
                JobConsumer(queue, runner, consumer_settings),
                OutboxRelay(engine, queue, RelaySettings()),
                Scheduler(plugins, engine),
                WorkerSettings(),
                pruners=(InteractionPruner(engine),),
            )
            _log.info(
                "worker started",
                plugins=sorted(plugins),
                concurrency=consumer_settings.worker_concurrency,
            )
            await worker.run(stop)
    finally:
        await engine.dispose()
    _log.info("worker stopped")


async def _run_worker_until_signalled() -> None:
    """SIGTERM or Ctrl+C asks for a graceful stop; a second signal forces it."""
    stop = asyncio.Event()
    main = asyncio.current_task()
    if main is None:
        raise RuntimeError("must run inside an asyncio task")

    def on_signal(name: str) -> None:
        if stop.is_set():
            _log.warning("second signal, stopping now", signal=name)
            main.cancel()
        else:
            _log.info("stopping", signal=name)
            stop.set()

    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, on_signal, signum.name)
    await run_worker(stop)


def worker_main() -> None:
    """The `guildhall-worker` command."""
    configure_logging(LogSettings())
    try:
        asyncio.run(_run_worker_until_signalled())
    except asyncio.CancelledError:
        _log.warning("worker forced to stop")
        sys.exit(1)
    except Exception:
        _log.exception("worker failed")
        sys.exit(1)


def api_main() -> None:
    """The `guildhall-api` command: serve Discord's interactions endpoint.

    Uvicorn's own logging setup is off, so its few lines go through ours as
    JSON, and it writes no access log: the app logs what matters itself.
    """
    configure_logging(LogSettings())
    settings = ApiSettings()
    routes = command_routes(load_plugins(Settings()))
    engine = create_engine(DatabaseSettings())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await engine.dispose()

    uvicorn.run(
        create_app(settings, PostgresInteractionLog(engine), routes, lifespan),
        host=settings.api_host,
        port=settings.api_port,
        log_config=None,
        access_log=False,
    )


def register_commands_main() -> None:
    """The `guildhall-register-commands` command (docs/adr/0065).

    Makes the test server's slash commands match the enabled plug-ins'. With
    `--dry-run`, prints what would be sent and calls nothing.
    """
    parser = argparse.ArgumentParser(description=register_commands_main.__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="print the commands; call nothing"
    )
    arguments = parser.parse_args()
    configure_logging(LogSettings())
    commands = discord_commands(commands_of(load_plugins(Settings())))
    if arguments.dry_run:
        print(json.dumps(commands, indent=2))
        return
    settings = DiscordSettings()

    async def register() -> int:
        async with open_discord(settings) as client:
            return await register_commands(client, settings, commands)

    count = asyncio.run(register())
    _log.info("commands registered", count=count)
