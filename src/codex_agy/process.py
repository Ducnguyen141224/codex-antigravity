import asyncio
import contextlib
import os
import signal
import subprocess
from pathlib import Path


class ProcessGroup:
    """Kill remaining children when the execution scope exits."""

    def __init__(self):
        self.job = None
        if os.name == "nt":
            import win32job

            self.job = win32job.CreateJobObject(None, "")
            limits = win32job.QueryInformationJobObject(
                self.job, win32job.JobObjectExtendedLimitInformation
            )
            limits["BasicLimitInformation"]["LimitFlags"] |= (
                win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            win32job.SetInformationJobObject(
                self.job, win32job.JobObjectExtendedLimitInformation, limits
            )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.close()

    def close(self):
        if self.job is not None:
            self.job.Close()
            self.job = None

    async def spawn(self, *args: str, **kwargs):
        if self.job is None:
            return await asyncio.create_subprocess_exec(*args, start_new_session=True, **kwargs)

        import win32api
        import win32con
        import win32job

        process = await asyncio.create_subprocess_exec(
            *args, creationflags=subprocess.CREATE_NO_WINDOW, **kwargs
        )
        try:
            handle = win32api.OpenProcess(
                win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, process.pid
            )
            try:
                win32job.AssignProcessToJobObject(self.job, handle)
            finally:
                handle.Close()
        except Exception:
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.communicate()
                raise
        return process


async def terminate(process) -> None:
    if os.name != "nt":
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    elif process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
    await process.wait()


def child_env() -> dict[str, str]:
    allowed = {
        "path",
        "systemroot",
        "windir",
        "comspec",
        "pathext",
        "temp",
        "tmp",
        "home",
        "userprofile",
        "homedrive",
        "homepath",
        "appdata",
        "localappdata",
        "programdata",
        "programfiles",
        "programfiles(x86)",
        "number_of_processors",
        "lang",
        "lc_all",
        "https_proxy",
        "http_proxy",
        "no_proxy",
        "ssl_cert_file",
        "ssl_cert_dir",
        "java_home",
        "maven_home",
        "gradle_home",
        "xdg_config_home",
        "xdg_cache_home",
    }
    return {
        **{key: value for key, value in os.environ.items() if key.casefold() in allowed},
        "PYTHONDONTWRITEBYTECODE": "1",
    }


async def capture(
    argv: list[str],
    cwd: Path | None = None,
    timeout: float = 25,
    data: bytes | None = None,
    env: dict | None = None,
):
    async with ProcessGroup() as group:
        process = await group.spawn(
            *argv,
            cwd=cwd,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(timeout):
                out, err = await process.communicate(data)
                return process.returncode, out, err
        except BaseException:
            group.close()
            await terminate(process)
            raise
