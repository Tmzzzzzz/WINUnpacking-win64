"""任务派发引擎：探测 → 选择解包器 → 执行 → （可选）递归解包 → 汇总。"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from . import detector, registry
from .context import CancelRequested, ExtractContext
from .models import (
    ArchiveInfo, ArchiveKind, ConflictPolicy, ExtractResult, ExtractStatus,
)

_TERMINAL = (ExtractStatus.SUCCESS, ExtractStatus.NEED_PASSWORD, ExtractStatus.SKIPPED)


@dataclass
class EngineOptions:
    """引擎行为开关。"""

    output_dir: Optional[Path] = None      # None -> 每个文件使用 <文件名>_unpacked
    merge_output: bool = False             # 批量时全部解到同一目录
    conflict: ConflictPolicy = ConflictPolicy.RENAME
    recursive: bool = False                # 递归解包嵌套压缩包
    max_depth: int = 3
    passwords: list[str] = field(default_factory=list)
    carve: bool = True                     # 是否允许雕刻内嵌归档
    encoding: str = "auto"                 # 成员名编码（auto = 自动回退）
    dry_run: bool = False                  # 只探测并给出计划，不落盘

    def context(self, output_dir: Path, *, log=None, progress=None,
                is_cancelled=None, source_mtime: Optional[float] = None
                ) -> ExtractContext:
        return ExtractContext(
            output_dir=output_dir,
            passwords=list(self.passwords),
            conflict=self.conflict,
            recursive=self.recursive,
            max_depth=self.max_depth,
            encoding=self.encoding,
            source_mtime=source_mtime,
            progress=progress or (lambda d, t, n: None),
            log=log or (lambda lv, m: None),
            is_cancelled=is_cancelled or (lambda: False),
        )


@dataclass
class EngineCallbacks:
    on_log: Callable[[str, str], None] = lambda lv, msg: None
    on_progress: Callable[[int, int, str], None] = lambda d, t, n: None
    on_file_start: Callable[[Path, int, int], None] = lambda p, i, n: None
    on_file_done: Callable[[ExtractResult], None] = lambda r: None
    is_cancelled: Callable[[], bool] = lambda: False


class Engine:
    """解包引擎。线程内使用，回调由调用方决定是否转发到 UI。"""

    def __init__(self, options: EngineOptions, callbacks: Optional[EngineCallbacks] = None):
        self.options = options
        self.cb = callbacks or EngineCallbacks()

    # ------------------------------------------------------------------ 探测
    def inspect(self, path: str | Path) -> ArchiveInfo:
        registry.load_all()
        info = detector.detect(path)
        candidates = registry.find(info)
        if candidates:
            info.extractor = candidates[0].name
        return info

    # ------------------------------------------------------------------ 解包
    def extract(self, path: str | Path) -> ExtractResult:
        registry.load_all()
        path = Path(path)
        info = self.inspect(path)

        if info.kind is ArchiveKind.UNKNOWN and not self.options.carve:
            return ExtractResult(source=path, status=ExtractStatus.UNSUPPORTED,
                                 message="未能识别文件格式")

        out_dir = self._resolve_output(path)

        if self.options.dry_run:
            return ExtractResult(
                source=path, status=ExtractStatus.DRY_RUN, kind=info.kind,
                output_dir=out_dir,
                message=f"将按「{info.kind.label}」处理并输出到 {out_dir}",
            )

        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = None
        ctx = self.options.context(
            out_dir,
            log=self.cb.on_log,
            progress=self.cb.on_progress,
            is_cancelled=self.cb.is_cancelled,
            source_mtime=mtime,
        )

        candidates = registry.find(info)
        if not candidates:
            return ExtractResult(source=path, status=ExtractStatus.UNSUPPORTED,
                                 kind=info.kind,
                                 message=f"没有解包器可处理 {info.kind.label}")

        try:
            result = self._dispatch(info, candidates, ctx)
        except CancelRequested:
            return ExtractResult(source=path, status=ExtractStatus.SKIPPED,
                                 kind=info.kind, message="已取消")

        # 递归解包嵌套归档
        if (result.ok and self.options.recursive and self.options.max_depth > 1
                and result.output_dir):
            nested = self._extract_nested(result.output_dir, depth=1)
            result.files.extend(nested)

        return result

    def _dispatch(self, info: ArchiveInfo, candidates, ctx: ExtractContext) -> ExtractResult:
        """按优先级依次尝试解包器，直到出现确定性结果。"""
        last: Optional[ExtractResult] = None
        for extractor in candidates:
            ctx.info(f"尝试解包器：{extractor.name}")
            started = time.perf_counter()
            try:
                res = extractor.extract(info, ctx)
            except CancelRequested:
                raise
            except Exception as exc:  # noqa: BLE001 - 解包器异常逐一降级
                res = ExtractResult(source=info.path, status=ExtractStatus.FAILED,
                                    kind=info.kind, extractor=extractor.name,
                                    message=f"{extractor.name} 内部错误：{exc}",
                                    duration=time.perf_counter() - started)
            res.kind = info.kind
            if res.output_dir is None:
                res.output_dir = ctx.output_dir
            # 统计量由落盘助手累加在 ctx 上，此处统一回填（含多次降级尝试的总和）
            res.bytes_written = ctx.bytes_written
            res.skipped = ctx.skipped_files
            if res.status in _TERMINAL:
                self._annotate(res, ctx)
                return res
            # 未成功：记录原因后再降级到下一个解包器，避免失败原因被静默吞掉
            ctx.warn(f"  └ {extractor.name} 未成功（{res.status.label}）：{res.message}")
            last = res
        if last is not None:
            last.bytes_written = ctx.bytes_written
            last.skipped = ctx.skipped_files
            self._annotate(last, ctx)
        return last or ExtractResult(source=info.path, status=ExtractStatus.UNSUPPORTED,
                                     kind=info.kind, message="所有解包器均未成功")

    @staticmethod
    def _annotate(res: ExtractResult, ctx: ExtractContext) -> None:
        """把「按冲突策略跳过 N 个」补充进结果说明。"""
        if not ctx.skipped_files:
            return
        extra = f"按冲突策略跳过 {ctx.skipped_files} 个已存在文件"
        res.message = f"{res.message}；{extra}" if res.message else extra

    # ------------------------------------------------------------------ 批量
    def run(self, paths: Iterable[str | Path]) -> list[ExtractResult]:
        paths = [Path(p) for p in paths]
        results: list[ExtractResult] = []
        for idx, p in enumerate(paths, 1):
            if self.cb.is_cancelled():
                break
            self.cb.on_file_start(p, idx, len(paths))
            res = self.extract(p)
            self.cb.on_file_done(res)
            results.append(res)
        return results

    # ---------------------------------------------------------------- helpers
    def _resolve_output(self, path: Path) -> Path:
        if self.options.merge_output and self.options.output_dir:
            base = Path(self.options.output_dir)
        elif self.options.output_dir:
            base = Path(self.options.output_dir) / f"{path.name}_unpacked"
        else:
            base = path.parent / f"{path.name}_unpacked"
        # 试运行只给计划，连目录都不创建
        if not self.options.dry_run:
            base.mkdir(parents=True, exist_ok=True)
        return base

    def _extract_nested(self, root: Path, depth: int) -> list[str]:
        """递归解包输出目录中残留的压缩包。"""
        produced: list[str] = []
        if depth >= self.options.max_depth:
            return produced
        for child in sorted(Path(root).rglob("*")):
            if self.cb.is_cancelled():
                break
            if not child.is_file():
                continue
            info = detector.detect(child)
            if info.kind is ArchiveKind.UNKNOWN:
                continue
            target = child.parent / f"{child.name}_unpacked"
            try:
                child_mtime = child.stat().st_mtime
            except OSError:
                child_mtime = None
            ctx = self.options.context(target, log=self.cb.on_log,
                                       progress=self.cb.on_progress,
                                       is_cancelled=self.cb.is_cancelled,
                                       source_mtime=child_mtime)
            cands = registry.find(info)
            if not cands:
                continue
            self.cb.on_log("info", f"递归解包：{child.name}（层级 {depth}）")
            sub = self._dispatch(info, cands, ctx)
            if sub.ok:
                produced.append(f"[递归] {child} -> {sub.file_count} 个文件")
                produced.extend(self._extract_nested(target, depth + 1))
        return produced
