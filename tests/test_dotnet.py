"""托管 .NET 程序集识别与资源提取测试。

真实样本（用户导入的第三方 exe）不随仓库分发，缺失时自动跳过；
识别逻辑用手工构造的 PE 头做单元测试，不依赖外部文件。
"""
from __future__ import annotations

import struct
from pathlib import Path

import pytest

from unpacker import detector, registry
from unpacker.extractors.dotnet import (
    DotNetExtractor, _read_comp_uint, _us_strings, find_decompiler,
    resolve_decompiler,
)
from unpacker.models import ArchiveKind, ExtractStatus

pytest.importorskip("dnfile")

ROOT = Path(__file__).resolve().parents[1]
REAL_SAMPLE = ROOT / "exe" / "Ha-mio-Windows-0.1.1.exe"


# ==================================================== 合成 PE 头
def _pe32plus(clr_rva: int, clr_size: int = 0x48, e_lfanew: int = 0x80) -> bytes:
    """构造一个只够被指纹函数读取的最小 PE32+ 头。"""
    buf = bytearray(0x200)
    buf[0:2] = b"MZ"
    struct.pack_into("<I", buf, 0x3C, e_lfanew)
    buf[e_lfanew:e_lfanew + 4] = b"PE\x00\x00"
    opt = e_lfanew + 24
    struct.pack_into("<H", buf, opt, 0x20B)          # PE32+
    dd = opt + 112                                    # PE32+ 数据目录起始
    struct.pack_into("<II", buf, dd + 14 * 8, clr_rva, clr_size)
    return bytes(buf)


def test_is_dotnet_detects_clr_directory():
    assert detector._is_dotnet(_pe32plus(0x2000)) is True


def test_is_dotnet_rejects_native_pe():
    assert detector._is_dotnet(_pe32plus(0, 0)) is False


def test_is_dotnet_rejects_non_pe():
    assert detector._is_dotnet(b"NOTAPE" + b"\x00" * 200) is False


def test_is_dotnet_tolerates_truncated_header():
    assert detector._is_dotnet(_pe32plus(0x2000)[:64]) is False


def test_detect_classifies_managed_pe(tmp_path):
    """完整走一遍 detector：合成 PE 应被识别为 .NET 程序集。"""
    p = tmp_path / "sample.exe"
    p.write_bytes(_pe32plus(0x2000))
    info = detector.detect(p)
    assert info.kind is ArchiveKind.DOTNET
    assert "反编译" in info.note


def test_dotnet_extractor_is_registered():
    names = [e.name for e in registry.extractors()]
    assert ".NET 程序集" in names
    assert ArchiveKind.DOTNET in DotNetExtractor.kinds


# ==================================================== #US 堆解析
def test_read_comp_uint_encodings():
    assert _read_comp_uint(b"\x7f", 0) == (0x7F, 1)
    assert _read_comp_uint(b"\x80\x80", 0) == (0x80, 2)
    assert _read_comp_uint(b"\xbf\xff", 0) == (0x3FFF, 2)
    assert _read_comp_uint(b"\xc0\x00\x40\x00", 0) == (0x4000, 4)


def test_us_strings_reads_utf16_literals():
    text = "你好"
    body = text.encode("utf-16-le")
    heap = bytes([len(body) + 1]) + body + b"\x01"
    assert _us_strings(heap, 0, len(heap)) == ["你好"]


def test_us_strings_skips_empty_entries():
    heap = b"\x00\x00"
    assert _us_strings(heap, 0, len(heap)) == []


# ==================================================== ILSpy 定位
def _no_bundled(monkeypatch):
    """屏蔽随包携带的反编译器，模拟「什么都没装」的机器。"""
    monkeypatch.setattr("unpacker.extractors.dotnet._bundled_roots", lambda: [])
    monkeypatch.setattr("unpacker.extractors.dotnet.shutil.which", lambda _n: None)
    monkeypatch.setattr("unpacker.extractors.dotnet.Path.home",
                        classmethod(lambda _cls: Path("C:/__nonexistent__")))


def test_find_decompiler_none_when_absent(monkeypatch):
    _no_bundled(monkeypatch)
    monkeypatch.delenv("ILSPYCMD_PATH", raising=False)
    assert find_decompiler() is None
    cmd, why = resolve_decompiler()
    assert cmd is None and "ilspycmd" in why       # 未命中时给出获取方式


def test_find_decompiler_from_env_dll(tmp_path, monkeypatch):
    dll = tmp_path / "ilspycmd.dll"
    dll.write_bytes(b"MZ")
    monkeypatch.setenv("ILSPYCMD_PATH", str(dll))
    cmd, why = resolve_decompiler()
    assert cmd is not None and cmd[-1] == str(dll)
    assert cmd[0].lower().endswith(("dotnet", "dotnet.exe"))   # 用 dotnet 驱动
    assert "环境变量" in why


def test_find_decompiler_from_env_exe(tmp_path, monkeypatch):
    exe = tmp_path / "ilspycmd.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setenv("ILSPYCMD_PATH", str(exe))
    assert find_decompiler() == [str(exe)]


def test_find_decompiler_env_dll_without_dotnet(tmp_path, monkeypatch):
    """有 dll 但没有 dotnet 运行时时，要给出明确原因而不是含糊的「没找到」。"""
    dll = tmp_path / "ilspycmd.dll"
    dll.write_bytes(b"MZ")
    monkeypatch.setenv("ILSPYCMD_PATH", str(dll))
    monkeypatch.setattr("unpacker.extractors.dotnet.shutil.which", lambda _n: None)
    cmd, why = resolve_decompiler()
    assert cmd is None and "dotnet 运行时" in why


def test_find_decompiler_ignores_missing_env_path(monkeypatch):
    _no_bundled(monkeypatch)
    monkeypatch.setenv("ILSPYCMD_PATH", "C:/__nonexistent__/nope.dll")
    assert find_decompiler() is None


def test_find_decompiler_prefers_env_over_bundled(tmp_path, monkeypatch):
    exe = tmp_path / "ilspycmd.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setenv("ILSPYCMD_PATH", str(exe))
    cmd, why = resolve_decompiler()
    assert cmd == [str(exe)] and "环境变量" in why


def test_bundled_decompiler_is_discovered(monkeypatch):
    """随包携带的 tools/ilspycmd 应被自动发现（源码运行时看项目根）。"""
    bundled = ROOT / "tools" / "ilspycmd" / "ilspycmd.dll"
    if not bundled.is_file():
        pytest.skip("项目未携带 tools/ilspycmd")
    monkeypatch.delenv("ILSPYCMD_PATH", raising=False)
    cmd, why = resolve_decompiler()
    assert cmd is not None, why
    assert cmd[-1].endswith("ilspycmd.dll")
    assert "随程序携带" in why


# ==================================================== csproj 资源重定向
def test_retarget_resources_rewrites_paths():
    csproj = ('<None Remove="A.png" />\n'
              '<EmbeddedResource Include="A.png" LogicalName="A.png" />\n'
              '<EmbeddedResource Include="B.txt" LogicalName="B.txt" />\n')
    fixed = DotNetExtractor._retarget_resources(csproj, {"A.png", "B.txt"})
    assert '<None Remove="..\\A.png" />' in fixed
    assert '<EmbeddedResource Include="..\\A.png" LogicalName="A.png" />' in fixed
    assert '<EmbeddedResource Include="..\\B.txt" LogicalName="B.txt" />' in fixed


def test_retarget_resources_leaves_unrelated_paths():
    csproj = '<Compile Include="Program.cs" />'
    assert DotNetExtractor._retarget_resources(csproj, {"A.png"}) == csproj


# ==================================================== 真实样本（可选）
@pytest.mark.skipif(not REAL_SAMPLE.is_file(),
                    reason="未提供真实 .NET 样本（exe/ 目录下无样本，跳过集成测试）")
def test_real_dotnet_assembly_extraction(workdir):
    out = workdir / "out"
    from unpacker.engine import Engine, EngineOptions

    res = Engine(EngineOptions(output_dir=out, merge_output=True)).extract(REAL_SAMPLE)

    assert res.status is ExtractStatus.SUCCESS, res.message
    assert res.extractor == ".NET 程序集"
    files = {p.name for p in out.iterdir()}
    assert "HaMio.persona.txt" in files
    assert "_dotnet_info.txt" in files
    pngs = [p for p in out.iterdir() if p.suffix == ".png"]
    assert len(pngs) == 12
    for p in pngs:
        assert p.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert res.bytes_written > 15 * 1024 * 1024

    info_text = (out / "_dotnet_info.txt").read_text(encoding="utf-8")
    assert "ILONLY" in info_text
    assert "PresentationFramework" in info_text      # WPF 程序集引用
    code_dir = out / "csharp"
    if code_dir.is_dir():
        cs = list(code_dir.rglob("*.cs"))
        assert cs, "csharp/ 存在但没有任何 .cs"
        joined = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in cs)
        assert "namespace HaMio" in joined
        # 反编译出的内嵌资源引用必须指向上层，不能重复搬运十几 MB
        proj = next(code_dir.glob("*.csproj"), None)
        if proj:
            assert 'Include="..\\HaMio.persona.txt"' in proj.read_text(encoding="utf-8")
            assert not (code_dir / "HaMio.persona.txt").exists()
        assert "已反编译到" in info_text
    else:
        assert "ilspycmd" in info_text               # 未装反编译器时给出指引
