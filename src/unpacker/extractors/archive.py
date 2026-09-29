"""通用压缩包解包器：ZIP / 7-Zip / RAR / TAR / 单流压缩(gz,bz2,xz,lzma,zstd)。

落盘统一走 BaseExtractor 的分块助手，大成员不会整体读入内存；
成员名统一经 ``recode_zip_name`` / ``ctx.encoding`` 处理，兼容 GBK 命名的中文包。
"""
from __future__ import annotations

import datetime as _dt
import shutil
import struct
import tarfile
import time
import zipfile
from pathlib import Path
from typing import Optional

from ..context import ExtractContext
from ..models import ArchiveInfo, ArchiveKind, ConflictPolicy, ExtractResult, ExtractStatus
from ..registry import register
from ..utils import recode_zip_name, safe_join, sanitize_member, strip_long
from .base import CHUNK, BaseExtractor

# 单流压缩的扩展名 -> 去后缀后的默认输出名
_STREAM_SUFFIXES = {
    ".tgz": ".tar", ".tbz2": ".tar", ".tbz": ".tar", ".txz": ".tar",
    ".gz": "", ".bz2": "", ".xz": "", ".zst": "", ".lzma": "",
}

#: 单流解压时进度上报的间隔（每写入这么多字节报一次）
_PROGRESS_EVERY = 1 << 20


def _is_tar_header(buf: bytes) -> bool:
    return len(buf) >= 263 and buf[257:262] == b"ustar"


def _zip_is_aes(zi: zipfile.ZipInfo) -> bool:
    """检测 WinZip AES（extra field 0x9901），标准库不支持。"""
    if zi.compress_type == 99:
        return True
    extra, i = zi.extra, 0
    while i + 4 <= len(extra):
        hid, hsize = struct.unpack_from("<HH", extra, i)
        if hid == 0x9901:
            return True
        i += 4 + hsize
    return False


def _mtime_of(value) -> Optional[float]:
    """把各种「时间」表示统一成 epoch 秒（供 NEWER 冲突策略使用）。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, _dt.datetime):
        try:
            return value.timestamp()
        except (OSError, ValueError, OverflowError):
            return None
    if isinstance(value, tuple) and len(value) >= 6:
        try:
            return time.mktime((*value[:6], 0, 0, -1))
        except (ValueError, OverflowError, OSError):
            return None
    return None


# --------------------------------------------------------------------- ZIP
@register
class ZipExtractor(BaseExtractor):
    name = "原生 ZIP"
    kinds = (ArchiveKind.ZIP,)
    priority = 50

    def extract(self, info: ArchiveInfo, ctx: ExtractContext) -> ExtractResult:
        started = time.perf_counter()
        out = self._prepare_dir(ctx)

        try:
            with zipfile.ZipFile(info.path) as zf:
                infos = zf.infolist()
        except zipfile.BadZipFile as exc:
            return self._result(info, ExtractStatus.FAILED,
                                message=f"ZIP 结构损坏：{exc}", started=started)
        except Exception as exc:  # noqa: BLE001
            return self._result(info, ExtractStatus.FAILED,
                                message=f"打开失败：{exc}", started=started)

        if any(_zip_is_aes(zi) for zi in infos):
            return self._result(
                info, ExtractStatus.UNSUPPORTED,
                message="WinZip AES 加密，标准库不支持，请改用 7-Zip 后端",
                started=started)

        # 成员名重解码：GBK 命名的包在这里被还原，随后才能正确做路径校验
        names: dict[str, str] = {}
        recoded = 0
        for zi in infos:
            fixed = recode_zip_name(zi.filename, bool(zi.flag_bits & 0x800), ctx.encoding)
            names[zi.filename] = fixed
            if fixed != zi.filename:
                recoded += 1
        if recoded:
            ctx.info(f"成员名编码回退：{recoded} 个名字已按 {ctx.encoding or 'auto'} 重新解码")

        # 路径穿越预检（Zip Slip 防护）
        try:
            for zi in infos:
                safe_join(out, names[zi.filename])
        except ValueError as exc:
            return self._result(info, ExtractStatus.FAILED, message=str(exc), started=started)

        members = [zi for zi in infos if not zi.is_dir()]
        encrypted = any(zi.flag_bits & 0x1 for zi in infos)

        def attempt(pwd: Optional[str]) -> list[str]:
            pwd_bytes = pwd.encode("utf-8") if pwd else None
            written: list[str] = []
            with zipfile.ZipFile(info.path) as zf:
                for idx, zi in enumerate(members, 1):
                    ctx.check_cancel()
                    rel = names[zi.filename]
                    with zf.open(zi, pwd=pwd_bytes) as src:
                        saved = self._copy_stream(ctx, out, rel, src,
                                                  member_mtime=_mtime_of(zi.date_time),
                                                  name=rel)
                    if saved is not None:
                        written.append(saved)
                    ctx.progress(idx, len(members), rel)
            return written

        if not encrypted:
            try:
                files = attempt(None)
            except Exception as exc:  # noqa: BLE001
                return self._result(info, ExtractStatus.FAILED,
                                    message=f"解压失败：{exc}", started=started)
            return self._result(info, ExtractStatus.SUCCESS, files=files,
                                message=f"解出 {len(files)} 个文件", started=started)

        # 加密包：密码表尝试。attempt 成功那次已经写完文件，直接复用其产出
        ok, used, files, err = self._try_passwords(
            ctx, attempt, candidates=[None, *ctx.passwords])
        if ok:
            return self._result(info, ExtractStatus.SUCCESS, files=list(files or []),
                                password=used, started=started,
                                message=f"解出 {len(files or [])} 个文件")
        return self._result(info, ExtractStatus.NEED_PASSWORD,
                            message=f"加密压缩包，密码尝试失败：{err}", started=started)


# ------------------------------------------------------------------ 7-Zip
@register
class SevenZipExtractor(BaseExtractor):
    name = "py7zr"
    kinds = (ArchiveKind.SEVENZIP,)
    priority = 50
    requires = ("py7zr",)

    def extract(self, info: ArchiveInfo, ctx: ExtractContext) -> ExtractResult:
        started = time.perf_counter()
        try:
            import py7zr
        except ImportError:
            return self._result(info, ExtractStatus.UNSUPPORTED,
                                message="未安装 py7zr，无法解包 7z", started=started)

        out = self._prepare_dir(ctx)

        def members_of(z) -> list[str]:
            return [n for n in z.getnames() if not n.endswith("/")]

        def attempt(pwd: Optional[str]) -> list[str]:
            with py7zr.SevenZipFile(info.path, mode="r", password=pwd) as z:
                names = members_of(z)
                for n in names:
                    safe_join(out, n)  # 路径穿越预检
                # 覆盖策略可直接原地解；其余策略需先解到暂存目录再按规则归位
                if ctx.conflict is ConflictPolicy.OVERWRITE:
                    z.extractall(path=str(out))
                    return [str(out.joinpath(*sanitize_member(n).split("/")))
                            for n in names]
                return self._staged_extract(ctx, out, z, names)

        ok, used, files, err = self._try_passwords(
            ctx, attempt, candidates=[None, *ctx.passwords])
        if ok:
            return self._result(info, ExtractStatus.SUCCESS, files=list(files or []),
                                password=used, started=started,
                                message=f"解出 {len(files or [])} 个文件")

        if info.encrypted or "password" in str(err).lower() or "encrypt" in str(err).lower():
            return self._result(info, ExtractStatus.NEED_PASSWORD,
                                message="7z 已加密，未能通过密码表解出", started=started)
        return self._result(info, ExtractStatus.FAILED,
                            message=f"解包失败：{err}", started=started)

    def _staged_extract(self, ctx: ExtractContext, out: Path, z, names: list[str]) -> list[str]:
        """先解到暂存目录，再按冲突策略逐个归位（7z 不支持按成员选择性落盘）。"""
        stage = out / f".7z_stage_{id(z) & 0xFFFFFF:06x}"
        stage.mkdir(parents=True, exist_ok=True)
        written: list[str] = []
        base = Path(out).resolve()
        try:
            z.extractall(path=str(stage))
            for idx, name in enumerate(names, 1):
                ctx.check_cancel()
                src = stage.joinpath(*sanitize_member(name).split("/"))
                if not src.is_file():
                    continue
                dst = self._target(ctx, name, out)
                if dst is None:
                    continue
                try:
                    shutil.move(strip_long(str(src)), strip_long(str(dst)))
                except OSError:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    with open(strip_long(str(src)), "rb") as fin, \
                            open(strip_long(str(dst)), "wb") as fout:
                        shutil.copyfileobj(fin, fout, CHUNK)
                ctx.bytes_written += dst.stat().st_size
                written.append(self._rel(base, dst))
                ctx.progress(idx, len(names), name)
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        return written


# -------------------------------------------------------------------- RAR
@register
class RarExtractor(BaseExtractor):
    name = "rarfile"
    kinds = (ArchiveKind.RAR,)
    priority = 50
    requires = ("rarfile", "unrar")

    def extract(self, info: ArchiveInfo, ctx: ExtractContext) -> ExtractResult:
        started = time.perf_counter()
        try:
            import rarfile
        except ImportError:
            return self._result(info, ExtractStatus.UNSUPPORTED,
                                message="未安装 rarfile", started=started)

        codec = ctx.encoding if ctx.encoding and ctx.encoding.lower() not in ("auto", "") else None
        try:
            try:
                rf = rarfile.RarFile(strip_long(str(info.path)), encoding=codec)
            except TypeError:  # 老版本 rarfile 无 encoding 参数
                rf = rarfile.RarFile(strip_long(str(info.path)))
            members = [m for m in rf.infolist() if not m.isdir()]
        except rarfile.RarCannotExec as exc:
            return self._result(info, ExtractStatus.UNSUPPORTED,
                                message=f"缺少 unrar 后端，已转交外部 7-Zip：{exc}",
                                started=started)
        except Exception as exc:  # noqa: BLE001
            return self._result(info, ExtractStatus.UNSUPPORTED,
                                message=f"rarfile 无法处理（将尝试外部后端）：{exc}",
                                started=started)

        out = self._prepare_dir(ctx)
        try:
            for m in members:
                safe_join(out, m.filename)
        except ValueError as exc:
            return self._result(info, ExtractStatus.FAILED, message=str(exc), started=started)

        def attempt(pwd: Optional[str]) -> list[str]:
            written: list[str] = []
            for idx, m in enumerate(members, 1):
                ctx.check_cancel()
                try:
                    with rf.open(m, pwd=pwd) as src:
                        saved = self._copy_stream(ctx, out, m.filename, src,
                                                  member_mtime=_mtime_of(
                                                      getattr(m, "mtime", None)),
                                                  name=m.filename)
                except Exception as exc:  # noqa: BLE001 - 空文件/特殊成员跳过
                    ctx.warn(f"  └ 跳过成员 {m.filename}：{exc}")
                    continue
                if saved is not None:
                    written.append(saved)
                ctx.progress(idx, len(members), m.filename)
            return written

        ok, used, files, err = self._try_passwords(
            ctx, attempt, candidates=[None, *ctx.passwords])
        if ok:
            return self._result(info, ExtractStatus.SUCCESS, files=list(files or []),
                                password=used, started=started,
                                message=f"解出 {len(files or [])} 个文件")
        if any(m.needs_password() for m in members):
            return self._result(info, ExtractStatus.NEED_PASSWORD,
                                message="RAR 已加密，密码尝试失败", started=started)
        return self._result(info, ExtractStatus.FAILED,
                            message=f"解包失败：{err}", started=started)


# -------------------------------------------------------------------- TAR
@register
class TarExtractor(BaseExtractor):
    name = "原生 TAR"
    kinds = (ArchiveKind.TAR,)
    priority = 50

    def extract(self, info: ArchiveInfo, ctx: ExtractContext) -> ExtractResult:
        started = time.perf_counter()
        out = self._prepare_dir(ctx)
        written: list[str] = []
        try:
            with tarfile.open(strip_long(str(info.path)), mode="r:*") as tf:
                members = [m for m in tf.getmembers() if m.isfile()]
                for m in members:
                    safe_join(out, m.name)
                for idx, m in enumerate(members, 1):
                    ctx.check_cancel()
                    src = tf.extractfile(m)
                    if src is None:
                        continue
                    with src:
                        saved = self._copy_stream(ctx, out, m.name, src,
                                                  member_mtime=_mtime_of(m.mtime),
                                                  name=m.name)
                    if saved is not None:
                        written.append(saved)
                    ctx.progress(idx, len(members), m.name)
        except Exception as exc:  # noqa: BLE001
            return self._result(info, ExtractStatus.FAILED,
                                message=f"TAR 解包失败：{exc}", started=started)
        return self._result(info, ExtractStatus.SUCCESS, files=written,
                            message=f"解出 {len(written)} 个文件", started=started)


# ------------------------------------------------------- 单流压缩 (gz/bz2/xz)
@register
class StreamExtractor(BaseExtractor):
    name = "单流解压"
    kinds = (ArchiveKind.GZIP, ArchiveKind.BZIP2, ArchiveKind.XZ,
             ArchiveKind.LZMA, ArchiveKind.ZSTD)
    priority = 50

    def _open(self, info: ArchiveInfo):
        kind = info.kind
        path = strip_long(str(info.path))
        if kind is ArchiveKind.GZIP:
            import gzip
            return gzip.open(path, "rb")
        if kind is ArchiveKind.BZIP2:
            import bz2
            return bz2.open(path, "rb")
        if kind in (ArchiveKind.XZ, ArchiveKind.LZMA):
            import lzma
            return lzma.open(path, "rb")
        if kind is ArchiveKind.ZSTD:
            try:
                import zstandard
            except ImportError as exc:
                raise ImportError("未安装 zstandard，无法解压 zst") from exc
            fh = open(path, "rb")
            return zstandard.ZstdDecompressor().stream_reader(fh)
        raise ValueError(f"不支持的单流压缩类型：{kind}")

    def extract(self, info: ArchiveInfo, ctx: ExtractContext) -> ExtractResult:
        started = time.perf_counter()
        out = self._prepare_dir(ctx)

        # 嗅探内层是否为 TAR（.tar.gz / .tgz 等）
        try:
            stream = self._open(info)
            head = stream.read(512)
            stream.close()
        except ImportError as exc:
            return self._result(info, ExtractStatus.UNSUPPORTED, message=str(exc), started=started)
        except Exception as exc:  # noqa: BLE001
            return self._result(info, ExtractStatus.FAILED,
                                message=f"解压失败：{exc}", started=started)

        if _is_tar_header(head):
            return self._extract_inner_tar(info, ctx, out, started)

        # 单文件解压（分块，内存 O(1MiB)）
        name = self._output_name(info.path)
        total = info.size or 0
        try:
            stream = self._open(info)
            with stream:
                saved = self._copy_stream(ctx, out, name, stream, total=total,
                                          name=name, progress_every=_PROGRESS_EVERY)
        except Exception as exc:  # noqa: BLE001
            return self._result(info, ExtractStatus.FAILED,
                                message=f"解压失败：{exc}", started=started)
        if saved is None:
            return self._result(info, ExtractStatus.SUCCESS, files=[],
                                message="已按冲突策略跳过（目标文件已存在）",
                                started=started)
        return self._result(info, ExtractStatus.SUCCESS, files=[saved],
                            message=f"解出文件：{Path(saved).name}", started=started)

    def _extract_inner_tar(self, info, ctx, out, started) -> ExtractResult:
        written: list[str] = []
        try:
            stream = self._open(info)
            with stream, tarfile.open(fileobj=stream, mode="r|") as tf:
                for idx, m in enumerate(tf, 1):
                    ctx.check_cancel()
                    if not m.isfile():
                        continue
                    src = tf.extractfile(m)
                    if src is None:
                        continue
                    with src:
                        saved = self._copy_stream(ctx, out, m.name, src,
                                                  member_mtime=_mtime_of(m.mtime),
                                                  name=m.name)
                    if saved is not None:
                        written.append(saved)
                    ctx.progress(idx, idx, m.name)
        except Exception as exc:  # noqa: BLE001
            return self._result(info, ExtractStatus.FAILED,
                                message=f"内层 TAR 解包失败：{exc}", started=started)
        return self._result(info, ExtractStatus.SUCCESS, files=written,
                            message=f"内层 TAR 解出 {len(written)} 个文件", started=started)

    @staticmethod
    def _output_name(path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix in _STREAM_SUFFIXES:
            rest = _STREAM_SUFFIXES[suffix]
            if rest:
                return path.stem + rest
            # 去掉压缩后缀，保留原名；若无名字则给默认名
            return path.stem or (path.name + ".out")
        return path.name + ".out"
