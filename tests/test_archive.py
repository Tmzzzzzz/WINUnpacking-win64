"""通用压缩包解包测试：ZIP / TAR / 单流压缩 / 冲突策略 / 安全与编码回归。"""
from __future__ import annotations

import os
import time
import tracemalloc

import pytest
import samples

from unpacker.context import ExtractContext
from unpacker.engine import EngineOptions
from unpacker.extractors.archive import ZipExtractor
from unpacker.models import ConflictPolicy, ExtractStatus
from unpacker.utils import decode_member_name, recode_zip_name, safe_join, sanitize_member


# ==================================================================== ZIP
def test_zip_basic(workdir):
    out = workdir / "out"
    samples.write_zip(workdir / "a.zip", [("docs/readme.txt", b"hello"),
                                          ("logo.bin", b"\x00\x01\x02")])
    res = samples.run_extract(workdir / "a.zip", out)

    assert res.status is ExtractStatus.SUCCESS
    assert res.kind.value == "zip"
    assert (out / "docs" / "readme.txt").read_bytes() == b"hello"
    assert (out / "logo.bin").read_bytes() == b"\x00\x01\x02"
    assert res.bytes_written == 8


def test_zip_deflated_large_member(workdir):
    out = workdir / "out"
    payload = b"abcdefgh" * 4096
    samples.write_zip(workdir / "c.zip", [("big.bin", payload)], compress=True)
    res = samples.run_extract(workdir / "c.zip", out)
    assert res.status is ExtractStatus.SUCCESS
    assert (out / "big.bin").read_bytes() == payload


def test_zip_member_is_streamed_not_buffered(workdir):
    """回归：大成员必须分块落盘，峰值内存远小于成员体积。"""
    out = workdir / "out"
    payload = b"\x00" * (24 << 20)          # 24 MiB
    samples.write_zip(workdir / "big.zip", [("big.bin", payload)], compress=True)

    tracemalloc.start()
    res = samples.run_extract(workdir / "big.zip", out)
    _cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert res.status is ExtractStatus.SUCCESS
    assert (out / "big.bin").stat().st_size == len(payload)
    assert peak < 8 << 20, f"峰值内存 {peak} 字节，说明成员被整体载入了内存"


def test_zip_relative_output_dir(workdir, monkeypatch):
    """回归：相对输出目录曾让 safe_join 的绝对路径与 relative_to 冲突而整体失败。"""
    monkeypatch.chdir(workdir)
    samples.write_zip(workdir / "r.zip", [("d/x.txt", b"hi")])

    res = samples.run_extract("r.zip", "out_rel")

    assert res.status is ExtractStatus.SUCCESS
    assert (workdir / "out_rel" / "d" / "x.txt").read_bytes() == b"hi"


def test_zip_slip_is_neutralized(workdir):
    out = workdir / "out"
    samples.write_zip(workdir / "evil.zip", [("../evil.txt", b"pwn"),
                                             ("a/../../evil2.txt", b"pwn")])
    res = samples.run_extract(workdir / "evil.zip", out)

    assert res.status is ExtractStatus.SUCCESS
    # 任何文件都不得逃出输出目录
    assert not (workdir / "evil.txt").exists()
    assert not (workdir.parent / "evil.txt").exists()
    for p in out.rglob("*.txt"):
        assert out.resolve() in p.resolve().parents


def test_safe_join_and_sanitize_never_escape(tmp_path):
    for evil in ["../a", "../../a", "a/../../b", "/etc/passwd",
                 "C:/windows/x", "..\\..\\x", "....//x"]:
        cleaned = sanitize_member(evil)
        assert ".." not in cleaned.split("/")
        target = safe_join(tmp_path, evil)
        assert tmp_path.resolve() in target.parents or target == tmp_path.resolve()


# ============================================================ 文件名编码回退
def test_decode_member_name_gbk():
    raw = "测试文件.txt".encode("gbk")
    assert decode_member_name(raw, "auto") == "测试文件.txt"


def test_decode_member_name_respects_explicit_encoding():
    raw = "測試.txt".encode("big5")
    assert decode_member_name(raw, "big5") == "測試.txt"


def test_recode_zip_name_roundtrip():
    raw = "中文名/子目录.bin".encode("gbk")
    as_cp437 = raw.decode("cp437")            # 模拟标准库的错误解码结果
    assert recode_zip_name(as_cp437, is_utf8=False) == "中文名/子目录.bin"


def test_recode_zip_name_keeps_western_and_ascii():
    assert recode_zip_name("plain.txt", is_utf8=False) == "plain.txt"
    assert recode_zip_name("caf\u00e9.txt", is_utf8=False) == "caf\u00e9.txt"
    # 已明确标记 UTF-8 的名字不应被再次改写
    assert recode_zip_name("测试.txt", is_utf8=True) == "测试.txt"


def test_gbk_zip_extracts_with_correct_names(workdir):
    out = workdir / "out"
    samples.write_zip(workdir / "gbk.zip",
                      [("中文目录/说明.txt", "内容".encode("utf-8"))],
                      gbk_names=True)
    res = samples.run_extract(workdir / "gbk.zip", out)

    assert res.status is ExtractStatus.SUCCESS
    assert (out / "中文目录" / "说明.txt").read_bytes() == "内容".encode("utf-8")


def test_explicit_utf8_encoding_disables_fallback(workdir):
    out = workdir / "out"
    samples.write_zip(workdir / "gbk2.zip", [("中文.txt", b"x")], gbk_names=True)
    samples.run_extract(workdir / "gbk2.zip", out, encoding="utf-8")
    # 强制 UTF-8 时不做回退：文件名会保留标准库的 cp437 解码结果（乱码）
    assert not (out / "中文.txt").exists()
    assert any(p.name != "中文.txt" for p in out.iterdir())


# ================================================================ 加密 ZIP
def test_encrypted_zip_password_table(workdir):
    """回归：命中密码后曾把 attempt 再执行一遍，产生 xxx_1 重复文件。"""
    out = workdir / "out"
    samples.write_zip(workdir / "enc.zip",
                      [("a.txt", b"AAA"), ("b.txt", b"BBB")],
                      password="s3cret")
    res = samples.run_extract(workdir / "enc.zip", out,
                              passwords=["wrong", "s3cret"])

    assert res.status is ExtractStatus.SUCCESS
    assert res.password == "s3cret"
    assert sorted(p.name for p in out.iterdir()) == ["a.txt", "b.txt"]
    assert res.file_count == 2
    assert (out / "a.txt").read_bytes() == b"AAA"


def test_encrypted_zip_wrong_password_reports_need_password(workdir):
    out = workdir / "out"
    samples.write_zip(workdir / "enc2.zip", [("a.txt", b"AAA")], password="right")
    res = samples.run_extract(workdir / "enc2.zip", out, passwords=["nope"])

    assert res.status is ExtractStatus.NEED_PASSWORD
    assert not (out / "a.txt").exists()       # 失败不留半成品


def test_try_passwords_returns_first_success_value_once(workdir):
    """直接锁定 _try_passwords 的契约：attempt 只执行一次并回传产出。"""
    ctx = ExtractContext(output_dir=workdir)
    calls: list[str | None] = []

    def attempt(pwd):
        calls.append(pwd)
        if pwd == "two":
            return ["done"]
        raise RuntimeError("bad password")

    ok, used, value, err = ZipExtractor()._try_passwords(
        ctx, attempt, candidates=[None, "one", "two"])

    assert ok is True and used == "two" and value == ["done"] and err is None
    assert calls == [None, "one", "two"]


# =============================================================== 冲突策略
def _prepare_conflict(workdir, name="f.txt", data=b"new"):
    out = workdir / "out"
    out.mkdir(parents=True, exist_ok=True)
    (out / name).write_bytes(b"old")
    samples.write_zip(workdir / "c.zip", [(name, data)])
    return out


def test_conflict_rename_keeps_both(workdir):
    out = _prepare_conflict(workdir)
    res = samples.run_extract(workdir / "c.zip", out,
                              conflict=ConflictPolicy.RENAME)
    assert res.status is ExtractStatus.SUCCESS
    assert (out / "f.txt").read_bytes() == b"old"
    assert (out / "f_1.txt").read_bytes() == b"new"
    assert res.skipped == 0


def test_conflict_overwrite(workdir):
    out = _prepare_conflict(workdir)
    samples.run_extract(workdir / "c.zip", out, conflict=ConflictPolicy.OVERWRITE)
    assert (out / "f.txt").read_bytes() == b"new"
    assert not (out / "f_1.txt").exists()


def test_conflict_skip(workdir):
    out = _prepare_conflict(workdir)
    res = samples.run_extract(workdir / "c.zip", out, conflict=ConflictPolicy.SKIP)
    assert (out / "f.txt").read_bytes() == b"old"
    assert not (out / "f_1.txt").exists()
    assert res.skipped == 1
    assert "跳过" in res.message


def test_conflict_newer_member_wins(workdir):
    """成员时间（样本固定为 2025-01-01）比现有文件新 -> 覆盖。"""
    out = _prepare_conflict(workdir)
    old = time.mktime((2020, 1, 1, 0, 0, 0, 0, 0, -1))
    os.utime(out / "f.txt", (old, old))

    samples.run_extract(workdir / "c.zip", out, conflict=ConflictPolicy.NEWER)

    assert (out / "f.txt").read_bytes() == b"new"
    assert not (out / "f_1.txt").exists()


def test_conflict_newer_existing_wins(workdir):
    """成员时间比现有文件旧 -> 保留两者。"""
    out = _prepare_conflict(workdir)

    res = samples.run_extract(workdir / "c.zip", out, conflict=ConflictPolicy.NEWER)

    assert (out / "f.txt").read_bytes() == b"old"
    assert (out / "f_1.txt").read_bytes() == b"new"
    assert res.skipped == 0


# ============================================================ TAR / 单流
def test_tar_basic(workdir):
    out = workdir / "out"
    samples.write(workdir / "a.tar", samples.tar_bytes([("p/q.txt", b"tar!")]))
    res = samples.run_extract(workdir / "a.tar", out)
    assert res.status is ExtractStatus.SUCCESS
    assert (out / "p" / "q.txt").read_bytes() == b"tar!"


def test_tar_gz_is_unwrapped(workdir):
    """tgz 应先解单流，再识别内层 TAR 并展开。"""
    out = workdir / "out"
    inner = samples.tar_bytes([("deep/file.txt", b"layered")])
    import gzip
    samples.write(workdir / "a.tar.gz", gzip.compress(inner))

    res = samples.run_extract(workdir / "a.tar.gz", out)

    assert res.status is ExtractStatus.SUCCESS
    assert (out / "deep" / "file.txt").read_bytes() == b"layered"


@pytest.mark.parametrize("kind,ext", [("gz", ".gz"), ("bz2", ".bz2"), ("xz", ".xz")])
def test_single_stream(workdir, kind, ext):
    out = workdir / "out"
    samples.write(workdir / f"data{ext}", samples.stream_bytes([("d", b"streamed")],
                                                               kind=kind))
    res = samples.run_extract(workdir / f"data{ext}", out)

    assert res.status is ExtractStatus.SUCCESS
    assert (out / "data").read_bytes() == b"streamed"


def test_zstd_stream(workdir):
    zstandard = pytest.importorskip("zstandard")
    out = workdir / "out"
    samples.write(workdir / "d.zst", zstandard.ZstdCompressor().compress(b"zstd!"))
    res = samples.run_extract(workdir / "d.zst", out)
    assert res.status is ExtractStatus.SUCCESS
    assert (out / "d").read_bytes() == b"zstd!"


# ================================================================ 试运行
def test_dry_run_writes_nothing(workdir):
    out = workdir / "out"
    samples.write_zip(workdir / "a.zip", [("x.txt", b"data")])
    res = samples.run_extract(workdir / "a.zip", out, dry_run=True)

    assert res.status is ExtractStatus.DRY_RUN
    assert not out.exists()            # 连输出目录都不创建
    assert res.bytes_written == 0
    assert "将按" in res.message


# ================================================================ 选项对象
def test_engine_options_conflict_applies_to_context(workdir):
    opts = EngineOptions(output_dir=workdir, conflict=ConflictPolicy.SKIP,
                         encoding="gbk")
    ctx = opts.context(workdir)
    assert ctx.conflict is ConflictPolicy.SKIP
    assert ctx.overwrite is False
    assert ctx.encoding == "gbk"

    ctx2 = EngineOptions(output_dir=workdir,
                         conflict=ConflictPolicy.OVERWRITE).context(workdir)
    assert ctx2.overwrite is True        # 兼容旧写法
