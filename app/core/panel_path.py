"""Helpers for normalizing and working with the admin panel path."""

from __future__ import annotations

import re
import secrets
import string


_SAFE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_RESERVED_ROOT_SEGMENTS = {
    "api",
    "cabinet",
    "docs",
    "health",
    "openapi.json",
    "redoc",
    "static",
    "webhook",
    "ws",
}


def normalize_panel_path(value: str | None, *, default: str = "/panel/") -> str:
    raw = (value or "").strip()
    if not raw:
        raw = default

    parts = [part for part in raw.split("/") if part]
    if not parts:
        parts = [part for part in default.split("/") if part]

    if parts and parts[0].lower() in _RESERVED_ROOT_SEGMENTS:
        raise ValueError(
            f"Admin path cannot start with reserved segment '{parts[0]}'"
        )

    for part in parts:
        if not _SAFE_SEGMENT_RE.fullmatch(part):
            raise ValueError(
                "Admin path may contain only letters, numbers, '_' and '-'"
            )

    return "/" + "/".join(parts) + "/"


def panel_prefix(panel_root: str) -> str:
    normalized = normalize_panel_path(panel_root)
    return normalized[:-1]


def panel_path(panel_root: str, suffix: str = "") -> str:
    normalized = normalize_panel_path(panel_root)
    if not suffix:
        return normalized

    cleaned = suffix.strip()
    if not cleaned or cleaned == "/":
        return normalized
    return normalized + cleaned.lstrip("/")


def is_panel_path(path: str, panel_root: str) -> bool:
    prefix = panel_prefix(panel_root)
    return path == prefix or path.startswith(f"{prefix}/")


def generate_panel_path(prefix_length: int = 3) -> str:
    length = max(1, min(prefix_length, 4))
    alphabet = string.ascii_lowercase + string.digits
    prefix = "".join(secrets.choice(alphabet) for _ in range(length))
    return f"/{prefix}/panel/"
