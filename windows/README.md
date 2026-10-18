# Kryra Windows

参考 macOS Kryra 的 Windows 原生托盘应用。C# / WPF 界面，Windows 用户态 HID 后端，本机 HTTP API 和原生 `kryractl.exe`。当前版本 **1.1.0**，目标 **Windows 10 / 11 x64、键盘固件 v3.1.0**。

## 运行

解压 `Kryra-Windows-1.1.0-win-x64.zip`，打开其中的 `Kryra.exe`。保留解压目录内的配套文件。默认发行包附带 .NET Desktop 运行时，成品无需安装 Python、Node、.NET SDK 或前端框架。

正式版下载见 [v3.1.0](https://github.com/xiaoliu-heng/q101-mq80-battery-bar/releases/tag/v3.1.0)，应用 ZIP 与 `v3.1.0-rc.1` 相同。Windows 软件版本 **1.1.0** 与键盘固件版本 **3.1.0** 分别编号。包内 `BuildInfo.json` 记录源码提交与文件校验值；运行时许可见 `THIRD_PARTY_NOTICES.md` 和 `licenses/`，固件风险见包内“免责声明.md”。

首次运行显示控制面板，关闭窗口后继续驻留系统托盘。点击托盘电量可重新打开；托盘菜单或设置页可退出。登录启动默认关闭，可在设置页自选。

1. USB：连接数据线并切至 USB 模式（本项目设备为 Fn+5）。2.4G：插入接收器并切至 2.4G 模式（Fn+4），先按键唤醒。
2. 自动连接优先 USB；也可在主面板选择 USB、2.4G 或具体设备。同类设备超过一台时要求明确选择。
3. 在纯色、闪烁、呼吸、彩虹之间选择，调整颜色、亮度、时长和周期。编辑和灯带动画仅预览，点击“发送提醒”后发送一次 PLAY，由键盘自行播放、到期恢复。
4. “恢复原灯效”主动取消。退出、切换设备和电脑休眠尝试释放自身仍有效的通知；唤醒仅恢复电量查询。纯查询后退出不会取消其他程序的控制。

默认预览彩虹、亮度 25%、时长 5 秒。电量是键盘缓存的百分比，默认每 60 秒查询，可设为 30～3600 秒；设置页提供常用间隔。断开后保留上次读数并标注陈旧。接收器无回复时退避，通知期间跳过自动查询。降低收发频率不代表已测得续航改善。

连续灯带由九个逻辑采样点插值。预览为颜色示意，不表示光学读数。无线本地灯效遵循键盘原有休眠，可能提前结束，唤醒不重播。

## Codex Skill

在“设置 → Codex Skill → 管理”查看安装位置和状态，点击“安装 Skill”或“更新 Skill”。可以选择安装父目录，应用在其中创建 `kryra` 子目录。默认使用 `%USERPROFILE%\.agents\skills`，对应 [OpenAI 官方文档的用户 Skill 目录](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills)。

Skill 使用 Windows 原生 CLI 查询电量和发送有限时长提醒。安装目录保存说明、UI 元数据和 `app.json` 路径绑定；API token 不写入 Skill。请保留应用目录，移动后通过此页面更新绑定。已有其他来源的同名目录时不会覆盖；只有带本应用归属标记的目录可更新。可在 Codex 使用 `$kryra`，未出现时重新打开 Codex。

1.1.0 同时修正选择框及菜单项的垂直居中，将底部状态文字和连接状态增至 13 号，并放大托盘电量数字、适配系统任务栏的深浅色。

## CLI 与本机 API

`kryractl.exe` 和 `Kryra.exe` 位于同一目录，CLI 自动读取当前用户凭证：

```powershell
.\kryractl.exe battery --refresh
.\kryractl.exe settings --poll-interval 120
.\kryractl.exe solid 34D399 --brightness 25 --seconds 3
.\kryractl.exe blink FFAA33 --period 1000 --seconds 5
.\kryractl.exe breathe 38BDF8 --period 2000 --seconds 5
.\kryractl.exe rainbow --period 5000 --seconds 5
.\kryractl.exe release
.\kryractl.exe show
.\kryractl.exe quit
.\kryractl.exe launch
```

API 仅监听 `127.0.0.1` 动态端口，所有接口均需 Bearer token。配置在 `%LOCALAPPDATA%\Kryra\api.json`，应用目录 ACL 仅授予当前用户。每次启动轮换 token，正常退出只清理本实例的运行凭证。设置单独保存在 `settings.json`。可用 `KRYRA_HOME` 或应用参数 `--home` 指定隔离目录。

接口与 macOS 基本一致：`GET /v1/health`、`/v1/status`、`/v1/battery`、`/v1/battery?refresh=true`、`/v1/settings`；`POST /v1/refresh`、`/v1/settings`、`/v1/lighting`、`/v1/lighting/release`。另有本机 `POST /v1/window/show` 与 `/v1/app/quit`。设置只接受 `battery_poll_interval_seconds` 整数，不触发查询。

本地通知示例：`{"mode":"breathe","color":"34D399","brightness":25,"period_ms":1000,"duration_seconds":3}`。可选 `request_id` 用于同一通知的固件去重。API 的高级 `frame` 接受九个 `colors` 和 0.5～10 秒有限租约，无心跳。应用中的四种提醒均使用 PLAY。

Origin、错误 Host、重复头、分块报文、无效 token、超大请求和无限通知会被拒绝；接口限制为八个并发连接。详情见同目录“使用说明.html”。

## 架构

- `Kryra.Core/Protocol.cs`：严格协议、版本 / 能力检查、参数编码。
- `WindowsHid.cs`：SetupAPI 枚举、HIDP_CAPS 检查、Overlapped I/O、取消及超时。只打开 `320F:5055` / `320F:5088` 的 `FF60:61` collection，33 字节 Windows 缓冲区包含零 Report ID 和 32 字节逻辑数据。
- `KeyboardController.cs`：UI/API 共用串行工作队列、电量轮询、通知状态与自身控制释放。
- `LoopbackServer.cs`、`PrivateStore.cs`：本机服务、凭证 ACL、设置持久化。
- `SkillInstaller.cs`：安装归属检查、暂存替换、CLI 路径绑定；`skill/kryra` 为随包资源。
- `Kryra.App`：固定 368×584 DIP 内容、深浅色、连续灯带、系统托盘、原生取色器。
- `Kryra.Cli`：原生命令行。运行时不依赖 Python、网页容器或云服务。

报告适配依据 [Microsoft HIDP_CAPS](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/hidpi/ns-hidpi-_hidp_caps) 与 [SetupDiGetDeviceInterfaceDetailW](https://learn.microsoft.com/en-us/windows/win32/api/setupapi/nf-setupapi-setupdigetdeviceinterfacedetailw)。协议与产品交互参照 [Kryra 设计文档](../docs/KRYRA_DESIGN.md)。

## 构建与检查

使用 [.NET 8 Windows SDK](https://dotnet.microsoft.com/en-us/download/dotnet/8.0)，无需 Visual Studio IDE。当前开发使用独立 SDK 8.0.426；构建脚本也接受其他 .NET 8 SDK 的 `dotnet.exe` 路径。

```powershell
python windows/build.py --sdk C:\path\to\dotnet.exe
dotnet run --project windows/Kryra.Tests -c Release
python windows/tests/validate_app.py --app dist/Kryra-Windows/Kryra.exe --cli dist/Kryra-Windows/kryractl.exe --out test-results/kryra-windows/e2e.json
dist/Kryra-Windows/Kryra.exe --demo --ui-test test-results/kryra-windows/ui
```

`--demo` 始终显示“演示”，API 返回 `demo: true`，不打开实体 HID。端到端测试使用隔离目录，真实启动原生 EXE / CLI / TCP 服务。界面检查输出四种模式、设置页、深浅色及 100%/125%/150%/200% 的应用自有视图渲染；这些图片不等于不同显示器的系统 DPI、标题栏与多屏验收。实机与视觉验证范围见 `VALIDATION.json`。

Skill 安装器的自动检查使用隔离目录；`--demo --ui-test` 的安装目标限制在测试 profile 内，不写入真实用户的 Skill 目录。

2026-10-18，Windows 原生后端通过 2.4G 实机验证：读取 v3.1.0 构建 3 和 97% 电量，亮度 0 / 60、橙色纯色、青色闪烁、紫色呼吸、彩虹六项检查均收到正常设备回复。四种本地灯效在无心跳期间保持播放并到期结束，使用者确认全部显示正常且恢复原灯效。开始时曾遇到超时，随后原生与参考工具都恢复响应，未据此认定已定位超时原因。USB 原生后端按使用者安排稍后测试，当前仍不计为实机通过。

已有发行目录时，可用 `python windows/build.py --package-only` 只更新说明、验证记录和 ZIP；脚本先核对已发布二进制哈希，不重新编译或覆盖运行中的应用。
