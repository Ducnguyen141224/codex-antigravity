import asyncio
import json
import os
import shutil
import sys
import time
import tomllib
from pathlib import Path

from codex_agy.planning import VerificationCommand
from codex_agy.process import ProcessGroup, child_env, terminate

MAX_LOG = 512 * 1024


def discover_commands(root: Path) -> list[VerificationCommand]:
    result = []
    manifest = root / "package.json"
    if manifest.is_file():
        try:
            package = json.loads(manifest.read_text(encoding="utf-8-sig"))
            scripts = package.get("scripts", {}) if isinstance(package, dict) else {}
            if not isinstance(scripts, dict):
                scripts = {}
        except (ValueError, OSError):
            scripts = {}
        result.extend(
            VerificationCommand(argv=["npm", "run", script])
            for script in ("lint", "typecheck", "type-check", "test", "build")
            if script in scripts
        )
        if not {"typecheck", "type-check"} & scripts.keys() and (root / "tsconfig.json").is_file():
            result.append(
                VerificationCommand(argv=["npm", "exec", "--no", "--", "tsc", "--noEmit"])
            )
    pyproject = root / "pyproject.toml"
    python_config = {}
    if pyproject.is_file():
        try:
            with pyproject.open("rb") as handle:
                python_config = tomllib.load(handle).get("tool", {})
        except (ValueError, OSError):
            pass
    if python_config or (root / "pytest.ini").is_file() or pyproject.exists():
        if (
            "pytest" in python_config
            or (root / "tests").is_dir()
            or (root / "pytest.ini").is_file()
        ):
            result.append(VerificationCommand(argv=["python", "-m", "pytest", "-q"]))
        if (
            "ruff" in python_config
            or (root / "ruff.toml").is_file()
            or (root / ".ruff.toml").is_file()
        ):
            result.append(VerificationCommand(argv=["python", "-m", "ruff", "check", "."]))
        if "mypy" in python_config or (root / "mypy.ini").is_file():
            result.append(VerificationCommand(argv=["python", "-m", "mypy", "."]))
    windows = os.name == "nt"
    if (root / "pom.xml").is_file():
        wrapper = "mvnw.cmd" if windows else "mvnw"
        result.append(
            VerificationCommand(argv=[wrapper if (root / wrapper).exists() else "mvn", "test"])
        )
    if any((root / name).is_file() for name in ("build.gradle", "build.gradle.kts")):
        wrapper = "gradlew.bat" if windows else "gradlew"
        result.append(
            VerificationCommand(argv=[wrapper if (root / wrapper).exists() else "gradle", "test"])
        )
    return result


def command_line(args: list[str], cwd: Path) -> list[str] | None:
    program = Path(args[0])
    if args[0] in {"python", "python3"}:
        env_python = cwd / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        executable = (
            str(env_python) if env_python.is_file() else shutil.which(args[0]) or sys.executable
        )
    elif program.name in {"mvnw", "mvnw.cmd", "gradlew", "gradlew.bat"}:
        executable = str(cwd / program.name) if (cwd / program.name).is_file() else None
    elif program.is_absolute() or len(program.parts) > 1:
        executable = shutil.which(str((cwd / program).resolve()))
    else:
        executable = shutil.which(args[0])
    if not executable:
        return None
    if os.name == "nt" and Path(executable).suffix.lower() in {".cmd", ".bat"}:
        safe = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-/=:@+"
        if any(not arg or any(ch not in safe for ch in arg) for arg in args[1:]):
            raise ValueError(
                "Batch wrapper arguments must use plain tokens. Use an executable for other arguments."
            )
        if any(ch in executable for ch in '%!&|^<>"\r\n'):
            raise ValueError(
                "Batch wrapper path contains unsupported command processor characters."
            )
    return [executable, *args[1:]]


async def verify(commands, root: Path, logs: Path, revision: str, timeout: int):
    if not commands:
        return [
            {
                "result": "SKIPPED",
                "reason": "No applicable verification commands.",
                "revision": revision,
            }
        ]
    logs.mkdir(parents=True, exist_ok=True)
    results = []
    seen = set()
    for command in commands:
        key = (tuple(command.argv), command.cwd)
        if key in seen:
            continue
        seen.add(key)
        cwd = (root / command.cwd).resolve()
        item = {
            "command": command.argv,
            "cwd": command.cwd,
            "revision": revision,
            "exit_code": None,
            "log_path": None,
            "duration_seconds": 0,
        }
        start = time.monotonic()
        try:
            if not cwd.is_relative_to(root.resolve()) or not cwd.is_dir():
                raise ValueError("Verification cwd must be a directory inside the worktree.")
            argv = command_line(command.argv, cwd)
            if argv is None:
                item["result"] = "NOT_AVAILABLE"
            else:
                log_path = logs / f"verification-{len(results) + 1}.log"
                item["log_path"] = str(log_path)
                item["exit_code"] = await run(
                    argv, cwd, log_path, min(command.timeout_seconds or timeout, timeout)
                )
                item["result"] = "PASSED" if item["exit_code"] == 0 else "FAILED"
                if item["exit_code"] and any(
                    marker in log_path.read_bytes()
                    for marker in (b"No module named ", b"command not found")
                ):
                    item["result"] = "NOT_AVAILABLE"
        except (OSError, ValueError, TimeoutError) as exc:
            item["result"] = "ERROR"
            item["reason"] = str(exc) or type(exc).__name__
        item["duration_seconds"] = round(time.monotonic() - start, 3)
        results.append(item)
    return results


async def run(argv, cwd, log_path, timeout):
    async with ProcessGroup() as group:
        process = await group.spawn(
            *argv,
            cwd=cwd,
            env=child_env(),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            async with asyncio.timeout(timeout):
                size = 0
                with log_path.open("wb") as handle:
                    while chunk := await process.stdout.read(8192):
                        if size < MAX_LOG:
                            handle.write(chunk[: MAX_LOG - size])
                        size += len(chunk)
                    if size > MAX_LOG:
                        handle.write(b"\n[log truncated]\n")
                return await process.wait()
        except BaseException:
            group.close()
            await terminate(process)
            raise
