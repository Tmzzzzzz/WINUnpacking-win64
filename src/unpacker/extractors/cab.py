"""原生 CAB (Microsoft Cabinet) 解包器。

支持：不压缩(NONE) 与 MSZIP(deflate) 两种 folder 压缩方式；
LZX / Quantum 需要外部 7-Zip，将返回 UNSUPPORTED 交由兜底链处理。

文件访问走 ``mmap``，folder 按 CFDATA 块**按需解压**（每块 ≤32KiB），
因此峰值内存与 cabinet 体积无关，大型 CAB 也不会把整卷读进内存。
"""
from __future__ import annotations

import bisect
import mmap
import struct
import time
import zlib
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from ..context import ExtractContext
from ..models import ArchiveInfo, ArchiveKind, ExtractResult, ExtractStatus
from ..registry import register
from ..utils import decode_member_name, strip_long
from .base import BaseExtractor

_FLAG_PREV = 0x0001
_FLAG_NEXT = 0x0002
_FLAG_RESERVE = 0x0004

_COMP_NONE, _COMP_MSZIP, _COMP_QUANTUM, _COMP_LZX = 0, 1, 2, 3
_PLAIN = (_COMP_NONE, _COMP_MSZIP)

_COMP_NAMES = {_COMP_NONE: "NONE", _COMP_MSZIP: "MSZIP",
               _COMP_QUANTUM: "Quantum", _COMP_LZX: "LZX"}


def _le16(buf, off: int) -> int:
    return struct.unpack_from("<H", buf, off)[0]


def _le32(buf, off: int) -> int:
    return struct.unpack_from("<I", buf, off)[0]


def _next_nul(data, pos: int) -> int:
    """找到下一个 NUL（bytes / mmap 都支持 find）。"""
    idx = data.find(b"\x00", pos)
    if idx < 0:
        raise ValueError("CAB 头部字符串未正常结束")
    return idx


def _dos_mtime(dos_date: int, dos_time: int) -> Optional[float]:
    """CAB 文件表里的 DOS 日期/时间 -> epoch 秒。"""
    if not dos_date:
        return None
    try:
        return datetime(
            1980 + ((dos_date >> 9) & 0x7F),
            (dos_date >> 5) & 0x0F,
            dos_date & 0x1F,
            (dos_time >> 11) & 0x1F,
            (dos_time >> 5) & 0x3F,
            (dos_time & 0x1F) * 2,
        ).timestamp()
    except ValueError:
        return None


class _Cab:
    """极简 CAB 解析结果。"""

    def __init__(self) -> None:
        self.folders: list[dict] = []
        self.files: list[dict] = []
        self.has_prev = False
        self.has_next = False


def _parse(data, encoding: str = "auto") -> _Cab:
    if bytes(data[:4]) != b"MSCF":
        raise ValueError("不是 CAB 文件（缺少 MSCF 签名）")

    cab = _Cab()
    coff_files = _le32(data, 16)
    c_folders = _le16(data, 26)
    c_files = _le16(data, 28)
    flags = _le16(data, 30)

    cab.has_prev = bool(flags & _FLAG_PREV)
    cab.has_next = bool(flags & _FLAG_NEXT)

    pos = 36
    if flags & _FLAG_RESERVE:
        cb_header = _le16(data, pos)
        pos += 4 + cb_header  # cbCFHeader/cbCFFolder/cbCFData + 保留区

    if flags & _FLAG_PREV:
        pos = _next_nul(data, pos) + 1   # szCabinetPrev
        pos = _next_nul(data, pos) + 1   # szDiskPrev
    if flags & _FLAG_NEXT:
        pos = _next_nul(data, pos) + 1
        pos = _next_nul(data, pos) + 1

    # folder 表（同时展开每个 folder 的 CFDATA 块索引）
    for _ in range(c_folders):
        coff = _le32(data, pos)
        cdata = _le16(data, pos + 4)
        ctype = _le16(data, pos + 6) & 0x000F
        blocks: list[tuple[int, int, int]] = []   # (数据起始, 压缩长度, 解压长度)
        p = coff
        for _b in range(cdata):
            # CFDATA = csum(4) cbData(2) cbUncomp(2) ab[cbData]
            cb_data = _le16(data, p + 4)
            cb_uncomp = _le16(data, p + 6)
            blocks.append((p + 8, cb_data, cb_uncomp))
            p += 8 + cb_data
        cab.folders.append({"coff": coff, "cdata": cdata, "type": ctype, "blocks": blocks})
        pos += 8

    # file 表
    pos = coff_files
    for _ in range(c_files):
        cb_file = _le32(data, pos)
        uoff = _le32(data, pos + 4)
        ifolder = _le16(data, pos + 8)
        mtime = _dos_mtime(_le16(data, pos + 10), _le16(data, pos + 12))
        name_end = _next_nul(data, pos + 16)
        raw_name = bytes(data[pos + 16:name_end])
        cab.files.append({
            "size": cb_file, "offset": uoff, "folder": ifolder,
            "name": decode_member_name(raw_name, encoding, fallback="cp1252"),
            "mtime": mtime,
        })
        pos = name_end + 1
    return cab


class _FolderReader:
    """按需解压 CAB folder 的块。

    CFDATA 的结构为 ``csum(4) cbData(2) cbUncomp(2) ab[cbData]``，
    因此可以直接由元数据算出每个块的压缩/解压长度，按文件所需的偏移区间
    只解压命中的块（MSZIP 的每个块是独立 deflate 流，可单独解压）。
    """

    def __init__(self, data, folder: dict) -> None:
        self.data = data
        self.comp = folder["type"]
        self.blocks = folder["blocks"]
        sizes: list[int] = []
        for _off, cb_data, cb_uncomp in self.blocks:
            if self.comp == _COMP_NONE:
                sizes.append(cb_data)
            elif cb_uncomp:
                sizes.append(cb_uncomp)
            else:
                raise ValueError("CAB 块缺少解压长度信息，无法按块读取")
        self.sizes = sizes
        self.starts: list[int] = []
        acc = 0
        for s in sizes:
            self.starts.append(acc)
            acc += s
        self.total = acc
        self._idx = -1
        self._buf = b""

    def _block(self, i: int) -> bytes:
        if i != self._idx:
            off, cb_data, _ = self.blocks[i]
            raw = bytes(self.data[off:off + cb_data])
            if self.comp == _COMP_NONE:
                buf = raw
            else:
                if raw[:2] != b"CK":
                    raise ValueError("MSZIP 块缺少 CK 签名")
                buf = zlib.decompressobj(-15).decompress(raw[2:])
            self._idx, self._buf = i, buf
        return self._buf

    def iter_range(self, start: int, length: int) -> Iterator[tuple[int, bytes]]:
        """按顺序产出 ``(相对偏移, 数据)``，覆盖 [start, start+length)。"""
        if length <= 0:
            return
        end = start + length
        if end > self.total:
            raise ValueError(f"文件偏移超出 CAB 数据范围（{end} > {self.total}）")
        pos = start
        i = max(0, bisect.bisect_right(self.starts, pos) - 1)
        while pos < end and i < len(self.blocks):
            blk = self._block(i)
            b_start = self.starts[i]
            b_end = b_start + len(blk)
            if pos >= b_end:
                i += 1
                continue
            lo = pos - b_start
            hi = min(b_end, end) - b_start
            yield pos - start, blk[lo:hi]
            pos += hi - lo
            i += 1


@register
class CabExtractor(BaseExtractor):
    name = "原生 CAB"
    kinds = (ArchiveKind.CAB,)
    priority = 50

    def extract(self, info: ArchiveInfo, ctx: ExtractContext) -> ExtractResult:
        started = time.perf_counter()
        out = self._prepare_dir(ctx)

        fh = open(strip_long(str(info.path)), "rb")
        data = None
        try:
            try:
                data = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
            except (ValueError, OSError):
                data = fh.read()          # 空文件等异常情况退回整读

            try:
                cab = _parse(data, ctx.encoding)
            except Exception as exc:  # noqa: BLE001
                return self._result(info, ExtractStatus.UNSUPPORTED,
                                    message=f"CAB 头解析失败（转交外部 7-Zip）：{exc}",
                                    started=started)

            try:
                written = self._extract_all(ctx, out, data, cab)
            except NotImplementedError as exc:
                return self._result(
                    info, ExtractStatus.UNSUPPORTED,
                    message=f"CAB 使用 {exc} 压缩，需外部 7-Zip 后端", started=started)
            except Exception as exc:  # noqa: BLE001
                return self._result(info, ExtractStatus.FAILED,
                                    message=f"CAB 解包失败：{exc}", started=started)
        finally:
            if isinstance(data, mmap.mmap):
                try:
                    data.close()
                except (ValueError, OSError):
                    pass
            fh.close()

        note = "解出 %d 个文件" % len(written)
        if cab.has_prev or cab.has_next:
            note += "（该 CAB 属于分卷，跨卷文件可能缺失）"
        return self._result(info, ExtractStatus.SUCCESS, files=written,
                            message=note, started=started)

    def _extract_all(self, ctx: ExtractContext, out: Path, data, cab: _Cab) -> list[str]:
        readers: dict[int, _FolderReader] = {}
        written: list[str] = []
        usable = [f for f in cab.files if 0 <= f["folder"] < len(cab.folders)]
        for idx, f in enumerate(usable, 1):
            ctx.check_cancel()
            fi = f["folder"]
            folder = cab.folders[fi]
            if folder["type"] not in _PLAIN:
                raise NotImplementedError(_COMP_NAMES.get(folder["type"], "未知"))

            if fi not in readers:
                readers.clear()           # 只保留当前 folder，控制内存
                readers[fi] = _FolderReader(data, folder)

            saved = self._write_range(ctx, out, f["name"], readers[fi],
                                      f["offset"], f["size"], f.get("mtime"))
            if saved is not None:
                written.append(saved)
            ctx.progress(idx, len(usable), f["name"])
        return written

    @staticmethod
    def _write_range(ctx: ExtractContext, out: Path, member: str, reader: _FolderReader,
                     offset: int, size: int, mtime: Optional[float]) -> Optional[str]:
        """把 folder 中 [offset, offset+size) 区间流式落盘。"""
        target = BaseExtractor._target(ctx, member, out, mtime)
        if target is None:
            return None

        written = 0

        def _do() -> None:
            nonlocal written
            with open(strip_long(str(target)), "wb") as dst:
                for _rel_off, chunk in reader.iter_range(offset, size):
                    ctx.check_cancel()
                    dst.write(chunk)
                    written += len(chunk)

        BaseExtractor._safe_write(target, _do)
        ctx.bytes_written += written
        return BaseExtractor._rel(Path(out).resolve(), target)
