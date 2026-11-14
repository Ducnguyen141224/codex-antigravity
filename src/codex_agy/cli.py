import argparse
import asyncio
import json
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from aiohttp import web
from pydantic import ValidationError

from codex_agy import acp_client
from codex_agy.api import TOOLS
from codex_agy.config import load_settings
from codex_agy.ipc import application, call, failure, success
from codex_agy.models import Problem
from codex_agy.storage import Database
from codex_agy.supervisor import Supervisor, instance_lock


async def serve(settings):
    settings.init()
    stop = asyncio.Event()
    with instance_lock(settings.data_dir / "supervisor.lock"):
        db = Database(settings.data_dir / "supervisor.sqlite3")
        supervisor = Supervisor(settings, db)
        runner = web.AppRunner(
            application(settings, supervisor.dispatch, stop),
            access_log=None,
            shutdown_timeout=4,
        )
        try:
            await runner.setup()
            await web.TCPSite(runner, "127.0.0.1", settings.port).start()
            supervisor.start()
            logging.info("Listening on %s", settings.endpoint)
            await stop.wait()
        finally:
            if supervisor.worker is not None:
                await supervisor.close()
            await runner.cleanup()
            db.close()


def main():
    parser = argparse.ArgumentParser(prog="codex-agy")
    parser.add_argument("--config", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "serve", "mcp", "health", "stop"):
        sub.add_parser(name)
    probe = sub.add_parser("probe")
    probe.add_argument("--session", action="store_true")
    tool = sub.add_parser("call")
    tool.add_argument("tool", choices=TOOLS)
    tool.add_argument("--json-file", type=Path)
    schema = sub.add_parser("schema")
    schema.add_argument("tool", choices=TOOLS)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.command == "schema":
        print(json.dumps(TOOLS[args.tool][0].model_json_schema(), indent=2))
        return
    try:
        settings = load_settings(args.config)
        if args.command == "init":
            settings.init()
            with instance_lock(settings.data_dir / "supervisor.lock"):
                Database(settings.data_dir / "supervisor.sqlite3").close()
            result = success({"data_dir": str(settings.data_dir)})
        elif args.command == "serve":
            settings.data_dir.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                settings.data_dir / "supervisor.log",
                maxBytes=2_000_000,
                backupCount=3,
                encoding="utf-8",
            )
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logging.getLogger().addHandler(handler)
            asyncio.run(serve(settings))
            return
        elif args.command == "mcp":
            from codex_agy.mcp_server import run

            asyncio.run(run(settings))
            return
        elif args.command == "probe":
            import tempfile

            with tempfile.TemporaryDirectory(
                prefix="Codex AGY probe ", ignore_cleanup_errors=True
            ) as folder:
                result = success(
                    asyncio.run(acp_client.probe(settings, Path(folder), args.session))
                )
        elif args.command == "call":
            payload = (
                json.loads(args.json_file.read_text(encoding="utf-8-sig")) if args.json_file else {}
            )
            result = asyncio.run(call(settings, args.tool, payload))
        else:
            method = "health_check" if args.command == "health" else "shutdown"
            result = asyncio.run(call(settings, method))
    except KeyboardInterrupt:
        return
    except Problem as exc:
        if args.command == "serve":
            logging.error("%s: %s", exc.code, exc)
        result = failure(exc)
    except (ValidationError, ValueError, OSError, TimeoutError) as exc:
        if args.command == "serve":
            logging.exception("Supervisor startup failed")
        result = failure(Problem("LOCAL_ERROR", str(exc) or type(exc).__name__))
    if sys.stdout is not None:
        print(json.dumps(result, ensure_ascii=True, indent=2))
    if not result["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
