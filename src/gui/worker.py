"""后台工作线程：在 QThread 中运行解包引擎，通过信号回传进度。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from unpacker.engine import Engine, EngineCallbacks, EngineOptions
from unpacker.models import ExtractResult


class UnpackWorker(QThread):
    """批量解包任务。所有 UI 更新一律通过信号传递，避免跨线程操作控件。"""

    sig_log = Signal(str, str)                 # level, message
    sig_progress = Signal(int, int, str)       # done, total, name
    sig_file_started = Signal(int, int, str)   # index, total, path
    sig_file_done = Signal(object)             # ExtractResult
    sig_finished = Signal(list)                # list[ExtractResult]
    sig_fatal = Signal(str)

    def __init__(self, paths: list[Path], options: EngineOptions, parent=None) -> None:
        super().__init__(parent)
        self._paths = list(paths)
        self._options = options
        self._cancelled = False

    # ------------------------------------------------------------------ 控制
    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    # ------------------------------------------------------------------ 执行
    def run(self) -> None:  # noqa: D102
        try:
            callbacks = EngineCallbacks(
                on_log=lambda lv, msg: self.sig_log.emit(lv, msg),
                on_progress=lambda d, t, n: self.sig_progress.emit(d, t, n),
                on_file_start=lambda p, i, n: self.sig_file_started.emit(i, n, str(p)),
                on_file_done=lambda r: self.sig_file_done.emit(r),
                is_cancelled=lambda: self._cancelled,
            )
            engine = Engine(self._options, callbacks)
            results: list[ExtractResult] = engine.run(self._paths)
            self.sig_finished.emit(results)
        except Exception as exc:  # noqa: BLE001 - 兜底，避免线程静默崩溃
            import traceback
            self.sig_fatal.emit(f"{exc}\n{traceback.format_exc(limit=3)}")
