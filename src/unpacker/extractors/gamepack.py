"""游戏/软件资源包解包器。

对 .pak/.npk/.dat/.bundle 等私有格式，采用「魔数雕刻」策略：
在二进制中定位内嵌的标准归档并提取。若仍未命中，则交由 plugins/ 目录下的
专用插件处理（见 unpacker/plugins.py）。
"""
from __future__ import annotations

import time
from pathlib import Path

from ..context import ExtractContext
from ..models import ArchiveInfo, ArchiveKind, ExtractResult, ExtractStatus
from ..registry import register
from .base import BaseExtractor
from .carver import _SIGS, carve_embedded


@register
class GamePackExtractor(BaseExtractor):
    name = "资源包雕刻"
    kinds = (ArchiveKind.GAME_PACK, ArchiveKind.UNKNOWN)
    priority = 90

    def extract(self, info: ArchiveInfo, ctx: ExtractContext) -> ExtractResult:
        started = time.perf_counter()
        out = self._prepare_dir(ctx)

        known = ", ".join(sorted({k.value for _s, k in _SIGS}))
        ctx.info(f"按资源包处理，扫描内嵌归档（候选类型：{known}）")

        files = carve_embedded(info, ctx, out)
        if files:
            return self._result(info, ExtractStatus.SUCCESS, files=files,
                                message=f"雕刻出 {len(files)} 个文件", started=started)

        return self._result(
            info, ExtractStatus.UNSUPPORTED,
            message="未在内嵌数据中找到标准归档；该私有格式需编写专用插件"
                    "（放入 plugins/ 目录，继承 BaseExtractor 即可自动注册）",
            started=started)
