"""内嵌归档雕刻（Carver）测试。

重点覆盖开发中踩到的两个坑：
    1. 归档**内部**的 PK 局部头被误判为独立归档（区间排除）；
    2. 切片整体读入内存导致的内存尖峰（改为流式）。
"""
from __future__ import annotations

import tracemalloc

import samples

from unpacker.models import ArchiveKind, ExtractStatus


def test_carve_embedded_zip(workdir):
    out = workdir / "out"
    samples.write(workdir / "game.pak", samples.pak_with_embedded_zip(
        [("assets/a.txt", b"AAA"), ("assets/b.bin", b"BBBB")]))

    res = samples.run_extract(workdir / "game.pak", out)

    assert res.status is ExtractStatus.SUCCESS, res.message
    found = {p.name for p in out.rglob("*") if p.is_file()}
    assert {"a.txt", "b.bin"} <= found


def test_carve_does_not_split_archive_into_pieces(workdir):
    """每个 ZIP 成员都带 PK\\x03\\x04，必须靠区间排除只切出整段归档。"""
    out = workdir / "out"
    entries = [(f"dir{i}/file{i}.txt", bytes([i]) * (200 + i))
               for i in range(6)]
    samples.write(workdir / "many.pak", samples.pak_with_embedded_zip(entries))

    res = samples.run_extract(workdir / "many.pak", out)

    assert res.status is ExtractStatus.SUCCESS, res.message
    carved = [d for d in out.iterdir() if d.is_dir() and d.name.startswith("carved_")]
    assert len(carved) == 1, [d.name for d in carved]
    for name, data in entries:
        assert (carved[0] / name).read_bytes() == data
    # 临时切片目录必须清理干净
    assert not (out / ".carve_tmp").exists()


def test_carve_embedded_cab(workdir):
    out = workdir / "out"
    samples.write(workdir / "setup.pak", samples.pak_with_embedded_cab(
        [("payload.txt", b"from cab")]))

    res = samples.run_extract(workdir / "setup.pak", out)

    assert res.status is ExtractStatus.SUCCESS, res.message
    hits = [p for p in out.rglob("payload.txt") if p.is_file()]
    assert len(hits) == 1
    assert hits[0].read_bytes() == b"from cab"


def test_carve_slice_is_streamed(workdir):
    """回归：切片曾整体读入内存（最大 512MiB），现须流式复制。"""
    out = workdir / "out"
    payload = b"\x00" * (32 << 20)
    samples.write(workdir / "huge.pak", samples.pak_with_embedded_zip(
        [("big.bin", payload)], compress=True))

    tracemalloc.start()
    res = samples.run_extract(workdir / "huge.pak", out)
    _cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert res.status is ExtractStatus.SUCCESS, res.message
    hits = [p for p in out.rglob("big.bin") if p.is_file()]
    assert len(hits) == 1 and hits[0].stat().st_size == len(payload)
    assert peak < 16 << 20, f"峰值内存 {peak} 字节，切片疑似被整体读入内存"


def test_carve_bytes_are_counted(workdir):
    """回归：雕刻走的是 replace() 出来的子上下文，统计量必须并回父上下文。"""
    out = workdir / "out"
    samples.write(workdir / "g.pak", samples.pak_with_embedded_zip(
        [("a.bin", b"12345"), ("b.bin", b"67")]))

    res = samples.run_extract(workdir / "g.pak", out)

    assert res.status is ExtractStatus.SUCCESS, res.message
    assert res.bytes_written == 7


def test_pak_without_embedded_archive_is_unsupported(workdir):
    out = workdir / "out"
    samples.write(workdir / "plain.pak", b"NOT-AN-ARCHIVE" * 64)

    res = samples.run_extract(workdir / "plain.pak", out)

    assert res.status is ExtractStatus.UNSUPPORTED
    assert res.kind is ArchiveKind.GAME_PACK
    assert "插件" in res.message
