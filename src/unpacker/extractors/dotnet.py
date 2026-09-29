"""托管 .NET 程序集解包器。

托管 PE（CLR Flags = ILONLY）里的载荷不是标准归档，而是 .NET 元数据 +
**内嵌托管资源**（`ManifestResource` 表）以及 IL 代码。本解包器：

1. 用 `dnfile` 解析 PE / COR20 / 元数据表，做真实校验（MetaData 必须指向 BSJB）；
2. 逐个还原 `ManifestResource`：每项形如 ``[int32 长度][原始字节]``，逐字节写出，
   资源名（如 ``HaMio.idle.png``）直接作为输出文件名，经基类的安全路径清洗；
3. 另存一份 ``_dotnet_info.txt``：运行时/程序集引用/类型与方法清单/字符串字面量；
4. **可选反编译源码**：若系统装有 ILSpy 命令行（`ilspycmd`），自动把 IL 还原成
   C# 源码输出到 ``csharp/``；未安装时给出获取方式，不影响其它产物。

ILSpy 定位顺序（任一即可）：
    - 环境变量 ``ILSPYCMD_PATH`` 指向 ``ilspycmd.exe`` 或 ``ilspycmd.dll``
    - PATH 中的 ``ilspycmd``
    - ``%USERPROFILE%\\.dotnet\\tools\\ilspycmd.exe``（`dotnet tool install -g ilspycmd` 的落点）
"""
from __future__ import annotations

import hashlib
import os
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from ..context import ExtractContext
from ..models import ArchiveInfo, ArchiveKind, ExtractResult, ExtractStatus
from ..registry import register
from .base import BaseExtractor

_META_SIG = b"BSJB"
_MAX_STRINGS = 20000          # 结构信息里最多列出多少条字符串字面量
_INFO_NAME = "_dotnet_info.txt"
_CODE_DIR = "csharp"
_DECOMPILE_TIMEOUT = 900
_ILSPY_ENV = "ILSPYCMD_PATH"
_BUNDLED_DIR = "ilspycmd"     # 随包携带的反编译器目录名（放在 tools/ 下）

_INSTALL_HINT = ("获取方式：`dotnet tool install -g ilspycmd`；"
                 f"或用环境变量 {_ILSPY_ENV} 指向 ilspycmd.exe / ilspycmd.dll；"
                 "或把 ILSpy 的 net8.0 构建放到 <程序目录>/tools/ilspycmd/ 下")


def _bundled_roots() -> list[Path]:
    """随包携带的反编译器搜索根目录（打包后取 exe 同级，源码运行取项目根）。"""
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parents[3]
    return [base / "tools" / _BUNDLED_DIR, base / "tools"]


def resolve_decompiler() -> tuple[Optional[list[str]], str]:
    """定位 ILSpy 命令行，返回 ``(命令前缀 或 None, 说明)``。

    搜索顺序：``ILSPYCMD_PATH`` 环境变量 → 随包携带的 ``tools/ilspycmd`` →
    PATH 中的 ``ilspycmd`` → ``%USERPROFILE%\\.dotnet\\tools``。
    ``.dll`` 形态需要 ``dotnet`` 运行时来驱动，因此单独区分「有 dll 但没有运行时」。
    """
    raw = os.environ.get(_ILSPY_ENV, "").strip().strip('"')
    if raw and Path(raw).is_file():
        if raw.lower().endswith(".dll"):
            dotnet = shutil.which("dotnet")
            if dotnet:
                return [dotnet, raw], f"来自环境变量 {_ILSPY_ENV}"
            return None, (f"{_ILSPY_ENV} 指向的是 ilspycmd.dll，但系统没有 dotnet 运行时，"
                          "无法驱动它；可改用 ilspycmd.exe，或安装 .NET 8 运行时")
        return [raw], f"来自环境变量 {_ILSPY_ENV}"

    for root in _bundled_roots():
        if not root.is_dir():
            continue
        exe = next(root.rglob("ilspycmd.exe"), None)
        if exe is not None:
            return [str(exe)], f"随程序携带（{exe.parent}）"
        dll = next(root.rglob("ilspycmd.dll"), None)
        if dll is not None:
            dotnet = shutil.which("dotnet")
            if dotnet:
                return [dotnet, str(dll)], f"随程序携带（{dll.parent}）"
            return None, ("随程序携带的 ILSpy 是 .NET 8 构建，驱动它需要 .NET 8 运行时；"
                          "请安装「.NET 8 Desktop Runtime」后重试，"
                          f"或用 {_ILSPY_ENV} 指定本机可用的 ilspycmd")

    exe = shutil.which("ilspycmd")
    if exe:
        return [exe], "来自 PATH"

    local = Path.home() / ".dotnet" / "tools" / "ilspycmd.exe"
    if local.is_file():
        return [str(local)], f"来自 {local.parent}"

    return None, _INSTALL_HINT


def find_decompiler() -> Optional[list[str]]:
    """定位 ILSpy 命令行，返回可直接 popen 的命令前缀（已含解释器）。"""
    return resolve_decompiler()[0]


def _read_comp_uint(buf: bytes, pos: int) -> tuple[int, int]:
    """.NET 压缩整数（#US / #Blob 堆的长度前缀）。"""
    b = buf[pos]
    if b & 0x80 == 0:
        return b, pos + 1
    if b & 0xC0 == 0x80:
        return ((b & 0x3F) << 8) | buf[pos + 1], pos + 2
    return ((b & 0x1F) << 24) | (buf[pos + 1] << 16) | (buf[pos + 2] << 8) \
        | buf[pos + 3], pos + 4


def _us_strings(data: bytes, offset: int, size: int) -> list[str]:
    """遍历 #US 堆，取出全部字符串字面量。"""
    heap = data[offset:offset + size]
    out: list[str] = []
    pos = 0
    while pos < len(heap):
        n, pos = _read_comp_uint(heap, pos)
        if n == 0:
            continue
        chunk, pos = heap[pos:pos + n], pos + n
        try:
            text = chunk[:n - 1].decode("utf-16-le")   # 末字节是“含非 ASCII”标志
        except UnicodeDecodeError:
            continue
        if text:
            out.append(text)
    return out


@register
class DotNetExtractor(BaseExtractor):
    name = ".NET 程序集"
    kinds = (ArchiveKind.DOTNET,)
    priority = 40                 # 早于安装包(PE)/资源包雕刻，避免被当成普通 PE
    requires = ("dnfile",)

    def extract(self, info: ArchiveInfo, ctx: ExtractContext) -> ExtractResult:
        started = time.perf_counter()
        try:
            import dnfile
        except ImportError:
            return self._result(
                info, ExtractStatus.UNSUPPORTED,
                message="未安装 dnfile，无法解析 .NET 程序集", started=started)

        try:
            dn = dnfile.dnPE(str(info.path))
        except Exception as exc:  # noqa: BLE001
            return self._result(info, ExtractStatus.FAILED,
                                message=f".NET 元数据解析失败：{exc}", started=started)

        net = dn.net
        if net is None:
            return self._result(info, ExtractStatus.UNSUPPORTED,
                                message="该 PE 不含 .NET 元数据（可能是原生程序）",
                                started=started)

        data = Path(info.path).read_bytes()
        md_off = dn.get_offset_from_rva(net.struct.MetaDataRva) if net.struct.MetaDataRva else None
        if md_off is None or data[md_off:md_off + 4] != _META_SIG:
            return self._result(
                info, ExtractStatus.UNSUPPORTED,
                message=".NET 元数据签名（BSJB）缺失，可能被加壳或裁剪，"
                        "建议改用 ILSpy/dnSpy 尝试打开", started=started)

        ctx.info(f"确认为托管 .NET 程序集，CLR Flags = 0x{net.struct.Flags:X}"
                 f"（{'ILONLY 纯托管' if net.struct.Flags & 0x1 else '含原生代码'}）")

        out = self._prepare_dir(ctx)
        written: list[str] = []
        total_bytes = 0
        code_files: list[str] = []

        #: ManifestResource 里的逻辑名（反编译阶段用来避免重复搬运资源）
        res_names = {str(r.Name.value).replace("\\", "/")
                     for r in dn.net.mdtables.ManifestResource.rows}

        # -------------------------------------------------- 内嵌托管资源
        if net.struct.ResourcesRva and net.struct.ResourcesSize:
            written, total_bytes = self._extract_resources(
                data, dn, net, ctx, out, started)
            if written:
                ctx.info(f"内嵌托管资源：{len(written)} 个，合计 "
                         f"{total_bytes / 1024 / 1024:.2f} MB")

        # -------------------------------------------------- C# 源码（可选）
        code_files, code_bytes = self._try_decompile(info, ctx, out, res_names)
        ctx.bytes_written += code_bytes

        # -------------------------------------------------- 结构信息
        info_rel = self._write_info(data, dn, net, ctx, out, info,
                                    code_ok=bool(code_files))
        if info_rel:
            written.append(info_rel)
        written.extend(code_files)

        n_res = len(written) - len(code_files) - (1 if info_rel else 0)
        if n_res == 0 and not code_files:
            return self._result(
                info, ExtractStatus.UNSUPPORTED, files=written, started=started,
                message="该 .NET 程序集不含内嵌资源，仅写出结构信息；"
                        "安装 ILSpy 后可自动反编译出 C# 源码")

        parts = []
        if n_res:
            parts.append(f"{n_res} 个内嵌资源（{total_bytes / 1024 / 1024:.2f} MB）")
        if code_files:
            n_cs = sum(1 for x in code_files if x.endswith(".cs"))
            parts.append(f"{n_cs} 个 C# 源码文件")
        parts.append("结构信息")
        return self._result(
            info, ExtractStatus.SUCCESS, files=written, started=started,
            message="从 .NET 程序集中解出 " + " + ".join(parts))

    # -------------------------------------------------------------- 反编译
    def _try_decompile(self, info: ArchiveInfo, ctx: ExtractContext,
                       out: Path, resource_names: set[str]) -> tuple[list[str], int]:
        """若有 ILSpy 可用则反编译出 C# 源码，失败不阻断其它产物。

        ILSpy 的 `-p` 会把内嵌资源一并复制进工程（本项目已在上层提取过，会重复
        十几 MB），因此先解到临时目录，只搬走源码/工程文件，并把 csproj 里对资源的
        引用改写为 ``..\\`` 前缀，指向已提取出来的那份。
        """
        cmd, why = resolve_decompiler()
        if cmd is None:
            ctx.info(f"跳过 C# 反编译：{why}")
            return [], 0

        tmp = out / ".ilspy_tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True, exist_ok=True)
        ctx.info(f"检测到 ILSpy（{why}），开始反编译 C# 源码…")
        try:
            # 注意：用二进制捕获。ilspycmd 在中文 Windows 上会向管道写入非 UTF-8 字节，
            # 若由 subprocess 以文本模式读取，读取线程会抛 UnicodeDecodeError。
            proc = subprocess.run(
                [*cmd, str(info.path), "-o", str(tmp), "-p",
                 "--nested-directories", "--disable-updatecheck"],
                capture_output=True, timeout=_DECOMPILE_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            shutil.rmtree(tmp, ignore_errors=True)
            ctx.warn("  └ ILSpy 反编译超时，已跳过")
            return [], 0
        except OSError as exc:
            shutil.rmtree(tmp, ignore_errors=True)
            ctx.warn(f"  └ 无法启动 ILSpy：{exc}")
            return [], 0

        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or b"").decode("utf-8", "replace")
            tail = [ln.strip() for ln in detail.strip().splitlines() if ln.strip()]
            shutil.rmtree(tmp, ignore_errors=True)
            ctx.warn(f"  └ ILSpy 反编译失败（返回码 {proc.returncode}）："
                     f"{tail[-1] if tail else ''}")
            return [], 0

        dest = out / _CODE_DIR
        dest.mkdir(parents=True, exist_ok=True)
        base = Path(out).resolve()
        kept: list[str] = []
        total = 0
        for src in sorted(tmp.rglob("*")):
            if not src.is_file() or src.name in resource_names:
                continue                     # 资源已在上一层提取，不重复搬运
            rel = src.relative_to(tmp)
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if src.suffix.lower() == ".csproj":
                target.write_text(self._retarget_resources(
                    src.read_text(encoding="utf-8", errors="replace"), resource_names),
                    encoding="utf-8")
            else:
                shutil.copy2(src, target)
            total += target.stat().st_size
            kept.append(self._rel(base, target))
        shutil.rmtree(tmp, ignore_errors=True)

        ctx.info(f"  └ 反编译完成：{len(kept)} 个文件 → {_CODE_DIR}/")
        for f in [x for x in kept if x.endswith(".cs")][:6]:
            ctx.info(f"     · {f}")
        n_cs = sum(1 for x in kept if x.endswith(".cs"))
        if n_cs > 6:
            ctx.info(f"     · …共 {n_cs} 个 .cs 文件")
        return kept, total

    @staticmethod
    def _retarget_resources(csproj: str, resource_names: set[str]) -> str:
        """把 csproj 里对内嵌资源的引用指向上层已提取的文件。"""
        out = csproj
        for name in resource_names:
            out = out.replace(f'Include="{name}"', f'Include="..\\{name}"')
            out = out.replace(f'Remove="{name}"', f'Remove="..\\{name}"')
        return out

    # ------------------------------------------------------------------ 资源
    def _extract_resources(self, data: bytes, dn, net, ctx: ExtractContext,
                           out: Path, started: float) -> tuple[list[str], int]:
        """还原 ManifestResource 表里的每一项。"""
        base = dn.get_offset_from_rva(net.struct.ResourcesRva)
        total = net.struct.ResourcesSize
        rows = sorted(dn.net.mdtables.ManifestResource.rows, key=lambda r: int(r.Offset))

        written: list[str] = []
        written_bytes = 0
        for idx, row in enumerate(rows, 1):
            ctx.check_cancel()
            at = base + int(row.Offset)
            if at + 4 > base + total:
                ctx.warn(f"  └ 资源偏移越界，跳过：{row.Name.value}")
                continue
            (length,) = struct.unpack_from("<i", data, at)
            if length <= 0 or at + 4 + length > len(data):
                ctx.warn(f"  └ 资源长度异常（{length}），跳过：{row.Name.value}")
                continue
            payload = data[at + 4:at + 4 + length]

            name = str(row.Name.value).replace("\\", "/")
            rel = self._write_bytes(ctx, out, name, payload)
            if rel is None:
                continue
            dest = out / rel
            digest = hashlib.sha256(payload).hexdigest()[:16]
            ctx.info(f"  · {name}  {length:,} 字节  sha256={digest}…")
            written.append(rel)
            written_bytes += length
            ctx.progress(idx, len(rows), name)
        return written, written_bytes

    # ---------------------------------------------------------------- 信息
    def _write_info(self, data: bytes, dn, net, ctx: ExtractContext,
                    out: Path, info: ArchiveInfo, code_ok: bool = False) -> Optional[str]:
        """把程序结构（程序集引用 / 类型方法 / 字符串字面量）写成文本产物。"""
        mt = dn.net.mdtables
        types, methods, fields = mt.TypeDef.rows, mt.MethodDef.rows, mt.Field.rows

        if code_ok:
            howto = [f"- C# 源码：已反编译到 `{_CODE_DIR}/` 目录（每个类型一个 .cs 文件）"]
        else:
            howto = ["- C# 源码：本次未反编译。ILSpy 定位顺序为",
                     f"  1. 环境变量 {_ILSPY_ENV}（可指向 ilspycmd.exe 或 ilspycmd.dll）",
                     "  2. 随程序携带的 `<程序目录>/tools/ilspycmd/`",
                     "  3. PATH 中的 `ilspycmd`",
                     "  4. `%USERPROFILE%\\.dotnet\\tools\\ilspycmd.exe`",
                     "  自行获取：`dotnet tool install -g ilspycmd`，然后：",
                     "  ```bat", f"  ilspycmd \"{info.path}\" -o out_csharp -p", "  ```",
                     "  GUI 工具 ILSpy / dnSpy 同样可直接打开该文件。"]

        lines = [f"# {info.path.name} —— .NET 程序集结构信息", "",
                 f"- 源文件：{info.path}",
                 f"- 文件大小：{info.size or len(data):,} 字节",
                 f"- CLR 运行时版本：{net.struct.MajorRuntimeVersion}."
                 f"{net.struct.MinorRuntimeVersion}",
                 f"- CLR Flags：0x{net.struct.Flags:X}"
                 f"（{'ILONLY，纯托管' if net.struct.Flags & 0x1 else '含原生代码'}）",
                 f"- 类型 {len(types)} / 方法 {len(methods)} / 字段 {len(fields)}", ""]
        lines += howto
        lines.append("")

        lines.append("## 程序集引用\n")
        for a in mt.AssemblyRef.rows:
            lines.append(f"- {a.Name.value} {a.MajorVersion}.{a.MinorVersion}.0.0")
        lines.append("")

        lines.append("## 类型结构\n")
        for t in types:
            ns, nm = t.TypeNamespace.value or "", t.TypeName.value
            full = f"{ns}.{nm}" if ns else nm
            lv = [fields[i.row_index - 1].Name.value
                  for i in (getattr(t, "FieldList", None) or [])]
            lm = [methods[i.row_index - 1].Name.value
                  for i in (getattr(t, "MethodList", None) or [])]
            lines.append(f"### {full}")
            if lv:
                lines.append("- 字段：" + "、".join(lv))
            if lm:
                lines.append("- 方法：" + "、".join(lm))
            lines.append("")

        stream = net.metadata.streams.get(b"#US")
        if stream is not None:
            strs = _us_strings(data, stream.file_offset, stream.struct.Size)
            lines.append(f"## 字符串字面量（{len(strs)} 条）\n")
            lines += [f"- {s}" for s in strs[:_MAX_STRINGS]]
            if len(strs) > _MAX_STRINGS:
                lines.append(f"- …（还有 {len(strs) - _MAX_STRINGS} 条未列出）")

        payload = "\n".join(lines).encode("utf-8")
        rel = self._write_bytes(ctx, out, _INFO_NAME, payload)
        if rel:
            ctx.info(f"已写出结构信息：{_INFO_NAME}（{len(payload):,} 字节）")
        return rel
