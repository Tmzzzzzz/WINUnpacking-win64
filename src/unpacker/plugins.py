"""用户插件加载器。

把 plugins/ 目录下的 .py 文件当模块执行；插件内用 registry.register 装饰器
注册自定义解包器即可接入主流程（GUI 与引擎无需改动）。

插件可用位置：
    1) <项目根>/plugins/*.py        （随程序分发）
    2) ~/.winunpack/plugins/*.py    （用户私有）
"""
from __future__ import annotations

import importlib.util
import sys
import traceback
from pathlib import Path

_LOADED_MODULES: list[str] = []
_ERRORS: list[str] = []


def plugin_dirs() -> list[Path]:
    """插件搜索目录（兼容 PyInstaller 打包后的单文件模式）。

    源码布局：<项目根>/src/unpacker/plugins.py -> parents[2] 即项目根
    """
    dirs: list[Path] = []
    if getattr(sys, "frozen", False):
        # 打包后：exe 同级目录下的 plugins/
        dirs.append(Path(sys.executable).resolve().parent / "plugins")
    else:
        dirs.append(Path(__file__).resolve().parents[2] / "plugins")
    dirs.append(Path.home() / ".winunpack" / "plugins")
    return dirs


def load_plugins() -> tuple[list[str], list[str]]:
    """加载所有插件，返回 (已加载模块名, 错误信息)。"""
    for directory in plugin_dirs():
        if not directory.is_dir():
            continue
        for py in sorted(directory.glob("*.py")):
            if py.name.startswith("_"):
                continue
            mod_name = f"winunpack_plugin_{py.stem}"
            if mod_name in _LOADED_MODULES:
                continue
            try:
                spec = importlib.util.spec_from_file_location(mod_name, py)
                if spec is None or spec.loader is None:
                    continue
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                _LOADED_MODULES.append(mod_name)
            except Exception:  # noqa: BLE001 - 插件失败不应影响主程序
                _ERRORS.append(f"{py.name}: {traceback.format_exc(limit=2)}")
    return _LOADED_MODULES, _ERRORS
