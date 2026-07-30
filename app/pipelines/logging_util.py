"""日志工具：loguru 结构化日志 + 文件轮转 + 控制台 + GUI 回调。

GuiLogger / GuiProgress 提供 pipeline 模块统一的日志与进度接口，
setup_logging 负责配置 loguru（按天轮转、保留 7 天）并接管标准库 logging。

设计原则：
- loguru 为唯一后端，标准库 logging 通过 InterceptHandler 转发到 loguru。
- GuiLogger 接口保持向后兼容（callback + log 方法），不改 pipeline 调用方。
- 未安装 loguru 时自动退化为原标准库实现，保证环境兼容性。
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from app.pipelines.paths import LOG_DIR, LOG_RETENTION_DAYS

ProgressCallback = Callable[[int, str], None]
LogCallback = Callable[[str], None]

try:
    from loguru import logger as _loguru_logger

    _HAS_LOGURU = True
except ImportError:  # pragma: no cover
    _loguru_logger = None  # type: ignore
    _HAS_LOGURU = False


class SourceFileError(RuntimeError):
    """源文件缺失或格式不符。"""


# ==============================================================================
# loguru 后端
# ==============================================================================


class InterceptHandler(logging.Handler):
    """把标准库 logging 的记录转发给 loguru。"""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = _loguru_logger.level(record.levelname).name
        except (ValueError, AttributeError):
            level = record.levelno

        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        _loguru_logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def _configure_loguru() -> None:
    """配置 loguru sink：按天轮转、保留 N 天、同时输出到 stderr。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    cleanup_old_logs()

    # 清空默认 sink
    _loguru_logger.remove()

    log_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>"
    )

    today = datetime.now().strftime("%Y%m%d")
    log_file = LOG_DIR / f"app_{today}.log"

    # 文件 sink：DEBUG 级别，按天轮转
    _loguru_logger.add(
        str(log_file),
        level="DEBUG",
        format=log_format,
        rotation="00:00",  # 每天 0 点轮转
        retention=f"{LOG_RETENTION_DAYS} days",
        encoding="utf-8",
        enqueue=True,  # 多进程/多线程安全
        backtrace=True,
        diagnose=False,
    )

    # 控制台 sink：INFO 级别
    _loguru_logger.add(
        sys.stderr,
        level="INFO",
        format=log_format,
        enqueue=True,
    )

    # 接管标准库 logging
    logging.basicConfig(
        handlers=[InterceptHandler()],
        level=logging.DEBUG,
        force=True,
    )

    # 常见第三方库降噪
    for noisy in ("urllib3", "matplotlib", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _configure_stdlib() -> logging.Logger:
    """loguru 不可用时的标准库降级实现。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    cleanup_old_logs()

    logger = logging.getLogger("CapPhysCombine")
    logger.setLevel(logging.DEBUG)
    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    today = datetime.now().strftime("%Y%m%d")
    log_file = LOG_DIR / f"app_{today}.log"
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger


def setup_logging() -> Any:
    """配置日志系统，每天一个日志文件，自动清理过期日志。

    返回 loguru logger 或标准库 logger（loguru 不可用时）。
    """
    if _HAS_LOGURU:
        _configure_loguru()
        return _loguru_logger
    return _configure_stdlib()


def cleanup_old_logs() -> None:
    """删除超过保留期的日志文件（仅对非 loguru 轮转的遗留文件生效）。"""
    if not LOG_DIR.exists():
        return

    cutoff = datetime.now() - timedelta(days=LOG_RETENTION_DAYS)
    deleted = 0
    for log_file in LOG_DIR.glob("app_*.log"):
        try:
            if datetime.fromtimestamp(log_file.stat().st_mtime) < cutoff:
                log_file.unlink()
                deleted += 1
        except OSError:
            continue
    if deleted > 0:
        # 不再依赖 logger 回写，避免循环初始化
        print(f"[cleanup_old_logs] 已清理 {deleted} 个过期日志文件", file=sys.stderr)


def get_logger() -> Any:
    """获取已配置的 logger（loguru 或标准库）。"""
    if _HAS_LOGURU:
        # loguru 全局单例，无需 handlers 检查；首次调用时自动配置
        if not _loguru_logger._core.handlers:
            setup_logging()
        return _loguru_logger
    logger = logging.getLogger("CapPhysCombine")
    if not logger.handlers:
        return setup_logging()
    return logger


# ==============================================================================
# GuiLogger / GuiProgress（接口保持向后兼容）
# ==============================================================================


class GuiLogger:
    """Pipeline 日志门面：同时写入 loguru/stdlib 日志与可选的 GUI 回调。

    向后兼容：构造方式与 ``GuiLogger()`` / ``GuiLogger(callback=fn)`` 一致。
    新增：``GuiLogger(component="物理表")`` 可附加结构化上下文 bind。
    """

    def __init__(
        self,
        callback: LogCallback | None = None,
        component: str | None = None,
    ) -> None:
        self.callback = callback
        self.component = component
        self._logger = get_logger()

    def _bind(self):
        """附加 component 上下文（loguru 专用）。"""
        if _HAS_LOGURU and self.component:
            return self._logger.bind(component=self.component)
        return self._logger

    def log(self, message: str) -> None:
        """INFO 级别日志 + GUI 回调。"""
        bound = self._bind()
        bound.info(message)
        if self.callback:
            self.callback(message)

    def debug(self, message: str) -> None:
        self._bind().debug(message)
        if self.callback:
            self.callback(message)

    def warning(self, message: str) -> None:
        self._bind().warning(message)
        if self.callback:
            self.callback(message)

    def error(self, message: str) -> None:
        self._bind().error(message)
        if self.callback:
            self.callback(message)

    def exception(self, message: str) -> None:
        """记录异常（含 traceback）。"""
        if _HAS_LOGURU:
            self._bind().exception(message)
        else:
            self._bind().exception(message)
        if self.callback:
            self.callback(message)


class GuiProgress:
    """进度回调门面：把百分比与消息推给 GUI，并写一条日志。"""

    def __init__(
        self,
        callback: ProgressCallback | None = None,
        logger: GuiLogger | None = None,
    ) -> None:
        self.callback = callback
        self.logger = logger or GuiLogger()

    def update(self, value: int, message: str) -> None:
        value = max(0, min(100, value))
        if self.callback:
            self.callback(value, message)
        self.logger.log(message)


__all__ = [
    "ProgressCallback",
    "LogCallback",
    "SourceFileError",
    "setup_logging",
    "cleanup_old_logs",
    "get_logger",
    "GuiLogger",
    "GuiProgress",
    "InterceptHandler",
]
