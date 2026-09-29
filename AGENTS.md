# AGENTS.md —— 给 AI 编码代理的开发指引

> 本文件是**机器可读优先**的工程约定：架构速查、硬性规则、踩坑清单、扩展步骤。
> 人读的版本在 [README.md](README.md)。**动手改代码前请先读完本文件。**

## 0. 一句话与边界

Windows 端综合解包工具：自动识别格式，处理**通用压缩包 / 安装包 / 游戏资源包**。
Python 3.10+ / PySide6（GUI）/ PyInstaller（单文件打包）。

- **真源只有 `src/`**。`release/` 是构建产物（已 gitignore），改代码**永远不要**改 `release/src`。
- 交付形态是**双 exe**：`WinUnpack.exe`（GUI 子系统）+ `WinUnpack-CLI.exe`（控制台子系统）。

## 1. 常用命令

```bat
:: 验证（改动后必跑，几秒）
.venv\Scripts\python -m pytest tests -q

:: 从源码启动图形界面（自动找 Python 环境）
Start.bat            :: 双击等效，pythonw 无黑窗
Start.bat console    :: 挂控制台，看报错

:: 打包
build.bat check     :: 只校验环境
build.bat           :: 出 release\WinUnpack-win64.zip
build.bat clean     :: 清 build\ 与 %TEMP% 下的 PyInstaller 残留

:: 生成图标（改 tools/icon/app-icon.svg 后）
.venv\Scripts\python tools\icon\render.py
```

## 2. 硬性规则（违反会造成静默故障或返工）

1. **改动后只跑 pytest，不要随手打包 exe。**
   一次 PyInstaller 打包 ≈ 3 分钟 + 150 MB 中间产物。确认功能全部做完再出成品。
2. **验证打包版 GUI「窗口能否打开」时，不要 `terminate()` / `kill()`。**
   强杀不会触发 PyInstaller onefile 的临时目录清理，每次泄漏约 113 MB 到
   `%TEMP%\_MEI*`。正确做法：找到窗口后 `PostMessageW(hwnd, WM_CLOSE, 0, 0)` 正常关窗。
3. **`build.bat` / `Start.bat` 必须保持 GBK(cp936) 编码 + CRLF 行尾。**
   详见第 5 节。改这两个文件后必须自检编码与行尾。
4. **测试不引入二进制夹具。** 样本一律在 `tests/samples.py` 里用代码构造
   （已手写 ZIP / ZipCrypto / CAB / ISO 生成器）。
5. **不要往 `release/` 里提交东西**（已 gitignore）。对外发布的是
   GitHub Releases 上的 `WinUnpack-win64.zip`。
6. **解包器不得抛异常穿透**：一律 `return self._result(info, ExtractStatus.XXX, ...)`。
   新增格式要遵循「优先级 + 兜底链」模型，见第 4 节。

## 3. 架构速查

> 本节只回答「**东西在哪**」。设计意图（为什么这样分层、为什么全文件扫描不做稀疏采样等）
> 见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) —— 架构说明只维护那一份，本节不重复展开。

```
src/main.py                GUI 版入口 → 无参数走 gui.launch()，带文件参数转 CLI
src/winunpack_cli.py       CLI 版入口 → 始终走 cli.run_cli()
src/unpacker/cli.py        ★ 业务统一入口：build_parser() 两入口共用；run_cli() 执行
src/gui/main_window.py     ★ GUI 启动核心：launch()；界面主题 _STYLE + CollapsibleGroup
src/gui/worker.py          QThread 工作线程（GUI 不阻塞）

src/unpacker/
├── detector.py            魔数探测 → ArchiveKind；_is_dotnet() 读 PE 数据目录 14 (CLR)
├── registry.py            ★ 解包器注册表：@register 装饰器 + 按 kind 过滤 + priority 排序
├── engine.py              任务派发、兜底链、递归解包、批量执行、试运行
├── context.py             ExtractContext：输出目录/密码/冲突策略/编码/进度/日志/取消
├── models.py              ArchiveKind / ConflictPolicy / ExtractStatus / ArchiveInfo / ExtractResult
├── utils.py               安全路径 safe_join、文件名编码回退、长路径
├── plugins.py             用户插件加载（<exe 同级>/plugins）
└── extractors/
    ├── base.py            ★ BaseExtractor：_prepare_dir / _write_bytes / _copy_stream / _write_slice
    ├── archive.py         ZIP / 7z / RAR / TAR / 单流
    ├── cab.py             原生 CAB（mmap + 按块解压）
    ├── iso.py             原生 ISO9660（Joliet / Rock Ridge）
    ├── installer.py       PE 安装包 / MSI
    ├── dotnet.py          托管 .NET 程序集（内嵌资源 + 可选 ILSpy 反编译）
    ├── carver.py          内嵌归档雕刻
    ├── gamepack.py        游戏资源包
    └── external.py        外部 7-Zip 兜底
```

选解包器的方式：`kind` 匹配 → `priority` 升序 → 逐个 `can_handle()` → 首个成功者胜；
都失败则走兜底链（外部 7-Zip / 雕刻器）。

## 4. 新增一个解包器

1. 在 `src/unpacker/models.py` 的 `ArchiveKind` 加枚举值，并在 `label` 里给中文名。
2. 在 `src/unpacker/detector.py` 加指纹（**只读文件头若干字节**，别整文件读入）。
3. 新建 `src/unpacker/extractors/xxx.py`：

```python
from ..models import ArchiveKind, ExtractStatus
from ..registry import register
from .base import BaseExtractor

@register
class XxxExtractor(BaseExtractor):
    name = "XXX 格式"
    kinds = (ArchiveKind.XXX,)
    priority = 50                 # 数字小者优先；兜底类给大值
    requires = ("some_lib",)      # 可选：缺库时自动降级为「不支持」

    def extract(self, info, ctx):
        out = self._prepare_dir(ctx)              # 建输出目录 + 冲突策略
        for i, member in enumerate(members, 1):
            ctx.check_cancel()                    # 必须响应取消
            self._write_bytes(ctx, out, name, data)   # 内部做安全路径清洗
            ctx.progress(i, total, member)
        return self._result(info, ExtractStatus.SUCCESS, files=..., message=...)
```

4. 在 `src/unpacker/registry.py` 的 `load_all()` 里 import 新模块。
5. 在 `tests/` 加用例（样本用 `samples.py` 构造或手写生成器）。
6. 更新 README 的「支持的格式与实现方式」表 + 「更新日志」。

**大文件必须流式**：用 `_copy_stream` / `_write_slice`，不要 `f.read()` 整个成员。

## 5. 踩坑清单（都是真实踩过的）

### Windows 批处理（`build.bat` / `Start.bat`）

| 坑 | 现象 | 解法 |
|---|---|---|
| **UTF-8 + `chcp 65001`** | 命令被吃掉字符：`echo [3] 开始检查` 报 `'开始检查' is not recognized`；`python` 变成 `thon` | 文件存 **GBK** + `chcp 936` |
| **LF 行尾** | cmd 解析 `^` 续行出错，PyInstaller 多行命令被截断 | 必须 **CRLF**（Edit/Write 工具改完会变 LF，要归一化） |
| **直接调用 `.cmd`** | `where python3` 返回 `python3.cmd`；在 .bat 里直接调用会把控制权交出去且不返回，脚本静默中断 | 一律加 **`call`**：`call "%PYEXE%" ...` |
| 忘记 `cd /d "%~dp0"` | 从别的目录运行时相对路径全错 | 脚本开头先切到自身目录 |

自检：
```python
raw = Path('build.bat').read_bytes()
raw.decode('gbk')                          # 能解出中文
raw.count(b'\r\n') == raw.count(b'\n')     # 全 CRLF
```

### PyInstaller

- `--onefile` 运行时会自解包到 `%TEMP%\_MEI*`（约 113 MB）；**强杀不清理**（见硬性规则 2）。
- 构建失败时若直接 `exit`，`build\`（约 150 MB）会留下 → 失败路径必须走统一的清理分支。
- 惰性 import 的第三方库要显式 `--hidden-import` / `--collect-submodules`，
  否则运行时报 `ModuleNotFoundError`。

### PE / .NET 解析

- **PE32+ 的 `Subsystem` 在可选头 `+0x44`**（PE32 才是 `+0x5C`）；`BaseOfData` 只在 PE32 存在。
- 可选头起始 = `e_lfanew + 4 + 20`（PE 签名 4 字节 + COFF 头 20 字节）。
- **`dnfile` 0.18 API**：`net.struct` 字段是 `MetaDataRva / ResourcesRva`（不是 `MetaData`）；
  `TypeDef.MethodList` 是 `MDTableIndex` **列表**，取值用 `.row_index - 1`；
  用户字符串堆在 `net.user_strings`，`mdtables` 里**没有** `UserStrings` 表。
- .NET 内嵌资源布局 = `[int32 小端长度][原始字节][对齐填充]`，按长度精确截取即可无损还原。
- `ilspycmd` 在中文 Windows 上会向管道写非 UTF-8 字节 → Python 侧**必须二进制捕获**
  （`capture_output=True` 不加 `text=True`），否则读取线程抛 `UnicodeDecodeError`。

### Qt / PySide6

- 纯 `QWidget` **不响应** QSS 的 `background` / `border-radius`：要用 `QFrame`，
  或 `setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)`。
- 可折叠面板：收起后若尺寸策略不收缩，腾出的空间会堆在下方形成空档。
  竖向用 `QSizePolicy.Maximum` + `setVisible()` 后 `updateGeometry()`。
- `focus` 状态若加粗边框，要同步减小 `padding`，否则控件会跳动。

### 沙箱 / 工具

- 批量删除（单命令 >50 文件）会被拦下。规避：`mv` 移出，或 Python `shutil.rmtree`。
- 给 GBK 批处理做字符串替换时，**不要对标签名裸替换** ——
  `replace(':detect_python\r\n', ...)` 会匹配到 `call :detect_python` 那行。
  用行首匹配 `^:label`，或整文件重写。

## 6. 提交前自检

- [ ] `.venv\Scripts\python -m pytest tests -q` 全绿
- [ ] 改了 GUI → 实际启动一次并截图确认（`Start.bat`）
- [ ] 改了 `.bat` → 编码 GBK + 全 CRLF 自检通过
- [ ] 改了打包相关 → `build.bat check` 通过
- [ ] 新增格式 → README 格式表 + 更新日志已同步
- [ ] 没有把构建产物、`exe/` 样本、缓存提交进去（查 `.gitignore`）
