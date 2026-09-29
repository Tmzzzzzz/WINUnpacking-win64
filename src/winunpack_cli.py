"""WIN 解包工具 —— 命令行版入口（控制台子系统）。

与图形界面版分开打包的原因：
    WinUnpack.exe      使用 GUI 子系统 —— 双击不弹出终端窗口
    WinUnpack-CLI.exe  使用控制台子系统 —— CLI 输出/阻塞/退出码语义完整

同时本入口不依赖 PySide6，因此体积显著更小。

用法：
    WinUnpack-CLI.exe -i a.zip b.7z
    WinUnpack-CLI.exe a.zip -o out -p 123 --report r.csv
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from unpacker import cli  # noqa: E402

_USAGE = """WIN 解包工具 - 命令行版

用法：
  WinUnpack-CLI.exe <文件或目录...> [选项]

选项：
  -i, --inspect          仅探测格式，不解包
  -n, --dry-run          试运行：只探测并给出输出计划，不写任何文件
  -o, --output DIR       输出目录（默认 <文件名>_unpacked）
      --merge            全部解到同一目录
      --conflict MODE    重名策略：rename（默认，保留两者）/ overwrite 覆盖 /
                         skip 跳过已存在 / newer 较新者胜
      --overwrite        等价于 --conflict overwrite
      --encoding ENC     成员名编码：auto（默认，自动回退 gbk/big5/shift_jis）
                         或指定 gbk / utf-8 等
  -r, --recursive        递归解包嵌套压缩包
      --max-depth N      递归最大层级（默认 3）
  -p, --password PWD     密码，可重复传入
      --pwfile FILE      密码字典文件（每行一个）
      --report FILE      导出报告（.csv / .json / .txt）
      --json             把结构化结果（JSON）打印到标准输出
  -v, --version          显示版本

退出码：
  0 全部成功 / 1 存在失败 / 2 无可处理文件 / 3 存在需密码项

示例：
  WinUnpack-CLI.exe -i sample.zip
  WinUnpack-CLI.exe sample.zip --conflict skip
  WinUnpack-CLI.exe D:\\packs -o D:\\out --merge -r -p 123456 --report r.csv
  WinUnpack-CLI.exe D:\\packs --json > result.json

图形界面请运行 WinUnpack.exe
"""


def main(argv: list[str] | None = None) -> int:
    cli.fix_console_encoding()
    args = cli.build_parser().parse_args(argv if argv is not None else sys.argv[1:])

    if not args.inputs:
        print(_USAGE, file=sys.stderr)
        return 2

    return cli.run_cli(args)


if __name__ == "__main__":
    raise SystemExit(main())
