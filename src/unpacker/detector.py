"""魔数（Magic Bytes）格式探测。

不依赖文件扩展名，直接读取文件头（必要时读取尾部的 ZIP EOCD）判断真实类型，
并对加密标志位做初步判定。
"""
from __future__ import annotations

import struct
from pathlib import Path

from .models import ArchiveInfo, ArchiveKind

_HEADER_SIZE = 4096
_TAIL_SIZE = 128 * 1024  # ZIP EOCD / 尾部签名可能落在最后

# (offset, signature, kind)  按可靠性排序
_SIGNATURES: list[tuple[int, bytes, ArchiveKind]] = [
    (0, b"PK\x03\x04", ArchiveKind.ZIP),
    (0, b"PK\x05\x06", ArchiveKind.ZIP),   # 空压缩包
    (0, b"PK\x07\x08", ArchiveKind.ZIP),   # 跨卷
    (0, b"7z\xbc\xaf\x27\x1c", ArchiveKind.SEVENZIP),
    (0, b"Rar!\x1a\x07\x00", ArchiveKind.RAR),
    (0, b"Rar!\x1a\x07\x01\x00", ArchiveKind.RAR),
    (0, b"\x1f\x8b\x08", ArchiveKind.GZIP),
    (0, b"BZh", ArchiveKind.BZIP2),
    (0, b"\xfd7zXZ\x00", ArchiveKind.XZ),
    (0, b"\x28\xb5\x2f\xfd", ArchiveKind.ZSTD),
    (0, b"MSCF", ArchiveKind.CAB),
    (0, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", ArchiveKind.OLE),
    (0, b"\x5d\x00\x00", ArchiveKind.LZMA),
    (0, b"MZ", ArchiveKind.PE_EXE),
]

# 扩展名兜底（当魔数不可辨识时）
_EXT_HINTS: dict[str, ArchiveKind] = {
    ".zip": ArchiveKind.ZIP, ".zipx": ArchiveKind.ZIP,
    ".7z": ArchiveKind.SEVENZIP,
    ".rar": ArchiveKind.RAR,
    ".tar": ArchiveKind.TAR,
    ".gz": ArchiveKind.GZIP, ".tgz": ArchiveKind.GZIP,
    ".bz2": ArchiveKind.BZIP2, ".tbz2": ArchiveKind.BZIP2,
    ".xz": ArchiveKind.XZ, ".txz": ArchiveKind.XZ,
    ".zst": ArchiveKind.ZSTD,
    ".lzma": ArchiveKind.LZMA,
    ".cab": ArchiveKind.CAB,
    ".iso": ArchiveKind.ISO,
    ".msi": ArchiveKind.MSI,
    ".exe": ArchiveKind.PE_EXE,
    ".pak": ArchiveKind.GAME_PACK, ".npk": ArchiveKind.GAME_PACK,
    ".bundle": ArchiveKind.GAME_PACK, ".assets": ArchiveKind.GAME_PACK,
    ".dat": ArchiveKind.GAME_PACK, ".res": ArchiveKind.GAME_PACK,
    ".wad": ArchiveKind.GAME_PACK, ".vpk": ArchiveKind.GAME_PACK,
    ".mpq": ArchiveKind.GAME_PACK, ".arc": ArchiveKind.GAME_PACK,
}


def _read_head(path: Path, size: int = _HEADER_SIZE) -> bytes:
    with open(path, "rb") as fh:
        return fh.read(size)


def _read_tail(path: Path, size: int = _TAIL_SIZE) -> bytes:
    with open(path, "rb") as fh:
        try:
            fh.seek(-size, 2)
        except OSError:
            fh.seek(0)
        return fh.read()


def _is_tar(head: bytes) -> bool:
    """tar 的 magic 位于偏移 257。"""
    return len(head) >= 263 and head[257:262] == b"ustar"


def _is_iso(path: Path) -> bool:
    """ISO9660 的 PVD 位于扇区 16（偏移 0x8000），标识为 'CD001'。"""
    try:
        with open(path, "rb") as fh:
            fh.seek(0x8001)
            return fh.read(5) == b"CD001"
    except OSError:
        return False


def _is_pe(head: bytes) -> bool:
    """MZ 头 + PE\\0\\0 签名，才是真正的 PE（否则可能是 DOS 或伪装）。"""
    if len(head) < 0x40 or head[:2] != b"MZ":
        return False
    try:
        (pe_off,) = struct.unpack_from("<I", head, 0x3C)
    except struct.error:
        return False
    if 0 < pe_off < len(head) - 4:
        return head[pe_off:pe_off + 4] == b"PE\x00\x00"
    return True  # 偏移越界，退化为 MZ 判定


#: PE 数据目录里 CLR 头（IMAGE_COR20_HEADER）的序号
_DD_CLR = 14


def _is_dotnet(head: bytes) -> bool:
    """是否为托管 .NET 程序集。

    只读 PE 可选头里的数据目录 14（CLR）即可判定，无需解析节表：
    非托管 PE 该项恒为 0。真正的校验（COR20 / BSJB 元数据签名）交给解包器。
    """
    if len(head) < 0x40 or head[:2] != b"MZ":
        return False
    try:
        (pe_off,) = struct.unpack_from("<I", head, 0x3C)
    except struct.error:
        return False
    opt = pe_off + 24                       # PE 签名(4) + COFF 头(20)
    if opt + 2 > len(head) or head[pe_off:pe_off + 4] != b"PE\x00\x00":
        return False
    (magic,) = struct.unpack_from("<H", head, opt)
    dd = opt + (112 if magic == 0x20B else 96)
    clr = dd + _DD_CLR * 8
    if clr + 8 > len(head):
        return False
    rva, size = struct.unpack_from("<II", head, clr)
    return rva != 0 and size >= 0x48        # sizeof(IMAGE_COR20_HEADER) == 72


def _zip_encrypted(head: bytes) -> bool:
    """ZIP 通用位标志 bit0 = 1 表示加密。"""
    if len(head) < 8 or head[:2] != b"PK":
        return False
    (flags,) = struct.unpack_from("<H", head, 6)
    return bool(flags & 0x0001)


def _sevenzip_probe_encrypted(path: Path) -> bool:
    """7z 头部没有加密标志位，只能靠读头部判断。

    优先使用 py7zr 的 needs_password()；头部本身被加密时会抛异常，
    此时同样视为需要密码。
    """
    try:
        import py7zr
    except ImportError:
        return False
    try:
        with py7zr.SevenZipFile(path, mode="r") as archive:
            probe = getattr(archive, "needs_password", None)
            if callable(probe):
                return bool(probe())
            archive.getnames()
            return False
    except Exception:  # noqa: BLE001 - 头部不可读：加密或损坏
        return True


def detect(path: str | Path) -> ArchiveInfo:
    """探测文件类型，返回 ArchiveInfo。"""
    path = Path(path)
    info = ArchiveInfo(path=path)
    try:
        info.size = path.stat().st_size
    except OSError as exc:
        info.note = f"无法读取文件：{exc}"
        return info

    if not path.is_file():
        info.note = "不是文件"
        return info

    try:
        head = _read_head(path)
    except OSError as exc:
        info.note = f"读取失败：{exc}"
        return info

    # 1) 优先魔数匹配
    for offset, sig, kind in _SIGNATURES:
        if head[offset:offset + len(sig)] == sig:
            info.kind = kind
            break

    # 2) 特殊魔数：tar / iso
    if info.kind is ArchiveKind.UNKNOWN and _is_tar(head):
        info.kind = ArchiveKind.TAR
    if info.kind is ArchiveKind.UNKNOWN and _is_iso(path):
        info.kind = ArchiveKind.ISO

    # 3) MZ 但非 PE -> 归入 OLE/未知
    if info.kind is ArchiveKind.PE_EXE and not _is_pe(head):
        info.kind = ArchiveKind.UNKNOWN
        info.note = "MZ 头但缺少 PE 签名"

    # 3.5) 托管 .NET 程序集（含 WPF/WinForms/控制台），单独成类
    if info.kind is ArchiveKind.PE_EXE and _is_dotnet(head):
        info.kind = ArchiveKind.DOTNET
        info.note = "托管 .NET 程序集（可反编译为 C#）"

    # 4) 扩展名兜底
    if info.kind is ArchiveKind.UNKNOWN:
        hint = _EXT_HINTS.get(path.suffix.lower())
        if hint:
            info.kind = hint
            info.note = "依据扩展名推断"

    # 5) 尾部兜底：PK 出现在尾部（自解压 exe 常见）-> 标记为可雕刻
    if info.kind in (ArchiveKind.PE_EXE, ArchiveKind.UNKNOWN, ArchiveKind.GAME_PACK):
        tail = _read_tail(path)
        if b"PK\x03\x04" in tail:
            info.details["embedded_zip"] = True
        if b"MSCF" in tail:
            info.details["embedded_cab"] = True

    # 6) 加密标志
    if info.kind is ArchiveKind.ZIP:
        info.encrypted = _zip_encrypted(head)
    elif info.kind is ArchiveKind.SEVENZIP:
        info.encrypted = _sevenzip_probe_encrypted(path)
        if info.encrypted:
            info.note = "7z 头部/数据已加密，解包需密码"

    # 7) MSI 实为 OLE 复合文档
    if info.kind is ArchiveKind.OLE and path.suffix.lower() in (".msi", ".msp", ".mst"):
        info.kind = ArchiveKind.MSI

    return info
