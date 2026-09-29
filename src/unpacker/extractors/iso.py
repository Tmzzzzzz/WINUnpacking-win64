"""原生 ISO9660 光盘镜像解包器。

支持 Primary Volume Descriptor、Joliet 补充卷（长文件名/Unicode）、
以及 Rock Ridge 的 NM 扩展名。UDF 格式不在支持范围内。
"""
from __future__ import annotations

import struct
import time
from pathlib import Path

from ..context import ExtractContext
from ..models import ArchiveInfo, ArchiveKind, ExtractResult, ExtractStatus
from ..registry import register
from .base import BaseExtractor

_SECTOR = 2048
_VD_OFFSET = 16 * _SECTOR


def _le32(buf: bytes, off: int) -> int:
    return struct.unpack_from("<I", buf, off)[0]


def _le16(buf: bytes, off: int) -> int:
    return struct.unpack_from("<H", buf, off)[0]


class _IsoReader:
    def __init__(self, path: Path) -> None:
        self._fh = open(path, "rb")
        self.joliet = False
        self.root: dict | None = None

    def close(self) -> None:
        self._fh.close()

    def read_at(self, offset: int, size: int) -> bytes:
        self._fh.seek(offset)
        return self._fh.read(size)

    # -------------------------------------------------------------- 卷描述符
    def parse_volumes(self) -> None:
        for i in range(16, 256):
            vd = self.read_at(i * _SECTOR, _SECTOR)
            if len(vd) < 7:
                break
            vd_type, ident = vd[0], vd[1:6]
            if ident != b"CD001":
                break
            if vd_type == 1 and self.root is None:      # PVD（兜底）
                self.root = self._dir_record(vd[156:], joliet=False)
            elif vd_type == 2:                          # SVD：优先 Joliet
                esc = vd[88:91]
                if esc in (b"%/@", b"%/C", b"%/E"):
                    self.joliet = True
                    self.root = self._dir_record(vd[156:], joliet=True)
            elif vd_type == 255:
                break
        if self.root is None:
            raise ValueError("未找到有效的 ISO9660 卷描述符")

    # ------------------------------------------------------------ 目录记录
    @staticmethod
    def _dir_record(rec: bytes, joliet: bool) -> dict:
        ext_attr = rec[1]
        extent = _le32(rec, 2)
        size = _le32(rec, 10)
        flags = rec[25]
        id_len = rec[32]
        ident = rec[33:33 + id_len]
        name = ident.decode("utf-16-be", "replace") if joliet else \
            ident.decode("latin-1", "replace")
        return {
            "extent": extent, "size": size, "flags": flags,
            "name": name, "ext_attr": ext_attr,
        }

    def _iter_records(self, data: bytes):
        pos = 0
        while pos < len(data):
            length = data[pos]
            if length == 0:
                # 扇区边界对齐：跳到下一个扇区
                pos = (pos // _SECTOR + 1) * _SECTOR
                if pos >= len(data):
                    break
                continue
            yield self._dir_record(data[pos:pos + length], self.joliet), data[pos:pos + length]
            pos += length

    @staticmethod
    def _rock_ridge_name(rec: bytes) -> str | None:
        """从 System Use 区提取 Rock Ridge NM 名。"""
        id_len = rec[32]
        p = 33 + id_len
        if id_len % 2 == 0:
            p += 1
        su = rec[p:]
        parts: list[str] = []
        while len(su) >= 4:
            sig, ln = su[0:2], su[2]
            if ln < 4 or ln > len(su):
                break
            if sig == b"NM" and not (su[3] & 0x06):  # 跳过 . / ..
                parts.append(su[5:ln].decode("utf-8", "replace"))
            su = su[ln:]
        return "".join(parts) if parts else None

    # ------------------------------------------------------------------ 遍历
    def walk(self, ctx: ExtractContext, out: Path) -> list[str]:
        written: list[str] = []
        assert self.root is not None
        self._walk_dir(ctx, out, self.root["extent"], self.root["size"], "", written, 0)
        return written

    def _walk_dir(self, ctx, out, extent, size, prefix, written, depth) -> None:
        if depth > 24:
            ctx.warn(f"目录层级过深，已跳过：{prefix}")
            return
        data = self.read_at(extent * _SECTOR, size)
        for rec, raw in self._iter_records(data):
            ctx.check_cancel()
            name = rec["name"]
            if len(name) == 1 and ord(name[0]) in (0, 1):
                continue  # '.' / '..'
            name = name.split(";")[0].rstrip(".") or "_"
            if not self.joliet:
                rr = self._rock_ridge_name(raw)
                if rr:
                    name = rr
            rel = f"{prefix}{name}"
            file_size = rec["size"]
            if rec["flags"] & 0x02:  # 目录
                self._walk_dir(ctx, out, rec["extent"], file_size, rel + "/",
                               written, depth + 1)
            else:
                # 直接从镜像按扇区偏移分块落盘：文件内容不整体载入内存
                saved = BaseExtractor._write_slice(
                    ctx, out, rel, self._fh,
                    rec["extent"] * _SECTOR, file_size, name=rel)
                if saved is not None:
                    written.append(saved)
                ctx.progress(len(written), 0, rel)


@register
class IsoExtractor(BaseExtractor):
    name = "原生 ISO9660"
    kinds = (ArchiveKind.ISO,)
    priority = 50

    def extract(self, info: ArchiveInfo, ctx: ExtractContext) -> ExtractResult:
        started = time.perf_counter()
        out = self._prepare_dir(ctx)
        reader = _IsoReader(Path(info.path))
        try:
            reader.parse_volumes()
            written = reader.walk(ctx, out)
        except Exception as exc:  # noqa: BLE001
            return self._result(info, ExtractStatus.FAILED,
                                message=f"ISO 解析失败：{exc}", started=started)
        finally:
            reader.close()

        note = f"解出 {len(written)} 个文件"
        if not reader.joliet:
            note += "（非 Joliet，文件名可能为 8.3 短名）"
        return self._result(info, ExtractStatus.SUCCESS, files=written,
                            message=note, started=started)
