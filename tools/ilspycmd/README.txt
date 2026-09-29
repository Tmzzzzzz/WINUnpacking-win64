tools/ilspycmd/ —— 随包携带的 C# 反编译器
==========================================

本目录是 **ILSpy 命令行工具（ilspycmd）** 的 .NET 8 构建，取自 NuGet 包
`ilspycmd`（版本 9.1.0.7988），用于把托管 .NET 程序集的 IL 还原成 C# 源码。

来源
----
- 项目主页：https://github.com/icsharpcode/ILSpy/
- NuGet 包：https://www.nuget.org/packages/ilspycmd/9.1.0.7988
- 包内路径：tools/net8.0/any/

许可
----
ILSpy 以 **MIT License** 发布，
Copyright (c) 2011-2025 AlphaSierraPapa for the ILSpy project。
完整许可文本见同目录下的 LICENSE.txt。按 MIT 要求保留版权与许可声明，可自由再分发。

运行要求
--------
本构建目标框架为 `net8.0`（框架依赖），需要目标机器安装
**.NET 8 运行时**（`Microsoft.NETCore.App 8.0` 或 .NET 8 Desktop Runtime）。
Windows 10/11 不自带该运行时；若未安装，WinUnpack 会自动跳过反编译并给出提示，
内嵌资源与结构信息仍可正常提取（那部分不依赖 .NET）。

不想装运行时？把 `ILSPYCMD_PATH` 环境变量指向本机已有的 ilspycmd 即可覆盖本目录。

为什么把这个放进 tools/ 而不是让用户自己装
------------------------------------------
`dotnet tool install -g ilspycmd` 需要 **.NET SDK**，而绝大多数只想解包的用户
并没有 SDK。随包携带可以让「解包即得源码」在没有任何开发环境的机器上开箱可用
（前提是装了 .NET 8 运行时）。

升级方式
--------
从 NuGet 取新版本包，解出 tools/<tfm>/any/ 下的全部文件覆盖本目录即可：

    curl -sL -o ilspycmd.nupkg \
      "https://www.nuget.org/api/v2/package/ilspycmd/<版本>"
    # 解压后覆盖 tools/net8.0/any/* 到本目录
    # 注意：目标框架要与目标机器可用的 .NET 运行时匹配
