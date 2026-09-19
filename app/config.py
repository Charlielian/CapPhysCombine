"""Path and runtime configuration."""

# 模块说明：
# 这里集中管理运行时路径和 Web 服务参数。配置文件优先级高于环境变量，
# 环境变量又高于代码默认值；这样既支持本地开发，也支持 Docker/生产环境注入配置。
# DATA_DIR、BASE_DIR 等路径由 paths.py 统一计算，避免各模块自行拼接路径造成不一致。

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

    优先级：config.yaml > 环境变量 CAPPHYS_PORT > 默认 4008。
    """
    config = _load_config()
    server = config.get("server") or {}
    port = server.get("port")
    if port is not None:
        return int(port)
    env_port = os.environ.get("CAPPHYS_PORT")
    if env_port:
        try:
            return int(env_port)
        except ValueError:
            pass
    return DEFAULT_PORT


def get_server_host() -> str:
    """获取绑定地址：127.0.0.1 仅本机访问，0.0.0.0 局域网访问。

    优先级：config.yaml > 环境变量 CAPPHYS_HOST > 默认 127.0.0.1。
    """
    config = _load_config()
    server = config.get("server") or {}
    host = server.get("host")
    if host is not None:
        return str(host)
    env_host = os.environ.get("CAPPHYS_HOST")
    if env_host:
        return env_host
    return DEFAULT_HOST


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
