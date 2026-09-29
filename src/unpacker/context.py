"""解包上下文：输出目录、密码表、进度/日志回调、取消信号。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .models import ConflictPolicy


class CancelRequested(Exception):
    """用户取消任务时抛出。"""


def _noop_progress(done: int, total: int, name: str) -> None:  # pragma: no cover
    return None


def _noop_log(level: str, message: str) -> None:  # pragma: no cover
    return None


def _never_cancel() -> bool:  # pragma: no cover
    return False


@dataclass
class ExtractContext:
    """贯穿单次解包任务的可变上下文。"""

    output_dir: Path
    passwords: list[str] = field(default_factory=list)
    conflict: ConflictPolicy = ConflictPolicy.RENAME
    recursive: bool = False
    max_depth: int = 3
    carve_min_size: int = 1024
    keep_structure: bool = True
    #: 成员名编码：``"auto"`` 自动回退（utf-8 → gbk → big5 → shift_jis），
    #: 也可指定 ``"gbk"`` / ``"utf-8"`` 等强制使用
    encoding: str = "auto"
    #: 源归档自身的时间戳，供 NEWER 冲突策略在成员无时间信息时兜底
    source_mtime: Optional[float] = None

    # ---- 运行统计（由落盘助手累加，供界面与报告展示）----
    bytes_written: int = 0
    skipped_files: int = 0

    progress: Callable[[int, int, str], None] = _noop_progress
    log: Callable[[str, str], None] = _noop_log
    is_cancelled: Callable[[], bool] = _never_cancel

    @property
    def overwrite(self) -> bool:
        """兼容旧写法：``ctx.overwrite`` 等价于「冲突策略 = 覆盖」。"""
        return self.conflict is ConflictPolicy.OVERWRITE

    def check_cancel(self) -> None:
        if self.is_cancelled():
            raise CancelRequested()

    def info(self, msg: str) -> None:
        self.log("info", msg)

    def warn(self, msg: str) -> None:
        self.log("warn", msg)

    def error(self, msg: str) -> None:
        self.log("error", msg)
