"""Path and runtime configuration."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.pipelines.common import (
    BASE_DIR,
    DATA_DIR,
    FILE_PATTERNS,
    LOG_DIR,
    LOWEFF_OUTPUT_PATH,
    PHYSICAL_FILE_PATTERNS,
    UNIFIED_DB_PATH,
)

CONFIG_PATH = BASE_DIR / "config.yaml"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4008
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

_config: dict[str, Any] | None = None


def _load_config() -> dict[str, Any]:
    global _config
    if _config is not None:
        return _config

    _config = {}

    if CONFIG_PATH.exists():
        try:
            import yaml
            with open(CONFIG_PATH, encoding="utf-8") as f:
                _config = yaml.safe_load(f) or {}
        except ImportError:
            pass
        except Exception:
            pass

    return _config


def get_server_port() -> int:
    """获取服务端口号，默认 4008。

    优先级：环境变量 CAPPHYS_PORT > config.yaml > 默认 4008。
    """
    env_port = os.environ.get("CAPPHYS_PORT")
    if env_port:
        try:
            return int(env_port)
        except ValueError:
            pass
    config = _load_config()
    return config.get("server", {}).get("port", DEFAULT_PORT)


def get_server_host() -> str:
    """获取绑定地址：127.0.0.1 仅本机访问，0.0.0.0 局域网访问。

    优先级：环境变量 CAPPHYS_HOST > config.yaml > 默认 127.0.0.1。
    """
    env_host = os.environ.get("CAPPHYS_HOST")
    if env_host:
        return env_host
    config = _load_config()
    return config.get("server", {}).get("host", DEFAULT_HOST)


def is_lan_accessible() -> bool:
    """判断是否允许局域网访问"""
    host = get_server_host()
    return host in ("0.0.0.0", "::")


__all__ = [
    "BASE_DIR",
    "CONFIG_PATH",
    "DATA_DIR",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "FILE_PATTERNS",
    "get_server_host",
    "get_server_port",
    "is_lan_accessible",
    "LOG_DIR",
    "LOWEFF_OUTPUT_PATH",
    "PHYSICAL_FILE_PATTERNS",
    "STATIC_DIR",
    "UNIFIED_DB_PATH",
]
