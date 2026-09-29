"""解包器注册表：注册、发现、选择。"""
from __future__ import annotations

from typing import Optional, Type

from .extractors.base import BaseExtractor
from .models import ArchiveInfo

_REGISTRY: list[BaseExtractor] = []
_LOADED = False
_LOAD_ERRORS: list[str] = []
_PLUGIN_MODULES: list[str] = []


def register(cls: Type[BaseExtractor]) -> Type[BaseExtractor]:
    """类装饰器：实例化并登记解包器。"""
    inst = cls()
    _REGISTRY.append(inst)
    _REGISTRY.sort(key=lambda e: e.priority)
    return cls


def load_all() -> None:
    """导入全部内置解包器模块并加载用户插件（触发注册）。幂等。"""
    global _LOADED
    if _LOADED:
        return
    from .extractors import archive, cab, dotnet, external, gamepack, installer, iso  # noqa: F401
    from .plugins import load_plugins

    loaded, errors = load_plugins()
    _LOAD_ERRORS[:] = errors
    _PLUGIN_MODULES[:] = loaded
    _REGISTRY.sort(key=lambda e: e.priority)
    _LOADED = True


def load_errors() -> list[str]:
    """返回插件加载错误（供界面/命令行提示，避免静默失败）。"""
    load_all()
    return list(_LOAD_ERRORS)


def plugin_modules() -> list[str]:
    load_all()
    return list(_PLUGIN_MODULES)


def extractors() -> list[BaseExtractor]:
    load_all()
    return list(_REGISTRY)


def find(info: ArchiveInfo) -> list[BaseExtractor]:
    """返回所有可处理该文件类型的解包器（按优先级排序）。"""
    return [e for e in extractors() if e.can_handle(info)]


def get(name: str) -> Optional[BaseExtractor]:
    for e in extractors():
        if e.name == name:
            return e
    return None
