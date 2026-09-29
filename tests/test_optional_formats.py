"""依赖第三方库的格式测试（7z / zstd）与递归解包。

缺少可选依赖时自动跳过，不会让整个测试集失败。
"""
from __future__ import annotations

import pytest
import samples

from unpacker.models import ConflictPolicy, ExtractStatus

py7zr = pytest.importorskip("py7zr")


def make_7z(path, entries, *, password=None):
    with py7zr.SevenZipFile(path, "w", password=password) as z:
        for name, data in entries:
            z.writestr(data, name)
    return path


# ==================================================================== 7z
def test_7z_plain(workdir):
    out = workdir / "out"
    src = make_7z(workdir / "a.7z", [("x.txt", b"seven"), ("d/y.bin", b"\x01\x02")])

    res = samples.run_extract(src, out)

    assert res.status is ExtractStatus.SUCCESS, res.message
    assert res.kind.value == "7z"
    assert (out / "x.txt").read_bytes() == b"seven"
    assert (out / "d" / "y.bin").read_bytes() == b"\x01\x02"


def test_7z_plain_with_overwrite_fast_path(workdir):
    """覆盖策略走 py7zr 原地解包；其余策略走暂存目录归位。"""
    out = workdir / "out"
    src = make_7z(workdir / "b.7z", [("x.txt", b"seven")])

    res = samples.run_extract(src, out, conflict=ConflictPolicy.OVERWRITE)

    assert res.status is ExtractStatus.SUCCESS, res.message
    assert (out / "x.txt").read_bytes() == b"seven"


def test_7z_encrypted_password_table_without_duplicates(workdir):
    """回归：密码命中后曾二次执行 attempt，产生 x_1.txt 重复文件。"""
    out = workdir / "out"
    src = make_7z(workdir / "enc.7z", [("a.txt", b"AAA"), ("b.txt", b"BBB")],
                  password="s3cret")

    res = samples.run_extract(src, out, passwords=["nope", "s3cret"])

    assert res.status is ExtractStatus.SUCCESS, res.message
    assert res.password == "s3cret"
    assert sorted(p.name for p in out.iterdir()) == ["a.txt", "b.txt"]


def test_7z_encrypted_wrong_password(workdir):
    out = workdir / "out"
    src = make_7z(workdir / "enc2.7z", [("a.txt", b"AAA")], password="right")

    res = samples.run_extract(src, out, passwords=["wrong"])

    assert res.status is ExtractStatus.NEED_PASSWORD
    assert not (out / "a.txt").exists()


def test_7z_staging_cleans_up_and_applies_skip(workdir):
    out = workdir / "out"
    out.mkdir()
    (out / "a.txt").write_bytes(b"existing")
    src = make_7z(workdir / "s.7z", [("a.txt", b"AAA"), ("b.txt", b"BBB")])

    res = samples.run_extract(src, out, conflict=ConflictPolicy.SKIP)

    assert res.status is ExtractStatus.SUCCESS, res.message
    assert (out / "a.txt").read_bytes() == b"existing"      # 跳过，未被覆盖
    assert (out / "b.txt").read_bytes() == b"BBB"           # 其余正常解出
    assert res.skipped == 1
    assert not any(d.name.startswith(".7z_stage") for d in out.iterdir())


def test_7z_staging_rename_keeps_both(workdir):
    out = workdir / "out"
    out.mkdir()
    (out / "a.txt").write_bytes(b"existing")
    src = make_7z(workdir / "r.7z", [("a.txt", b"AAA")])

    res = samples.run_extract(src, out, conflict=ConflictPolicy.RENAME)

    assert res.status is ExtractStatus.SUCCESS, res.message
    assert (out / "a.txt").read_bytes() == b"existing"
    assert (out / "a_1.txt").read_bytes() == b"AAA"
    assert not any(d.name.startswith(".7z_stage") for d in out.iterdir())


# ================================================================ 递归解包
def test_recursive_extraction(workdir):
    """输出目录中残留的压缩包应被继续拆开。"""
    out = workdir / "out"
    inner = samples.zip_bytes([("deep.txt", b"nested!")])
    samples.write_zip(workdir / "outer.zip", [("payload/inner.zip", inner)])

    res = samples.run_extract(workdir / "outer.zip", out,
                              recursive=True, max_depth=3)

    assert res.status is ExtractStatus.SUCCESS, res.message
    hits = [p for p in out.rglob("deep.txt") if p.is_file()]
    assert hits and hits[0].read_bytes() == b"nested!"
    assert any(f.startswith("[递归]") for f in res.files)


def test_recursive_can_be_disabled(workdir):
    out = workdir / "out"
    inner = samples.zip_bytes([("deep.txt", b"nested!")])
    samples.write_zip(workdir / "outer2.zip", [("inner.zip", inner)])

    samples.run_extract(workdir / "outer2.zip", out, recursive=False)

    assert not list(out.rglob("deep.txt"))


# ================================================================ Zstd 流
def test_zstd_single_stream(workdir):
    zstandard = pytest.importorskip("zstandard")
    out = workdir / "out"
    samples.write(workdir / "d.zst", zstandard.ZstdCompressor().compress(b"zstd data"))

    res = samples.run_extract(workdir / "d.zst", out)

    assert res.status is ExtractStatus.SUCCESS, res.message
    assert (out / "d").read_bytes() == b"zstd data"
