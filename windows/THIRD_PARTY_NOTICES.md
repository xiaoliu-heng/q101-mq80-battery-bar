# Kryra Windows 第三方运行时

Windows x64 便携包附带 Microsoft .NET Runtime 8.0.32 和 Windows Desktop Runtime 8.0.32。用户无需另外安装 .NET。以下文件保留对应运行时包附带的原始许可与第三方通知：

- `licenses/dotnet-runtime-8.0.32/LICENSE.TXT`
- `licenses/dotnet-runtime-8.0.32/THIRD-PARTY-NOTICES.TXT`
- `licenses/windows-desktop-8.0.32/LICENSE`

许可文件来自构建时还原的官方 NuGet 包 `Microsoft.NETCore.App.Runtime.win-x64/8.0.32` 与 `Microsoft.WindowsDesktop.App.Runtime.win-x64/8.0.32`，按原内容保存。源码与许可来源：[.NET Runtime](https://github.com/dotnet/runtime)、[Windows Desktop](https://github.com/dotnet/windowsdesktop)、[WPF](https://github.com/dotnet/wpf)、[Windows Forms](https://github.com/dotnet/winforms)。各组件按其附带许可提供。

此包包含 Kryra 原生应用、CLI 和 Skill 资源，不包含厂商刷机工具。键盘固件单独下载；厂商固件与项目的其他第三方内容说明见 [仓库第三方说明](https://github.com/xiaoliu-heng/q101-mq80-battery-bar/blob/v3/third-party/README.md)。
