"""测试样本构造器：全部在代码里生成，仓库不存放任何二进制夹具。

覆盖 ZIP（含 ZipCrypto 加密、GBK 名、路径穿越）、TAR、单流压缩、
CAB（NONE / MSZIP / 多块）、ISO9660，以及内嵌归档雕刻用的伪造资源包。
"""
from __future__ import annotations

import io
import os
import struct
import tarfile
import zlib
from pathlib import Path

# ============================================================ 引擎便捷入口
def run_extract(src, out, **opts):
    """按「合并到同一目录」的方式解包一个文件，返回 ExtractResult。"""
    from unpacker.engine import Engine, EngineOptions

    opts.setdefault("merge_output", True)
    return Engine(EngineOptions(output_dir=Path(out), **opts)).extract(Path(src))


def write(path, data: bytes) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


# ================================================================ ZIP
def _gen_crc_table() -> list[int]:
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = (0xEDB88320 ^ (crc >> 1)) if (crc & 1) else (crc >> 1)
        table.append(crc & 0xFFFFFFFF)
    return table


_CRC_TABLE = _gen_crc_table()


def _crc32_raw(crc: int, byte: int) -> int:
    """PKZIP 规范里的 CRC32(c, b) —— 裸寄存器更新。

    注意**不能**用 zlib.crc32 代替：后者在进入/返回时各做一次按位取反，
    与 ZipCrypto 密钥调度所要求的原始更新不是同一个运算。
    """
    return ((crc >> 8) ^ _CRC_TABLE[(crc ^ byte) & 0xFF]) & 0xFFFFFFFF


def _zipcrypto_keys(password: bytes) -> list[int]:
    keys = [0x12345678, 0x23456789, 0x34567890]
    for ch in password:
        _zipcrypto_update(keys, ch)
    return keys


def _zipcrypto_update(keys: list[int], byte: int) -> None:
    keys[0] = _crc32_raw(keys[0], byte)
    keys[1] = (keys[1] + (keys[0] & 0xFF)) & 0xFFFFFFFF
    keys[1] = (keys[1] * 134775813 + 1) & 0xFFFFFFFF
    keys[2] = _crc32_raw(keys[2], (keys[1] >> 24) & 0xFF)


def _zipcrypto_crypt(keys: list[int], data: bytes) -> bytes:
    """ZipCrypto 加解密同一套运算（对称）。"""
    out = bytearray()
    for ch in data:
        temp = (keys[2] | 2) & 0xFFFF
        k = ((temp * (temp ^ 1)) >> 8) & 0xFF
        out.append(ch ^ k)
        _zipcrypto_update(keys, ch)
    return bytes(out)


#: 成员时间固定为 2025-01-01 00:00:00（DOS 格式），便于确定性测试 NEWER 策略
_DOS_TIME = 0x0000
_DOS_DATE = ((2025 - 1980) << 9) | (1 << 5) | 1


def zip_bytes(entries, *, password: str | None = None, compress: bool = False,
              gbk_names: bool = False, utf8_flag: bool = True) -> bytes:
    """手写 ZIP 生成器。

    需要自己写的原因是标准库 zipfile 既不能写加密 ZIP，也不能写
    非法/非 UTF-8 的原始成员名（GBK 乱码场景需要精确复现）。
    """
    pwd = password.encode("utf-8") if isinstance(password, str) else password
    body = bytearray()
    central = bytearray()

    for name, data in entries:
        if isinstance(name, bytes):
            name_bytes = name
            marker_utf8 = False
        elif gbk_names:
            name_bytes = name.encode("gbk")
            marker_utf8 = False
        else:
            name_bytes = name.encode("utf-8")
            marker_utf8 = utf8_flag and not name.isascii()

        crc = zlib.crc32(data) & 0xFFFFFFFF
        if compress:
            co = zlib.compressobj(6, zlib.DEFLATED, -15)
            payload = co.compress(data) + co.flush()
            method = 8
        else:
            payload = data
            method = 0

        flags = 0x0800 if marker_utf8 else 0
        offset = len(body)

        if pwd is not None:
            flags |= 0x0001
            keys = _zipcrypto_keys(pwd)
            # 12 字节加密头：末字节必须是未压缩数据 CRC 的高字节
            header = os.urandom(11) + bytes([(crc >> 24) & 0xFF])
            blob = _zipcrypto_crypt(keys, header) + _zipcrypto_crypt(keys, payload)
        else:
            blob = payload

        body += struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, flags, method,
                            _DOS_TIME, _DOS_DATE, crc, len(blob), len(data),
                            len(name_bytes), 0)
        body += name_bytes
        body += blob

        central += struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 20, 20, flags,
                               method, _DOS_TIME, _DOS_DATE, crc, len(blob), len(data),
                               len(name_bytes), 0, 0, 0, 0, 0, offset)
        central += name_bytes

    cd_offset = len(body)
    body += central
    body += struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, len(entries), len(entries),
                        len(central), cd_offset, 0)
    return bytes(body)


def write_zip(path, entries, **kwargs) -> Path:
    return write(path, zip_bytes(entries, **kwargs))


# ================================================================ TAR / 单流
def tar_bytes(entries, *, mode: str = "w") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode=mode, format=tarfile.GNU_FORMAT) as tf:
        for name, data in entries:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = 1700000000
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def stream_bytes(entries, kind: str = "gz") -> bytes:
    """把多份数据串接后做单流压缩（内容形如 name\\x00data，便于断言）。"""
    raw = b"".join(d for _n, d in entries)
    if kind == "gz":
        import gzip
        return gzip.compress(raw)
    if kind == "bz2":
        import bz2
        return bz2.compress(raw)
    if kind == "xz":
        import lzma
        return lzma.compress(raw)
    if kind == "zst":
        import zstandard
        return zstandard.ZstdCompressor().compress(raw)
    raise ValueError(kind)


# ================================================================ CAB
def cab_bytes(entries, *, compression: str = "none", block_size: int = 32768) -> bytes:
    """手写 CAB 生成器（单 folder）。

    ``block_size`` 调小即可构造多 CFDATA 块的样本，用于验证按块流式解压。
    """
    stream = b"".join(d for _n, d in entries)
    offsets: list[int] = []
    pos = 0
    for _n, d in entries:
        offsets.append(pos)
        pos += len(d)

    blocks: list[tuple[bytes, int]] = []
    for i in range(0, len(stream), block_size):
        raw = stream[i:i + block_size]
        if compression == "mszip":
            co = zlib.compressobj(6, zlib.DEFLATED, -15)
            payload = b"CK" + co.compress(raw) + co.flush()
        else:
            payload = raw
        blocks.append((payload, len(raw)))

    n_folders = 1 if entries else 0
    files_tbl = bytearray()
    for (name, data), off in zip(entries, offsets):
        files_tbl += struct.pack("<IIHHHH", len(data), off, 0,
                                 0x5021, 0x0000, 0x20)   # 2025-01-01 00:00:00
        # CAB 规范里成员名使用系统代码页；非 ASCII 用 GBK 复现国产包场景
        files_tbl += (name.encode("gbk") if not name.isascii()
                      else name.encode("ascii")) + b"\x00"

    coff_files = 36 + 8 * n_folders
    coff_cab_start = coff_files + len(files_tbl)
    folder = struct.pack("<IHH", coff_cab_start, len(blocks),
                         0 if compression == "none" else 1)

    data_region = bytearray()
    for payload, uncomp in blocks:
        data_region += struct.pack("<IHH", 0, len(payload), uncomp) + payload

    total = coff_cab_start + len(data_region)
    header = struct.pack("<4sIIIIIBBHHHHH", b"MSCF", 0, total, 0, coff_files, 0,
                         3, 1, n_folders, len(entries), 0, 0x1234, 0)
    return bytes(header) + bytes(folder) + bytes(files_tbl) + bytes(data_region)


# ================================================================ ISO9660
def iso_bytes(entries, *, volume_id: str = "TESTVOL") -> bytes:
    """手写 ISO9660（只含 PVD，不含 Joliet）生成器。"""
    sector = 2048
    root_lba = 18
    data_lba = 19

    def both32(v: int) -> bytes:
        return struct.pack("<I", v) + struct.pack(">I", v)

    def both16(v: int) -> bytes:
        return struct.pack("<H", v) + struct.pack(">H", v)

    def record(extent: int, size: int, flags: int, ident: bytes) -> bytes:
        rec = bytearray()
        rec += b"\x00\x00"                       # 长度占位 / 扩展属性长度
        rec += both32(extent)
        rec += both32(size)
        rec += bytes(7)                          # 录制时间
        rec += bytes([flags, 0, 0])
        rec += both16(1)
        rec += bytes([len(ident)])
        rec += ident
        if len(rec) % 2:
            rec += b"\x00"
        rec[0] = len(rec)
        return bytes(rec)

    files_lba: list[int] = []
    cursor = data_lba
    for _n, data in entries:
        files_lba.append(cursor)
        cursor += max(1, (len(data) + sector - 1) // sector)

    root_recs = bytearray()
    root_recs += record(root_lba, 0, 0x02, b"\x00")            # '.'
    root_recs += record(root_lba, 0, 0x02, b"\x01")            # '..'
    for (name, data), lba in zip(entries, files_lba):
        root_recs += record(lba, len(data), 0x00, f"{name};1".encode("ascii"))
    root_size = len(root_recs)

    root_rec = record(root_lba, root_size, 0x02, b"\x00")

    pvd = bytearray(sector)
    pvd[0] = 1
    pvd[1:6] = b"CD001"
    pvd[6] = 1
    pvd[8:8 + len(volume_id)] = volume_id.encode("ascii")[:32]
    pvd[156:156 + len(root_rec)] = root_rec

    term = bytearray(sector)
    term[0] = 255
    term[1:6] = b"CD001"
    term[6] = 1

    out = bytearray()
    out += bytes(sector) * 16          # 0-15 保留
    out += bytes(pvd)                  # 16 PVD
    out += bytes(term)                 # 17 终止符
    out += bytes(root_recs)            # 18 根目录
    if root_size < sector:
        out += bytes(sector - root_size)
    for _n, data in entries:            # 19+ 文件内容
        out += data
        out += bytes((-len(data)) % sector)
    return bytes(out)


# ================================================================ 内嵌归档
def pak_with_embedded_zip(entries, *, prefix: bytes = b"GAMEPAK\x00",
                          suffix: bytes = b"\x00" * 64, **zip_kwargs) -> bytes:
    """伪造资源包：头部私有标识 + 内嵌 ZIP + 尾部填充。

    用来验证雕刻器能定位内嵌归档，并对归档**内部**的 PK 局部头做区间排除。
    """
    return prefix + zip_bytes(entries, **zip_kwargs) + suffix


def pak_with_embedded_cab(entries, *, prefix: bytes = b"PAKDATA") -> bytes:
    return prefix + cab_bytes(entries) + b"\x00" * 32
