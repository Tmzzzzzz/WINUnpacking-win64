"""WIN-Unpacking —— Windows 端综合解包工具（核心包）。

分层设计：
    unpacker/       纯逻辑层，零 GUI 依赖，可被 CLI / 测试直接调用
        models.py       数据模型
        detector.py     魔数格式探测
        registry.py     解包器注册表
        engine.py       任务派发引擎
        extractors/     各类解包器
    gui/            PySide6 展示层
"""

__version__ = "1.0.2"
__all__ = ["__version__"]
