"""解包器抽象基类。

新增格式只需继承 BaseExtractor 并实现 extract()，再在 extractors/__init__.py 注册即可，
主流程与 GUI 无需任何改动（对游戏私有格式的插件化扩展同样走这个接口）。

落盘 API 分三档，按数据来源选择，**大文件请勿使用 _write_bytes**：

    _write_bytes()  内存中的 bytes -> 文件（适合小成员）
    _copy_stream()  已打开的文件对象 / 流 -> 文件（分块，内存 O(chunk)）
    _write_slice()  已打开文件的 [offset, size) 区间 -> 文件（分块，mmap 友好）
"""
from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, BinaryIO, Iterable, Optional

from ..context import ExtractContext
from ..models import ArchiveInfo, ArchiveKind, ConflictPolicy, ExtractResult, ExtractStatus
from ..utils import safe_join, strip_long

#: 流式读写的默认块大小
CHUNK = 1 << 20  # 1 MiB


class BaseExtractor(ABC):
    """所有解包器的基类。"""

    #: 解包器名称（唯一）
    name: str = "base"
    #: 可处理的容器类型
    kinds: tuple[ArchiveKind, ...] = ()
    #: 优先级，数值越小越先被选中
    priority: int = 100
    #: 是否需要可选第三方库
    requires: tuple[str, ...] = ()

    def can_handle(self, info: ArchiveInfo) -> bool:
        return info.kind in self.kinds

    @abstractmethod
    def extract(self, info: ArchiveInfo, ctx: ExtractContext) -> ExtractResult:
        """执行解包，返回结果。实现方需自行捕获业务异常。"""

    # ---------------------------------------------------------------- helpers
    def _result(
        self,
        info: ArchiveInfo,
        status: ExtractStatus,
        *,
        files: Optional[list[str]] = None,
        message: str = "",
        password: Optional[str] = None,
        output_dir: Optional[Path] = None,
        started: Optional[float] = None,
    ) -> ExtractResult:
        duration = (time.perf_counter() - started) if started else 0.0
        return ExtractResult(
            source=info.path,
            status=status,
            kind=info.kind,
            extractor=self.name,
            output_dir=output_dir,
            files=files or [],
            message=message,
            password=password,
            duration=duration,
        )

    @staticmethod
    def _prepare_dir(ctx: ExtractContext) -> Path:
        """创建并返回输出目录（统一转为绝对路径）。

        必须返回绝对路径：``safe_join`` 内部会 ``resolve()``，若此处返回相对路径，
        后续 ``target.relative_to(out)`` 会因绝对/相对混用而抛 ValueError。
        """
        out = Path(ctx.output_dir).expanduser().resolve()
        out.mkdir(parents=True, exist_ok=True)
        return out

    @staticmethod
    def _rel(base: Path, target: Path) -> str:
        """把绝对路径表达为相对输出目录的形式（跨盘符时退回绝对路径）。"""
        try:
            return str(target.relative_to(base))
        except ValueError:
            return os.path.relpath(str(target), str(base))

    # ------------------------------------------------------------ 冲突策略
    @staticmethod
    def _target(ctx: ExtractContext, member: str, out: Path,
                member_mtime: Optional[float] = None) -> Optional[Path]:
        """安全解析成员输出路径，并按 ``ctx.conflict`` 处理重名。

        返回 ``None`` 表示「按跳过策略不写该成员」，调用方应跳过并不要计数。
        """
        target = safe_join(out, member)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            return target

        policy = ctx.conflict
        if policy is ConflictPolicy.OVERWRITE:
            return target
        if policy is ConflictPolicy.SKIP:
            ctx.skipped_files += 1
            return None
        if policy is ConflictPolicy.NEWER:
            src_time = member_mtime if member_mtime is not None else ctx.source_mtime
            if src_time is not None:
                try:
                    if src_time > target.stat().st_mtime:
                        return target          # 归档内的成员更新 -> 覆盖
                except OSError:
                    return target
            # 成员更旧 / 时间未知 -> 落到下面「保留两者」

        # RENAME 与 NEWER 的兜底：追加 _1 / _2 …
        stem, suffix = target.stem, target.suffix
        i = 1
        while target.exists():
            target = target.parent / f"{stem}_{i}{suffix}"
            i += 1
        return target

    # ------------------------------------------------------------ 落盘（小数据）
    @staticmethod
    def _write_bytes(ctx: ExtractContext, out: Path, member: str,
                     data: bytes, member_mtime: Optional[float] = None) -> Optional[str]:
        """把内存中的字节写入成员路径。返回相对路径；按跳过策略则为 None。"""
        target = BaseExtractor._target(ctx, member, out, member_mtime)
        if target is None:
            return None

        def _do() -> None:
            with open(strip_long(str(target)), "wb") as fh:
                fh.write(data)

        BaseExtractor._safe_write(target, _do)
        ctx.bytes_written += len(data)
        return BaseExtractor._rel(Path(out).resolve(), target)

    # ------------------------------------------------------------ 落盘（流式）
    @staticmethod
    def _copy_stream(ctx: ExtractContext, out: Path, member: str, src: BinaryIO,
                     *, chunk_size: int = CHUNK, member_mtime: Optional[float] = None,
                     name: Optional[str] = None, total: int = 0,
                     progress_every: int = 0) -> Optional[str]:
        """把文件对象分块写入成员路径（内存占用 O(chunk_size)）。

        ``total`` 与 ``progress_every`` 用于上报进度：每累计写入
        ``progress_every`` 字节调用一次 ``ctx.progress(done, total, name)``。
        """
        target = BaseExtractor._target(ctx, member, out, member_mtime)
        if target is None:
            return None

        written = 0

        def _do() -> int:
            nonlocal written
            step = progress_every or 0
            next_report = step
            with open(strip_long(str(target)), "wb") as dst:
                while True:
                    ctx.check_cancel()
                    block = src.read(chunk_size)
                    if not block:
                        break
                    dst.write(block)
                    written += len(block)
                    if step and written >= next_report:
                        ctx.progress(written, total or written, name or member)
                        next_report = written + step
            return written

        BaseExtractor._safe_write(target, _do)
        ctx.bytes_written += written
        return BaseExtractor._rel(Path(out).resolve(), target)

    @staticmethod
    def _write_slice(ctx: ExtractContext, out: Path, member: str, src: BinaryIO,
                     offset: int, size: int, *, chunk_size: int = CHUNK,
                     member_mtime: Optional[float] = None,
                     name: Optional[str] = None,
                     progress_every: int = 0) -> Optional[str]:
        """从已打开的文件/mmap 中截取 [offset, offset+size) 分块写入。

        ``src`` 需要支持 ``seek``/``tell``；对 mmap 对象同样适用。
        """
        target = BaseExtractor._target(ctx, member, out, member_mtime)
        if target is None:
            return None

        written = 0

        def _do() -> int:
            nonlocal written
            remaining = size
            step = progress_every or 0
            next_report = step
            src.seek(offset)
            with open(strip_long(str(target)), "wb") as dst:
                while remaining > 0:
                    ctx.check_cancel()
                    block = src.read(min(chunk_size, remaining))
                    if not block:
                        break
                    dst.write(block)
                    remaining -= len(block)
                    written += len(block)
                    if step and written >= next_report:
                        ctx.progress(written, size, name or member)
                        next_report = written + step
            return written

        BaseExtractor._safe_write(target, _do)
        ctx.bytes_written += written
        return BaseExtractor._rel(Path(out).resolve(), target)

    @staticmethod
    def _safe_write(target: Path, writer) -> None:
        """执行写入；中途失败时删除半成品，避免残留错位/解密失败的文件。"""
        try:
            writer()
        except BaseException:
            try:
                os.unlink(strip_long(str(target)))
            except OSError:
                pass
            raise

    # ------------------------------------------------------------ 密码尝试
    def _try_passwords(
        self,
        ctx: ExtractContext,
        attempt,
        *,
        candidates: Optional[Iterable[str]] = None,
    ) -> tuple[bool, Optional[str], Any, Optional[Exception]]:
        """依次尝试密码。

        返回 ``(是否成功, 命中的密码, attempt 的返回值, 最后一次异常)``。

        注意：``attempt`` 在本函数内**已经执行并完成了实际工作**，调用方应直接
        使用返回的产出值，不要再执行一次（否则会重复落盘，在「保留两者」策略
        下还会生成 ``xxx_1`` 重复文件）。
        """
        pwd_list = list(candidates) if candidates is not None else list(ctx.passwords)
        if not pwd_list:
            pwd_list = [None]  # 代表“无密码”
        last_exc: Optional[Exception] = None
        for pwd in pwd_list:
            ctx.check_cancel()
            try:
                value = attempt(pwd)
                return True, pwd, value, None
            except Exception as exc:  # noqa: BLE001 - 逐个密码试错
                last_exc = exc
        return False, None, None, last_exc
