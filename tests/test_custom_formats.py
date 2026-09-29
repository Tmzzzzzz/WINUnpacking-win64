"""自研解析器测试：原生 CAB（NONE / MSZIP / 多块）与原生 ISO9660。"""
from __future__ import annotations

import samples

from unpacker.extractors.cab import _FolderReader, _parse
from unpacker.models import ArchiveKind, ExtractStatus


# ==================================================================== CAB
def test_cab_none(workdir):
    out = workdir / "out"
    samples.write(workdir / "a.cab", samples.cab_bytes(
        [("hello.txt", b"cab payload"), ("sub/deep.bin", b"\xff" * 100)]))

    res = samples.run_extract(workdir / "a.cab", out)

    assert res.status is ExtractStatus.SUCCESS, res.message
    assert res.kind is ArchiveKind.CAB
    assert (out / "hello.txt").read_bytes() == b"cab payload"
    assert (out / "sub" / "deep.bin").read_bytes() == b"\xff" * 100


def test_cab_mszip(workdir):
    out = workdir / "out"
    payload = bytes(range(256)) * 40
    samples.write(workdir / "m.cab", samples.cab_bytes(
        [("data.bin", payload)], compression="mszip"))

    res = samples.run_extract(workdir / "m.cab", out)

    assert res.status is ExtractStatus.SUCCESS, res.message
    assert (out / "data.bin").read_bytes() == payload


def test_cab_multiblock_crossing_files(workdir):
    """一个文件跨多个 CFDATA 块时必须按块拼接正确。"""
    out = workdir / "out"
    big = bytes((i * 7) % 251 for i in range(10000))
    small = b"tail"
    samples.write(workdir / "b.cab", samples.cab_bytes(
        [("big.bin", big), ("small.txt", small)],
        compression="mszip", block_size=4096))

    res = samples.run_extract(workdir / "b.cab", out)

    assert res.status is ExtractStatus.SUCCESS, res.message
    assert (out / "big.bin").read_bytes() == big
    assert (out / "small.txt").read_bytes() == small


def test_cab_folder_reader_maps_offsets(workdir):
    """直接验证块偏移映射：按块流式读取的结果必须与整段解压一致。"""
    data = bytes((i * 13) % 256 for i in range(9000))
    samples.write(workdir / "r.cab", samples.cab_bytes(
        [("x.bin", data)], compression="mszip", block_size=2048))

    raw = (workdir / "r.cab").read_bytes()
    cab = _parse(raw)
    reader = _FolderReader(raw, cab.folders[0])
    assert reader.total == len(data)

    for start, length in ((0, 1), (0, len(data)), (2047, 5), (5000, 4000),
                          (len(data) - 3, 3)):
        got = b"".join(chunk for _off, chunk in reader.iter_range(start, length))
        assert got == data[start:start + length], (start, length)


def test_cab_gbk_member_name(workdir):
    out = workdir / "out"
    samples.write(workdir / "g.cab", samples.cab_bytes(
        [("中文.txt", b"gbk name")]))

    res = samples.run_extract(workdir / "g.cab", out)

    assert res.status is ExtractStatus.SUCCESS, res.message
    assert (out / "中文.txt").read_bytes() == b"gbk name"


def test_cab_lzx_is_delegated(workdir):
    """LZX 压缩的 CAB 应由原生解包器返回「不支持」，交给兜底链而非伪成功。"""
    from unpacker.context import ExtractContext
    from unpacker.extractors.cab import CabExtractor
    from unpacker.models import ArchiveInfo

    raw = bytearray(samples.cab_bytes([("a.txt", b"data")]))
    # CFFOLDER 的 typeCompress 位于 36+6；改成 3 (LZX)
    raw[42:44] = (3).to_bytes(2, "little")
    src = samples.write(workdir / "lzx.cab", bytes(raw))

    out = workdir / "out"
    out.mkdir()
    info = ArchiveInfo(path=src, kind=ArchiveKind.CAB, size=len(raw))
    res = CabExtractor().extract(info, ExtractContext(output_dir=out))

    assert res.status is ExtractStatus.UNSUPPORTED
    assert "LZX" in res.message
    assert not (out / "a.txt").exists()


# ================================================================== ISO9660
def test_iso_basic(workdir):
    out = workdir / "out"
    samples.write(workdir / "a.iso", samples.iso_bytes(
        [("README.TXT", b"iso file content")]))

    res = samples.run_extract(workdir / "a.iso", out)

    assert res.status is ExtractStatus.SUCCESS, res.message
    assert res.kind is ArchiveKind.ISO
    assert (out / "README.TXT").read_bytes() == b"iso file content"


def test_iso_multiple_files_and_large_payload(workdir):
    out = workdir / "out"
    big = b"Z" * 5000          # 跨多个扇区
    samples.write(workdir / "b.iso", samples.iso_bytes(
        [("A.BIN", big), ("B.DAT", b"small")]))

    res = samples.run_extract(workdir / "b.iso", out)

    assert res.status is ExtractStatus.SUCCESS, res.message
    assert (out / "A.BIN").read_bytes() == big
    assert (out / "B.DAT").read_bytes() == b"small"


def test_iso_large_payload_is_streamed(workdir):
    """ISO 文件内容应分块从镜像读取，而不是整体载入内存。"""
    import tracemalloc

    out = workdir / "out"
    big = b"\x00" * (16 << 20)
    samples.write(workdir / "big.iso", samples.iso_bytes([("BIG.BIN", big)]))

    tracemalloc.start()
    res = samples.run_extract(workdir / "big.iso", out)
    _cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert res.status is ExtractStatus.SUCCESS, res.message
    assert (out / "BIG.BIN").stat().st_size == len(big)
    assert peak < 8 << 20, f"峰值内存 {peak} 字节，说明镜像内容被整体载入了内存"
