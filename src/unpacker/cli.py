"""命令行模式：参数解析与执行。

独立成模块，供两个入口共用：
    src/main.py            —— 图形界面版入口（GUI 子系统，双击不弹终端）
    src/winunpack_cli.py   —— 命令行版入口（控制台子系统，CLI 语义完整）
"""
from __future__ import annotations

import argparse
import os
import sys
import unicodedata
from pathlib import Path

from . import __version__, detector, registry, report
from .engine import Engine, EngineCallbacks, EngineOptions
from .models import ConflictPolicy, ExtractStatus
from .utils import human_size

_ATTACH_PARENT_PROCESS = -1

#: 表格列宽（按显示宽度计算，中日韩字符占 2 列）
_TYPE_COL = 18


def display_width(text: str) -> int:
    """按终端显示宽度计算字符串长度（CJK 全角字符算 2 列）。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
               for ch in text)


def pad(text: str, width: int, *, align: str = "left") -> str:
    """按显示宽度补齐/对齐，避免中文列错位。"""
    fill = " " * max(0, width - display_width(text))
    return fill + text if align == "right" else text + fill


def fix_console_encoding() -> None:
    """把标准输出/错误切到 UTF-8，避免中文在 GBK 控制台乱码。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def attach_parent_console() -> bool:
    """GUI 子系统进程从控制台调用时，重新附加到父进程控制台。

    ``--windowed`` 打包后进程没有控制台，直接 ``print`` 会丢失输出；
    ``AttachConsole(ATTACH_PARENT_PROCESS)`` 可挂回调用它的 cmd/PowerShell，
    恢复标准输出。双击启动（无父控制台）时返回 False。

    非打包环境（源码运行）始终返回 True。
    """
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return True
    try:
        import ctypes

        if not ctypes.windll.kernel32.AttachConsole(_ATTACH_PARENT_PROCESS):
            return False
        for attr in ("stdout", "stderr"):
            try:
                setattr(sys, attr, open("CONOUT$", "w", encoding="utf-8",
                                        errors="replace", buffering=1))
            except OSError:
                pass
        try:
            sys.stdin = open("CONIN$", "r", encoding="utf-8", errors="replace")
        except OSError:
            pass
        return True
    except Exception:  # noqa: BLE001 - 附加失败按无控制台处理
        return False


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="win-unpack",
        description="Windows 端综合解包工具（自动识别格式：压缩包 / 安装包 / 游戏资源包）",
    )
    p.add_argument("inputs", nargs="*", help="要处理的文件或目录")
    p.add_argument("-i", "--inspect", action="store_true", help="仅探测格式，不解包")
    p.add_argument("-n", "--dry-run", action="store_true",
                   help="试运行：只探测并给出输出计划，不写任何文件")
    p.add_argument("-o", "--output", help="输出目录")
    p.add_argument("--merge", action="store_true", help="全部解到同一目录")
    p.add_argument("--conflict", choices=[c.value for c in ConflictPolicy],
                   default=ConflictPolicy.RENAME.value,
                   help="重名处理策略：rename 保留两者（默认）/ overwrite 覆盖 / "
                        "skip 跳过已存在 / newer 较新者胜")
    p.add_argument("--overwrite", action="store_true",
                   help="等价于 --conflict overwrite（保留该参数以兼容旧脚本）")
    p.add_argument("--encoding", default="auto",
                   help="归档成员名编码：auto（默认，自动回退 gbk/big5/shift_jis）"
                        "或指定 gbk / utf-8 等")
    p.add_argument("-r", "--recursive", action="store_true", help="递归解包嵌套压缩包")
    p.add_argument("--max-depth", type=int, default=3, help="递归最大层级（默认 3）")
    p.add_argument("-p", "--password", action="append", default=[],
                   help="密码，可重复传入")
    p.add_argument("--pwfile", help="密码字典文件（每行一个）")
    p.add_argument("--report", help="把结果导出到文件（.csv/.json/.txt）")
    p.add_argument("--json", action="store_true",
                   help="把结构化结果（JSON）打印到标准输出")
    p.add_argument("--gui", action="store_true", help="强制启动图形界面（仅图形界面版有效）")
    p.add_argument("-v", "--version", action="version", version=f"WIN 解包工具 v{__version__}")
    return p


def collect_targets(inputs: list[str]) -> list[Path]:
    targets: list[Path] = []
    for raw in inputs:
        p = Path(raw)
        if p.is_dir():
            targets.extend(sorted(f for f in p.rglob("*") if f.is_file()))
        elif p.is_file():
            targets.append(p)
        else:
            print(f"[跳过] 不存在：{raw}", file=sys.stderr)
    return targets


def run_cli(args: argparse.Namespace) -> int:
    registry.load_all()
    for err in registry.load_errors():
        print(f"[插件加载失败] {err}", file=sys.stderr)

    targets = collect_targets(args.inputs)
    if not targets:
        print("没有可处理的文件。", file=sys.stderr)
        return 2

    if args.inspect:
        print(f"{pad('类型', _TYPE_COL)}{pad('大小', 10, align='right')}  "
              f"{pad('加密', 5)} 文件")
        print("-" * 72)
        for t in targets:
            info = detector.detect(t)
            print(f"{pad(info.kind.label, _TYPE_COL)}"
                  f"{pad(human_size(info.size), 10, align='right')}  "
                  f"{pad('是' if info.encrypted else '否', 5)} {t}")
        return 0

    passwords = list(args.password)
    if args.pwfile:
        try:
            passwords += [ln.strip() for ln in
                          Path(args.pwfile).read_text(encoding="utf-8",
                                                      errors="replace").splitlines()
                          if ln.strip()]
        except OSError as exc:
            print(f"[警告] 密码文件读取失败：{exc}", file=sys.stderr)

    conflict = (ConflictPolicy.OVERWRITE if args.overwrite
                else ConflictPolicy(args.conflict))

    options = EngineOptions(
        output_dir=Path(args.output) if args.output else None,
        merge_output=args.merge,
        conflict=conflict,
        recursive=args.recursive,
        max_depth=args.max_depth,
        passwords=passwords,
        encoding=args.encoding or "auto",
        dry_run=bool(args.dry_run),
    )

    # --json 时把过程日志赶到 stderr，保证 stdout 是纯 JSON
    stream = sys.stderr if args.json else sys.stdout

    def on_log(level: str, msg: str) -> None:
        tag = {"info": "·", "warn": "!", "error": "x"}.get(level, "-")
        print(f"  [{tag}] {msg}", file=stream)

    def on_file_start(path: Path, idx: int, total: int) -> None:
        print(f"\n[{idx}/{total}] {path}", file=stream)

    engine = Engine(options, EngineCallbacks(on_log=on_log, on_file_start=on_file_start))
    results = engine.run(targets)

    if args.json:
        print(report.to_json(results))
    else:
        print("\n" + report.to_text(results))

    ok = sum(1 for r in results if r.status is ExtractStatus.SUCCESS)
    need = sum(1 for r in results if r.status is ExtractStatus.NEED_PASSWORD)
    dry = sum(1 for r in results if r.status is ExtractStatus.DRY_RUN)

    if args.report:
        out = report.export(results, Path(args.report))
        print(f"\n报告已导出：{out}", file=stream)

    if need:
        return 3
    if dry and dry == len(results):
        return 0
    return 0 if ok == len(results) - dry else 1
