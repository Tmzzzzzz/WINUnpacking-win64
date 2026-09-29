"""内嵌归档雕刻器（Embedded Archive Carver）。

许多游戏资源包 / 自解压安装包并非标准容器，而是把 ZIP/CAB/7z 等归档
直接拼接在文件尾部或中间。本模块按魔数在文件中定位这些内嵌归档，
切割出临时文件后交给对应的原生解包器提取。

切片全程流式处理：内存占用为 O(块大小)，与内嵌归档体积无关。
"""
from __future__ import annotations

import bz2
import dataclasses
import gzip
import lzma
import os
import time
from pathlib import Path

from ..context import ExtractContext
from ..models import ArchiveInfo, ArchiveKind, ExtractStatus
from ..utils import human_size, strip_long

# (signature, kind)  —— 排序时会优先匹配更长的签名
_SIGS: list[tuple[bytes, ArchiveKind]] = [
    (b"Rar!\x1a\x07\x01\x00", ArchiveKind.RAR),
    (b"Rar!\x1a\x07\x00", ArchiveKind.RAR),
    (b"7z\xbc\xaf\x27\x1c", ArchiveKind.SEVENZIP),
    (b"PK\x03\x04", ArchiveKind.ZIP),
    (b"\xfd7zXZ\x00", ArchiveKind.XZ),
    (b"\x28\xb5\x2f\xfd", ArchiveKind.ZSTD),
    (b"\x1f\x8b\x08", ArchiveKind.GZIP),
    (b"MSCF", ArchiveKind.CAB),
    (b"BZh", ArchiveKind.BZIP2),
]

_KIND_TO_EXTRACTOR = {
    ArchiveKind.ZIP: "原生 ZIP",
    ArchiveKind.SEVENZIP: "py7zr",
    ArchiveKind.RAR: "rarfile",
    ArchiveKind.CAB: "原生 CAB",
    ArchiveKind.XZ: "单流解压",
    ArchiveKind.LZMA: "单流解压",
    ArchiveKind.ZSTD: "单流解压",
    ArchiveKind.GZIP: "单流解压",
    ArchiveKind.BZIP2: "单流解压",
}

_CHUNK = 4 << 20
_MAX_SLICE = 512 << 20   # 单个内嵌归档切片上限 512 MiB
_MAX_HITS = 32
_HEAD_PROBE = 64
#: ZIP EOCD 的最大回看距离：注释最长 65535 字节 + 22 字节固定结构
_TAIL_PROBE = 128 << 10

_SINGLE_STREAM = (ArchiveKind.GZIP, ArchiveKind.BZIP2, ArchiveKind.XZ, ArchiveKind.ZSTD)


def _scan_offsets(path: Path, min_size: int, file_size: int | None = None
                  ) -> list[tuple[int, ArchiveKind, int]]:
    """分块扫描文件，返回 [(offset, kind, sig_len)]。

    扫描按 4MiB 分块、9 条签名各走一次 ``bytes.find``（memchr 快速路径），
    内存恒定；不采用「稀疏采样」是因为大文件中段可能内嵌归档，
    采样会漏检，而扫描本身并不是瓶颈。
    """
    hits: list[tuple[int, ArchiveKind, int]] = []
    overlap = 32
    with open(strip_long(str(path)), "rb") as fh:
        pos = 0
        tail = b""
        while True:
            chunk = fh.read(_CHUNK)
            if not chunk:
                break
            buf = tail + chunk
            base = pos - len(tail)
            for sig, kind in _SIGS:
                start = 0
                while True:
                    idx = buf.find(sig, start)
                    if idx < 0:
                        break
                    hits.append((base + idx, kind, len(sig)))
                    start = idx + 1
            tail = buf[-overlap:]
            pos += len(chunk)

    # 同偏移去重：保留最长签名；再按偏移排序
    best: dict[int, tuple[ArchiveKind, int]] = {}
    for off, kind, ln in hits:
        if off < 0:
            continue
        cur = best.get(off)
        if cur is None or ln > cur[1]:
            best[off] = (kind, ln)

    ordered = sorted((off, k, ln) for off, (k, ln) in best.items())
    # 丢弃落在同一归档内部的伪命中：相邻命中间距过小则跳过
    cleaned: list[tuple[int, ArchiveKind, int]] = []
    for off, kind, ln in ordered:
        if cleaned and off - cleaned[-1][0] < 8:
            continue
        cleaned.append((off, kind, ln))

    del min_size, file_size  # 尺寸校验交由真实解包结果判定
    return cleaned[:_MAX_HITS]


def _copy_slice(src_path: Path, offset: int, length: int, dst_path: Path,
                ctx: ExtractContext) -> tuple[bytes, bytes]:
    """把 [offset, offset+length) 流式写入 dst_path，返回 (头部探针, 尾部探针)。"""
    head = b""
    tail = b""
    remaining = length
    with open(strip_long(str(src_path)), "rb") as fsrc, \
            open(strip_long(str(dst_path)), "wb") as fdst:
        fsrc.seek(offset)
        while remaining > 0:
            ctx.check_cancel()
            block = fsrc.read(min(_CHUNK, remaining))
            if not block:
                break
            fdst.write(block)
            remaining -= len(block)
            if len(head) < _HEAD_PROBE:
                head = (head + block)[:_HEAD_PROBE]
            tail = (tail + block)[-_TAIL_PROBE:]
    return head, tail


def _looks_valid(kind: ArchiveKind, tmp_file: Path, total: int,
                 head: bytes, tail: bytes) -> bool:
    """切片是否真的像一个完整归档（用于过滤误报签名）。"""
    if total < 32:
        return False
    if kind is ArchiveKind.ZIP:
        return tail.rfind(b"PK\x05\x06") >= 0      # 必须存在 EOCD（必落在尾部 128KiB 内）
    if kind is ArchiveKind.CAB:
        return head[:4] == b"MSCF" and total >= 40
    if kind is ArchiveKind.RAR:
        return head[:2] == b"Ra" and total >= 20
    if kind is ArchiveKind.SEVENZIP:
        return total >= 32
    if kind in _SINGLE_STREAM:
        # 单流压缩：直接对切片文件试解首字节（不把内容读进内存）
        try:
            path = strip_long(str(tmp_file))
            if kind is ArchiveKind.GZIP:
                fh = gzip.open(path, "rb")
            elif kind is ArchiveKind.BZIP2:
                fh = bz2.open(path, "rb")
            elif kind is ArchiveKind.XZ:
                fh = lzma.open(path, "rb")
            else:
                import zstandard
                fh = zstandard.ZstdDecompressor().stream_reader(open(path, "rb"))
            with fh:
                fh.read(1)
        except Exception:  # noqa: BLE001 - 解不动就是误报
            return False
        return True
    return True


def _archive_end(kind: ArchiveKind, tmp_file: Path, offset: int, total: int,
                 tail: bytes) -> int | None:
    """能精确算出归档结束位置时返回**绝对偏移**，并顺带截断临时切片。

    ZIP 归档的每个成员都带有 PK\\x03\\x04 局部头，若不排除区间，会把归档中段
    误判为独立归档，切出错位数据导致解包失败（这正是开发中 Errno 22 的根因）。
    """
    if kind is ArchiveKind.ZIP:
        idx = tail.rfind(b"PK\x05\x06")
        if idx < 0:
            return None
        if idx + 22 > len(tail):
            return None
        comment_len = int.from_bytes(tail[idx + 20:idx + 22], "little")
        end_in_tail = idx + 22 + comment_len
        if end_in_tail > len(tail):
            return None
        end_in_slice = total - len(tail) + end_in_tail
        if 0 < end_in_slice < total:
            try:
                os.truncate(strip_long(str(tmp_file)), end_in_slice)
            except OSError:
                pass
        return offset + end_in_slice
    if kind is ArchiveKind.CAB and total >= 36:
        with open(strip_long(str(tmp_file)), "rb") as fh:
            head36 = fh.read(36)
        if len(head36) >= 12:
            cb_total = int.from_bytes(head36[8:12], "little")
            if 0 < cb_total <= total:
                if cb_total < total:
                    try:
                        os.truncate(strip_long(str(tmp_file)), cb_total)
                    except OSError:
                        pass
                return offset + cb_total
    return None


def carve_embedded(info: ArchiveInfo, ctx: ExtractContext,
                   out_root: Path, *, extractor_names: dict | None = None,
                   require_full_coverage: bool = False) -> list[str]:
    """扫描 info.path 中内嵌的归档并逐一提取，返回产出的文件清单。"""
    from .. import registry

    mapping = extractor_names or _KIND_TO_EXTRACTOR
    hits = _scan_offsets(Path(info.path), ctx.carve_min_size, info.size)
    if not hits:
        return []

    tmp_dir = Path(out_root) / ".carve_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    produced: list[str] = []
    file_size = info.size or Path(info.path).stat().st_size
    covered: list[tuple[int, int]] = []   # 已识别归档占据的字节区间

    for off, kind, _ln in hits:
        ctx.check_cancel()

        if any(start <= off < end for start, end in covered):
            ctx.info(f"跳过嵌套命中：{kind.label} @ 0x{off:X}（位于已识别归档内部）")
            continue

        extractor_name = mapping.get(kind)
        extractor = registry.get(extractor_name) if extractor_name else None
        if extractor is None:
            continue

        slice_len = min(file_size - off, _MAX_SLICE)
        if slice_len <= 0:
            continue

        tmp_file = tmp_dir / f"carve_{off:08x}.{kind.value}"
        head, tail = _copy_slice(Path(info.path), off, slice_len, tmp_file, ctx)

        if not _looks_valid(kind, tmp_file, slice_len, head, tail):
            ctx.info(f"跳过疑似误报：{kind.label} @ 0x{off:X}")
            try:
                tmp_file.unlink()
            except OSError:
                pass
            continue

        end = _archive_end(kind, tmp_file, off, slice_len, tail)
        if end:
            covered.append((off, end))
            slice_len = end - off

        sub_dir = Path(out_root) / f"carved_{off:08x}_{kind.value}"
        sub_ctx = dataclasses.replace(ctx, output_dir=sub_dir)
        sub_info = ArchiveInfo(path=tmp_file, kind=kind, size=slice_len)

        ctx.info(f"发现内嵌 {kind.label} @ 0x{off:X}（{human_size(slice_len)}），尝试提取…")
        result = extractor.extract(sub_info, sub_ctx)
        # sub_ctx 是 replace() 出来的副本，统计量要显式并回父上下文
        ctx.bytes_written += sub_ctx.bytes_written
        ctx.skipped_files += sub_ctx.skipped_files
        if result.ok and result.files:
            produced.extend(result.files)
            ctx.info(f"  └ 成功，解出 {result.file_count} 个文件 → {sub_dir.name}/")
        else:
            ctx.warn(f"  └ 提取未成功（{result.status.label}）：{result.message}")

    # 清理临时切片
    for f in tmp_dir.glob("carve_*"):
        try:
            f.unlink()
        except OSError:
            pass
    try:
        os.rmdir(tmp_dir)
    except OSError:
        pass

    if require_full_coverage and not produced:
        ctx.warn("未在内嵌数据中提取到任何文件")
    return produced


def carve_as_result(info: ArchiveInfo, ctx: ExtractContext,
                    out_root: Path) -> tuple[ExtractStatus, list[str], str]:
    """供解包器使用的便捷封装，返回 (状态, 文件列表, 说明)。"""
    started = time.perf_counter()
    files = carve_embedded(info, ctx, out_root)
    cost = time.perf_counter() - started
    if files:
        return (ExtractStatus.SUCCESS, files,
                f"雕刻出 {len(files)} 个文件（耗时 {cost:.1f}s）")
    return (ExtractStatus.UNSUPPORTED, [],
            "未在该文件中定位到可识别的内嵌归档（私有格式需专用插件）")
