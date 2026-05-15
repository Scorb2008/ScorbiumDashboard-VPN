import os
import socket
from pathlib import Path


def _read_env_db_host() -> str:
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.exists():
        return ""

    try:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == "DB_HOST":
                return value.strip().strip("\"'")
    except OSError:
        return ""
    return ""


def bootstrap_cli_environment() -> None:
    """Adjust env vars so local CLI works both inside and outside Docker."""
    db_host = (os.environ.get("DB_HOST") or _read_env_db_host()).strip()
    if db_host != "db":
        return

    if os.path.exists("/.dockerenv"):
        return

    try:
        socket.getaddrinfo("db", 5432, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
        return
    except socket.gaierror:
        os.environ["DB_HOST"] = "127.0.0.1"
