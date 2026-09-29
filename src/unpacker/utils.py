"""通用工具：路径安全、体积格式化、唯一路径、文件名编码回退。"""
from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Windows 保留设备名，作为文件名会失败
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

# 文件名编码自动回退候选（按可能性排序）。
# 注意：UTF-8 必须最先，cp437 最后（它是 0x00-0xFF 全映射，一定能解出东西，
# 放在前面会永远命中，导致 GBK 包名乱码）。
_ENCODING_CANDIDATES: tuple[str, ...] = ("utf-8", "gbk", "big5", "shift_jis", "cp437")

_CJK_RANGES: tuple[tuple[int, int], ...] = (
    (0x3400, 0x4DBF),    # 扩展 A
    (0x4E00, 0x9FFF),    # 基本区
    (0xF900, 0xFAFF),    # 兼容表意
    (0x3000, 0x303F),    # CJK 标点
    (0xFF00, 0xFFEF),    # 全角字符
    (0x3040, 0x30FF),    # 日文假名
    (0xAC00, 0xD7AF),    # 韩文音节
)


def has_cjk(text: str) -> bool:
    """是否含 CJK / 日韩文（用于判定编码回退是否真的“更像原名”）。"""
    return any(any(lo <= ord(ch) <= hi for lo, hi in _CJK_RANGES) for ch in text)


def human_size(num: float) -> str:
    """把字节数格式化为易读字符串。"""
    if num is None:
        return "-"
    num = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024.0:
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} PB"


def decode_member_name(raw: bytes, encoding: str = "auto",
                       fallback: str = "cp437") -> str:
    """把归档成员名的原始字节解码为字符串。

    国产压缩工具大量使用 GBK/CP936 且不写 UTF-8 标志位，标准库按 cp437 解码
    会出现「中文名乱码」。这里按候选编码依次 strict 解码：

    * ``encoding="auto"`` —— 按 utf-8 → gbk → big5 → shift_jis 顺序尝试，
      只有当解码结果**含 CJK 字符**时才采纳（避免把正常西文名误判成中文）；
    * 指定具体编码 —— 先按指定编码 strict 解码，失败再退回自动流程；
    * 全部失败 —— 用 fallback（默认 cp437）以 replace 方式兜底，保证有名字。
    """
    if not raw:
        return ""

    def _try(codec: str) -> str | None:
        try:
            text = raw.decode(codec)
        except (UnicodeDecodeError, LookupError):
            return None
        # 纯 ASCII 任何编码都一样，直接采纳
        if text.isascii():
            return text
        return text

    if encoding and encoding.lower() not in ("auto", ""):
        text = _try(encoding)
        if text is not None:
            return text

    if encoding and encoding.lower() in ("auto", ""):
        for codec in _ENCODING_CANDIDATES:
            if codec == fallback:
                continue
            text = _try(codec)
            if text is None:
                continue
            # 只有解出 CJK 才认为“原编码猜错了”，否则保持既定 fallback
            if has_cjk(text):
                return text
            break

    try:
        return raw.decode(fallback)
    except (UnicodeDecodeError, LookupError):
        return raw.decode("latin-1", "replace")


def sanitize_member(name: str) -> str:
    """清洗归档内的成员名，防止非法字符、保留设备名与超长文件名。"""
    name = name.replace("\\", "/")
    parts: list[str] = []
    for part in name.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            # 路径穿越 -> 丢弃，交由 safe_join 兜底
            parts.append("__up__")
            continue
        cleaned = _ILLEGAL.sub("_", part).strip().rstrip(".")
        if not cleaned:
            cleaned = "_"
        # 单个路径分量上限 120 字符：避免超出文件系统 255 字节限制导致 Errno 22
        if len(cleaned) > 120:
            stem, dot, suffix = cleaned.rpartition(".")
            keep = 120 - (len(suffix) + 1 if dot else 0)
            cleaned = (stem[:max(keep, 1)] + dot + suffix) if dot else cleaned[:120]
        stem = cleaned.split(".")[0].upper()
        if stem in _RESERVED:
            cleaned = "_" + cleaned
        parts.append(cleaned)
    return "/".join(parts)


def recode_zip_name(name: str, is_utf8: bool, encoding: str = "auto") -> str:
    """还原 ZIP 成员名。

    ``zipfile`` 在缺少 UTF-8 标志位（bit 11）时按 **cp437** 解码文件名，
    GBK 命名的中文包因此乱码。cp437 是 0x00–0xFF 的完全单字节映射，
    所以 ``name.encode("cp437")`` 可以无损还原原始字节，再按候选编码重解。

    已是 UTF-8（标志位明确）或纯 ASCII 的名字原样返回。
    """
    if is_utf8 or name.isascii():
        return name
    if encoding and encoding.lower() in ("utf-8", "utf8"):
        return name
    try:
        raw = name.encode("cp437")
    except UnicodeEncodeError:
        return name  # 含 cp437 之外的字符，说明已被上层正确解码过
    return decode_member_name(raw, encoding, fallback="cp437")


def safe_join(base: Path, member: str) -> Path:
    """把归档成员安全地拼接到 base 下，阻止路径穿越（Zip Slip）。"""
    base = Path(base).resolve()
    member = sanitize_member(member)
    target = (base / member).resolve()
    if target != base and base not in target.parents:
        raise ValueError(f"非法路径（疑似路径穿越）：{member}")
    return target


def unique_path(path: Path) -> Path:
    """若路径已存在，追加 _1 / _2 ... 直到不冲突。"""
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    i = 1
    while True:
        candidate = parent / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def strip_long(path: str) -> str:
    """Windows 长路径前缀，规避 260 字符限制。"""
    if os.name != "nt":
        return path
    p = os.path.abspath(path)
    if p.startswith("\\\\?\\"):
        return p
    if p.startswith("\\\\"):
        return "\\\\?\\UNC\\" + p.lstrip("\\")
    return "\\\\?\\" + p


def default_output_dir(source: Path, base: Path | None = None) -> Path:
    """默认输出目录：<base>/<文件名>_unpacked。"""
    base = Path(base) if base else source.parent
    safe = unicodedata.normalize("NFC", source.name)
    return unique_path(base / f"{safe}_unpacked")
