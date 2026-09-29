"""WIN 解包工具 —— 图形界面版入口。

双击运行：直接打开图形界面（本程序使用 GUI 子系统，不会弹出终端窗口）。
命令行调用：若从 cmd / PowerShell 启动且带了文件参数，会自动挂回父控制台输出结果；
            未挂上（如双击传入文件）时会弹窗提示改用命令行版。

用法：
    WinUnpack.exe                         # 图形界面
    WinUnpack.exe -i a.zip b.7z           # 命令行探测（输出到父控制台）
    WinUnpack.exe a.zip -o out -p 123     # 命令行解包
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 保证以脚本方式运行时能找到同目录的包
sys.path.insert(0, str(Path(__file__).resolve().parent))

from unpacker import cli  # noqa: E402  (必须在 sys.path 设置之后)

_MB_ICONERROR = 0x10


def _report_fatal(message: str) -> None:
    """无控制台场景下用消息框提示（不依赖 PySide6）。"""
    print(message, file=sys.stderr)
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "WIN 解包工具", _MB_ICONERROR)
    except Exception:  # noqa: BLE001 - 提示失败不影响退出码
        pass


def main(argv: list[str] | None = None) -> int:
    args = cli.build_parser().parse_args(argv if argv is not None else sys.argv[1:])

    want_gui = args.gui or not args.inputs
    if want_gui:
        try:
            from gui.main_window import launch
        except ImportError as exc:
            _report_fatal(f"无法加载图形界面（缺少 PySide6）：{exc}\n\n"
                          f"可改用命令行版 WinUnpack-CLI.exe")
            return 4
        return launch()

    # 命令行模式：GUI 子系统进程默认没有控制台，需挂回父控制台
    if not cli.attach_parent_console():
        _report_fatal(
            "当前进程没有可用的控制台窗口，命令行输出无法显示。\n\n"
            "请任选其一：\n"
            "  1. 使用命令行版程序：WinUnpack-CLI.exe\n"
            "  2. 在 cmd / PowerShell 中调用本程序\n\n"
            "只想解包的话，直接双击 WinUnpack.exe 打开图形界面即可。"
        )
        return 4

    cli.fix_console_encoding()
    return cli.run_cli(args)


if __name__ == "__main__":
    raise SystemExit(main())
