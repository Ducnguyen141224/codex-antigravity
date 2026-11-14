import asyncio
import hmac
import logging
from collections.abc import Awaitable, Callable

from aiohttp import ClientError, ClientSession, ClientTimeout, web
from pydantic import ValidationError

from codex_agy.api import TOOLS
from codex_agy.config import Settings
from codex_agy.models import Problem

log = logging.getLogger(__name__)


def success(data):
    return {"ok": True, "data": data}


def failure(problem: Problem):
    return {"ok": False, "error": problem.payload()}


async def call(settings: Settings, method: str, args: dict | None = None) -> dict:
    try:
        token = settings.token()
        timeout = max(settings.startup_seconds + 15, settings.max_wait_seconds + 5)
        async with ClientSession(timeout=ClientTimeout(total=timeout), trust_env=False) as session:
            async with session.post(
                f"{settings.endpoint}/{method}",
                json=args or {},
                headers={"Authorization": f"Bearer {token}"},
                allow_redirects=False,
            ) as response:
                return await response.json()
    except Problem as exc:
        return failure(exc)
    except (ClientError, TimeoutError, OSError, ValueError):
        return failure(
            Problem("IPC_UNAVAILABLE", "Cannot reach the local supervisor. Check its logs.")
        )


def application(
    settings: Settings, dispatch: Callable[[str, object], Awaitable], stop: asyncio.Event
) -> web.Application:
    token = f"Bearer {settings.token()}"

    async def endpoint(request):
        if not hmac.compare_digest(
            request.headers.get("Authorization", "").encode(), token.encode()
        ):
            return web.json_response(
                failure(Problem("UNAUTHORIZED", "Invalid IPC credentials.")), status=401
            )
        method = request.match_info["method"]
        try:
            if method == "shutdown":
                asyncio.get_running_loop().call_later(0.15, stop.set)
                return web.json_response(success({"status": "STOPPING"}))
            if method not in TOOLS:
                raise Problem("UNKNOWN_TOOL", "Unknown supervisor operation.")
            model, _ = TOOLS[method]
            try:
                data = model.model_validate(await request.json())
            except ValidationError as exc:
                raise Problem(
                    "VALIDATION",
                    "Invalid tool input.",
                    exc.errors(include_input=False, include_context=False, include_url=False),
                ) from exc
            except (ValueError, UnicodeError) as exc:
                raise Problem("VALIDATION", "Request body must be JSON.") from exc
            return web.json_response(success(await dispatch(method, data)))
        except web.HTTPRequestEntityTooLarge:
            return web.json_response(
                failure(Problem("PAYLOAD_TOO_LARGE", "Tool input exceeds the 512 KiB limit.")),
                status=413,
            )
        except Problem as exc:
            return web.json_response(failure(exc))
        except Exception:
            log.exception("Operation failed: %s", method)
            return web.json_response(
                failure(Problem("INTERNAL", "See the supervisor log.")), status=500
            )

    app = web.Application(client_max_size=512 * 1024)
    app.router.add_post("/v1/{method}", endpoint)
    return app
