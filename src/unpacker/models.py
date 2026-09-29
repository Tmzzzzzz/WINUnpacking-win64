"""数据模型：格式枚举、探测结果、解包结果。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


class ArchiveKind(str, Enum):
    """识别出的容器类型。"""

    UNKNOWN = "unknown"
    ZIP = "zip"
    SEVENZIP = "7z"
    RAR = "rar"
    TAR = "tar"
    GZIP = "gzip"
    BZIP2 = "bzip2"
    XZ = "xz"
    ZSTD = "zstd"
    LZMA = "lzma"
    CAB = "cab"
    ISO = "iso"
    PE_EXE = "exe"
    MSI = "msi"
    OLE = "ole"
    GAME_PACK = "gamepack"
    DOTNET = "dotnet"

    @property
    def label(self) -> str:
        return {
            "unknown": "未知",
            "zip": "ZIP 压缩包",
            "7z": "7-Zip 压缩包",
            "rar": "RAR 压缩包",
            "tar": "TAR 归档",
            "gzip": "GZip 压缩流",
            "bzip2": "BZip2 压缩流",
            "xz": "XZ 压缩流",
            "zstd": "Zstd 压缩流",
            "lzma": "LZMA 压缩流",
            "cab": "CAB 压缩包",
            "iso": "ISO 光盘镜像",
            "exe": "PE 可执行/安装包",
            "msi": "MSI 安装包",
            "ole": "OLE 复合文档",
            "gamepack": "游戏/软件资源包",
            "dotnet": ".NET 程序集",
        }.get(self.value, self.value)


class ConflictPolicy(str, Enum):
    """输出文件重名时的处理策略。"""

    RENAME = "rename"        # 保留两者：追加 _1 / _2
    OVERWRITE = "overwrite"  # 直接覆盖
    SKIP = "skip"            # 已存在则跳过不写
    NEWER = "newer"          # 较新者胜：成员比现有文件新才覆盖，否则保留两者

    @property
    def label(self) -> str:
        return {
            "rename": "保留两者（重名自动加序号）",
            "overwrite": "覆盖已存在文件",
            "skip": "跳过已存在文件",
            "newer": "较新者胜（旧文件保留为副本）",
        }.get(self.value, self.value)

    @property
    def short(self) -> str:
        return {
            "rename": "保留两者",
            "overwrite": "覆盖",
            "skip": "跳过",
            "newer": "较新者胜",
        }.get(self.value, self.value)


class ExtractStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    SKIPPED = "skipped"
    NEED_PASSWORD = "need_password"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"
    DRY_RUN = "dry_run"

    @property
    def label(self) -> str:
        return {
            "pending": "等待中",
            "running": "处理中",
            "success": "成功",
            "skipped": "已跳过",
            "need_password": "需要密码",
            "unsupported": "不支持",
            "failed": "失败",
            "dry_run": "试运行",
        }.get(self.value, self.value)

    @property
    def icon(self) -> str:
        """状态图标（界面与文本报告共用）。"""
        return {
            "pending": "•",
            "running": "▶",
            "success": "✓",
            "skipped": "⏸",
            "need_password": "⚠",
            "unsupported": "⊘",
            "failed": "✕",
            "dry_run": "◌",
        }.get(self.value, "?")

    @property
    def color(self) -> str:
        """状态文本色值（浅色主题下可读）。"""
        return {
            "pending": "#8b93a1",
            "running": "#2f6fed",
            "success": "#15803d",
            "skipped": "#6b7280",
            "need_password": "#b45309",
            "unsupported": "#6b7280",
            "failed": "#b91c1c",
            "dry_run": "#7c3aed",
        }.get(self.value, "#1f2328")


@dataclass
class ArchiveInfo:
    """单个文件的探测结果。"""

    path: Path
    kind: ArchiveKind = ArchiveKind.UNKNOWN
    size: int = 0
    encrypted: bool = False
    entry_count: Optional[int] = None
    extractor: str = ""
    note: str = ""
    details: dict = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.path.name


@dataclass
class ExtractResult:
    """单个文件的解包结果。"""

    source: Path
    status: ExtractStatus
    kind: ArchiveKind = ArchiveKind.UNKNOWN
    extractor: str = ""
    output_dir: Optional[Path] = None
    files: list[str] = field(default_factory=list)
    message: str = ""
    password: Optional[str] = None
    duration: float = 0.0
    bytes_written: int = 0    # 实际落盘字节数（用于显示解出体积）
    skipped: int = 0          # 按冲突策略跳过的文件数

    @property
    def ok(self) -> bool:
        return self.status == ExtractStatus.SUCCESS

    @property
    def file_count(self) -> int:
        return len(self.files)

    def to_dict(self) -> dict:
        return {
            "source": str(self.source),
            "status": self.status.value,
            "kind": self.kind.value,
            "extractor": self.extractor,
            "output_dir": str(self.output_dir) if self.output_dir else "",
            "file_count": self.file_count,
            "bytes_written": self.bytes_written,
            "skipped": self.skipped,
            "message": self.message,
            "password": self.password or "",
            "duration": round(self.duration, 3),
            "files": self.files[:500],
        }
