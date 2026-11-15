import os
import secrets
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field

from codex_agy.models import Input, Problem


def default_data() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    return Path(local) / "CodexAgySupervisor" if local else Path.home() / ".codex-agy"


class Settings(Input):
    data_dir: Path = Field(default_factory=default_data)
    port: int = Field(18765, ge=1024, le=65535)
    agy_executable: str = "agy"
    agy_model: str | None = None
    sandbox: bool = True
    permission_mode: Literal["manual", "worktree", "allow_all"] = "worktree"
    workers: int = Field(2, ge=1, le=8)
    max_review_rounds: int = Field(3, ge=1, le=10)
    startup_seconds: int = Field(35, ge=2, le=180)
    idle_seconds: int = Field(300, ge=2)
    hard_seconds: int = Field(1800, ge=3)
    permission_seconds: int = Field(120, ge=1, le=600)
    verification_seconds: int = Field(240, ge=1, le=1800)
    max_wait_seconds: int = Field(45, ge=1, le=50)
    allowed_roots: list[Path] = Field(default_factory=list)

    @property
    def token_path(self) -> Path:
        return self.data_dir / "ipc.token"

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def token(self) -> str:
        try:
            token = self.token_path.read_text(encoding="ascii").strip()
        except FileNotFoundError as exc:
            raise Problem("NOT_INITIALIZED", "Run codex-agy init first.") from exc
        if len(token) != 64:
            raise Problem("INVALID_TOKEN", "IPC token file is invalid.")
        return token

    def init(self) -> None:
        self.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            fd = os.open(self.token_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            self.token()
            restrict_token(self.token_path)
        else:
            try:
                with os.fdopen(fd, "w", encoding="ascii") as handle:
                    restrict_token(self.token_path)
                    handle.write(secrets.token_hex(32) + "\n")
            except Exception:
                self.token_path.unlink(missing_ok=True)
                raise


def restrict_token(path: Path) -> None:
    if os.name != "nt":
        path.chmod(0o600)
        return

    import win32api
    import win32con
    import win32security

    process = win32api.GetCurrentProcess()
    access_token = win32security.OpenProcessToken(process, win32con.TOKEN_QUERY)
    try:
        owner, _ = win32security.GetTokenInformation(access_token, win32security.TokenUser)
    finally:
        access_token.Close()
    system = win32security.CreateWellKnownSid(win32security.WinLocalSystemSid, None)
    acl = win32security.ACL()
    for sid in (owner, system):
        acl.AddAccessAllowedAce(win32security.ACL_REVISION, win32con.GENERIC_ALL, sid)
    win32security.SetNamedSecurityInfo(
        str(path),
        win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        None,
        None,
        acl,
        None,
    )


def load_settings(config: str | Path | None = None) -> Settings:
    override = config or os.environ.get("CODEX_AGY_CONFIG")
    path = Path(override).expanduser() if override else default_data() / "config.yaml"
    if override or path.exists():
        settings = Settings.model_validate(
            yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
        )
    else:
        settings = Settings()
    settings.data_dir = settings.data_dir.expanduser().resolve()
    settings.allowed_roots = [root.expanduser().resolve() for root in settings.allowed_roots]
    return settings
