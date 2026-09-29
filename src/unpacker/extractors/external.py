"""外部 7-Zip 后端（兜底链）。

当原生解包器无法处理（LZX 压缩的 CAB、加密 RAR、NSIS/Inno 安装包、
私有游戏包等）时，若系统中存在 7z.exe，则自动调用它完成解包。
可通过环境变量 SEVENZIP_PATH 指定可执行文件位置。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from ..context import ExtractContext
from ..models import ArchiveInfo, ArchiveKind, ExtractResult, ExtractStatus
from ..registry import register
from .base import BaseExtractor

_KINDS = (
    ArchiveKind.ZIP, ArchiveKind.SEVENZIP, ArchiveKind.RAR, ArchiveKind.TAR,
    ArchiveKind.GZIP, ArchiveKind.BZIP2, ArchiveKind.XZ, ArchiveKind.ZSTD,
    ArchiveKind.LZMA, ArchiveKind.CAB, ArchiveKind.ISO, ArchiveKind.PE_EXE,
    ArchiveKind.MSI, ArchiveKind.OLE, ArchiveKind.GAME_PACK,
)

_cached_path: Optional[str] = None
_probed = False


def find_7z() -> Optional[str]:
    """定位 7z 可执行文件，结果缓存的。"""
    global _cached_path, _probed
    if _probed:
        return _cached_path
    _probed = True

    candidates: list[str] = []
    env = os.environ.get("SEVENZIP_PATH", "").strip('"')
    if env and Path(env).is_file():
        candidates.append(env)
    for name in ("7z", "7za", "7zr"):
        p = shutil.which(name)
        if p:
            candidates.append(p)
    for base in (r"C:\Program Files\7-Zip", r"C:\Program Files (x86)\7-Zip",
                 r"C:\Program Files\NanaZip", r"C:\Program Files (x86)\NanaZip"):
        for exe in ("7z.exe", "7za.exe"):
            p = Path(base) / exe
            if p.is_file():
                candidates.append(str(p))

    _cached_path = candidates[0] if candidates else None
    return _cached_path


@register
class External7zExtractor(BaseExtractor):
    name = "外部 7-Zip"
    kinds = _KINDS
    priority = 60  # 原生解包器之后兜底

    def can_handle(self, info: ArchiveInfo) -> bool:
        return info.kind in self.kinds and find_7z() is not None

    def extract(self, info: ArchiveInfo, ctx: ExtractContext) -> ExtractResult:
        started = time.perf_counter()
        exe = find_7z()
        if not exe:
            return self._result(info, ExtractStatus.UNSUPPORTED,
                                message="系统未安装 7-Zip", started=started)

        out = self._prepare_dir(ctx)
        last_err = ""
        for pwd in [None, *ctx.passwords]:
            ctx.check_cancel()
            cmd = [exe, "x", str(info.path), f"-o{out}", "-y", "-bd", "-bso1", "-bsp0",
                   f"-p{pwd or ''}"]
            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=3600,
                )
            except subprocess.TimeoutExpired:
                return self._result(info, ExtractStatus.FAILED,
                                    message="外部 7-Zip 执行超时", started=started)
            except OSError as exc:
                return self._result(info, ExtractStatus.UNSUPPORTED,
                                    message=f"无法启动 7-Zip：{exc}", started=started)

            if proc.returncode == 0:
                files = [str(p) for p in Path(out).rglob("*") if p.is_file()]
                return self._result(info, ExtractStatus.SUCCESS, files=files, password=pwd,
                                    message=f"外部 7-Zip 解出 {len(files)} 个文件",
                                    started=started)
            last_err = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or [""]
            last_err = last_err[0]

        if ctx.passwords:
            return self._result(info, ExtractStatus.NEED_PASSWORD,
                                message=f"外部 7-Zip：密码尝试失败（{last_err}）",
                                started=started)
        return self._result(info, ExtractStatus.FAILED,
                            message=f"外部 7-Zip 返回失败：{last_err}", started=started)
