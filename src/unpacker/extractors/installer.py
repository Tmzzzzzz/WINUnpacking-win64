"""安装包解包器：PE 可执行(WinRAR/7z SFX、NSIS、Inno) 与 MSI 安装包。

策略：识别安装器指纹 → 雕刻内嵌归档 → 交由原生解包器提取。
如需完整还原 MSI 的内部文件表，建议配合外部 7-Zip 后端。
"""
from __future__ import annotations

import time
from pathlib import Path

from ..context import ExtractContext
from ..models import ArchiveInfo, ArchiveKind, ExtractResult, ExtractStatus
from ..registry import register
from .base import BaseExtractor
from .carver import carve_embedded

# 安装器指纹（按优先级）
_FINGERPRINTS: list[tuple[bytes, str]] = [
    (b"Rar!\x1a\x07", "WinRAR 自解压 (SFX)"),
    (b"7z\xbc\xaf\x27\x1c", "7-Zip 自解压 (SFX)"),
    (b"Nullsoft", "NSIS 安装包"),
    (b"NSIS", "NSIS 安装包"),
    (b"Inno Setup Setup Data", "Inno Setup 安装包"),
    (b"InstallShield", "InstallShield 安装包"),
    (b"WinZip Self-Extractor", "WinZip 自解压"),
    (b"PK\x03\x04", "含内嵌 ZIP 的自解压包"),
]

_READ = 8 << 20


def _fingerprint(path: Path) -> list[str]:
    """只读取文件头部若干字节做指纹匹配，避免整文件载入内存。"""
    try:
        with open(path, "rb") as fh:
            head = fh.read(_READ)
    except OSError:
        return []
    found = [label for sig, label in _FINGERPRINTS if sig in head]
    # 去重并保持顺序
    return list(dict.fromkeys(found))


@register
class PeInstallerExtractor(BaseExtractor):
    name = "安装包(PE)"
    kinds = (ArchiveKind.PE_EXE,)
    priority = 70

    def extract(self, info: ArchiveInfo, ctx: ExtractContext) -> ExtractResult:
        started = time.perf_counter()
        out = self._prepare_dir(ctx)
        flavors = _fingerprint(info.path)
        if flavors:
            ctx.info(f"识别到安装器类型：{'、'.join(flavors)}")

        files = carve_embedded(info, ctx, out)
        if files:
            tag = f"（{'、'.join(flavors)}）" if flavors else ""
            return self._result(info, ExtractStatus.SUCCESS, files=files,
                                message=f"自 {tag}中雕刻出 {len(files)} 个文件",
                                started=started)
        hint = "、".join(flavors) if flavors else "未识别出已知安装器指纹"
        return self._result(
            info, ExtractStatus.UNSUPPORTED,
            message=f"{hint}；建议安装 7-Zip 以获得完整解包能力", started=started)


@register
class MsiExtractor(BaseExtractor):
    name = "安装包(MSI)"
    kinds = (ArchiveKind.MSI,)
    priority = 70

    def extract(self, info: ArchiveInfo, ctx: ExtractContext) -> ExtractResult:
        started = time.perf_counter()
        out = self._prepare_dir(ctx)

        # 1) 枚举 OLE 复合文档内的流，便于用户了解 MSI 结构
        streams: list[str] = []
        try:
            import olefile
            with olefile.OleFileIO(str(info.path)) as ole:
                streams = ["/".join(s) for s in ole.listdir()]
                ctx.info(f"MSI 内含 {len(streams)} 个 OLE 流")
        except ImportError:
            ctx.warn("未安装 olefile，跳过 MSI 流枚举")
        except Exception as exc:  # noqa: BLE001
            ctx.warn(f"OLE 流枚举失败：{exc}")

        # 2) 雕刻内嵌 CAB 载荷
        files = carve_embedded(info, ctx, out, extractor_names={ArchiveKind.CAB: "原生 CAB"})
        if files:
            return self._result(info, ExtractStatus.SUCCESS, files=files,
                                message=f"从 MSI 内嵌 CAB 中解出 {len(files)} 个文件",
                                started=started)

        if streams:
            report = out / "_msi_streams.txt"
            report.write_text("\n".join(streams), encoding="utf-8")
            return self._result(
                info, ExtractStatus.UNSUPPORTED, files=[str(report)],
                message=f"未发现可解的内嵌 CAB；已导出 {len(streams)} 条流清单，"
                        f"建议安装 7-Zip 完整解包", started=started)

        return self._result(info, ExtractStatus.UNSUPPORTED,
                            message="MSI 解析失败，建议安装 7-Zip", started=started)
