# v3.1.0 正式发布

本次正式发布包含 **键盘固件 v3.1.0（构建 3）**、**Kryra Windows 1.1.0** 和 **Kryra macOS 1.3.4**。三者独立编号。

2026-10-19 更新：macOS 应用升级至 **1.3.4**，修复新系统上的选中文字模糊，并恢复等宽原生玻璃胶囊；固件和 Windows 包沿用 `v3.1.0-rc.1`。v2 与 v3 继续共存：只需要 Fn+B 电量条可选 [v2](https://github.com/xiaoliu-heng/q101-mq80-battery-bar/releases/tag/battery-bar-v2)，需要主动查询电量、灯光通知与 Kryra / Skill 集成可选 v3。

## 下载与使用

| 附件 | 用途 |
| --- | --- |
| `MQ80-CS836B-V0109-20241220-BatteryBar-SideHID-v3.1.0-USB-RF-EXPERIMENTAL.bin` | 可供官方升级工具导入的完整固件 BIN |
| `Kryra-Windows-1.1.0-win-x64.zip` | Windows x64 原生应用、CLI、Skill 资源和 .NET 运行时 |
| `Kryra-Windows-1.1.0-win-x64.sha256` | Windows ZIP 校验值 |
| `Kryra-1.3.4-universal.dmg` | macOS 安装镜像，推荐；Apple Silicon / Intel 通用版 |
| `Kryra-1.3.4-universal.zip` | 相同 macOS 应用的 ZIP 归档 |
| `Kryra-1.3.4-universal-SHA256SUMS.txt` | Mac DMG / ZIP 的 SHA-256 校验清单 |
| `Kryra-1.3.4-universal-manifest.json` | Mac 版本、源码对应关系、架构、签名与验证信息 |
| `SHA256SUMS.txt` | 全部发布附件（清单自身除外）的 SHA-256 清单 |
| `RELEASE-MANIFEST.json` | 固件、Windows、macOS 的源码提交、版本、附件大小与校验结果 |

完整解压 Windows ZIP 后打开 `Kryra.exe`，保留同目录全部配套文件。无需另装 Python、Node 或 .NET。目标系统为 Windows 10 / 11 x64，本次实测 Windows 11 x64。使用方法见 [Windows 说明](../windows/README.md)。Windows 包包含运行时许可，未包含厂商刷机工具。

macOS 用户优先下载 DMG，将 `Kryra.app` 拖入 `Applications` 后打开；ZIP 提供相同应用。支持 macOS 13+ 与 Apple Silicon / Intel，原生 Liquid Glass 需要 macOS 26+，无需另装 Python 或 Node。当前采用本地 ad-hoc 签名，**未经过 Developer ID 公证**。详见 [Mac 使用说明](../macos/README.md)。

## 固件与刷写

基线为 **MQ80-CS836B-V0109-20241220**，保留 Fn+B 侧灯电量条，支持 USB / 2.4G 电量与灯光控制。新增纯色、闪烁、呼吸和彩虹的本地播放：一次 PLAY，有限时长，无需电脑持续发帧或心跳，到期恢复原灯效。无线原有休眠可提前结束通知，唤醒不重播。蓝牙灯光控制未实现。

- 固件大小 / codesize：**129732** 字节。
- Telink CRC：**F2979115**。
- SHA-256：`5757c625adf427a4932a9fb9e8e7be0e5a4a88311e4484278d771c5c1b29cf0a`。

先保存 VIA 键位和宏，确认自己的硬件适用该 MQ80 基线，再按 [刷写步骤](../README.md#刷写步骤) 用 USB 导入上述完整 BIN。核对 v3.1.0 的大小和 CRC 后才开始写入；升级目标是键盘 `320F:5055`，接收器 `320F:5088` 不刷写。

实验固件可能导致设备失效、配置或配对丢失；本项目没有已验证的 USB 强制救援或自动回滚方法。单台设备通过不能证明所有批次兼容，使用前阅读 [风险与免责声明](../DISCLAIMER.md)。

## Windows 1.1.0

原生 WPF 界面与 Win32 HID 后端，支持 USB / 2.4G 电量查询、系统托盘电量、四种本地灯效、颜色、亮度、持续时间、周期及恢复原灯效；提供深浅色、本机 API、原生 `kryractl.exe`、Codex Skill 安装与更新。

本版补全“设置 → Codex Skill → 管理”入口，修正选择框和弹出菜单的文字垂直居中，放大底部状态文字和托盘电量数字。应用及 Skill 不在发布包中保存个人 API token、设备路径绑定或用户设置。

## macOS 1.3.4

Swift / SwiftUI / AppKit 原生菜单栏应用，显示键盘电量和连续竖向渐变灯带预览；支持纯色、闪烁、呼吸、彩虹、颜色、亮度、持续时间与周期。本机 API、原生 CLI 和一键安装的 Codex Skill 共用控制逻辑。

电量默认每 60 秒查询，可配置为 30～3600 秒；四种通知灯效由键盘本地播放，到期自动恢复，无持续 RGB 帧流或心跳。1.3.4 将玻璃直接应用于选中按钮内容，修复文字模糊，同时保留等宽胶囊的原生 Liquid Glass 外观。继续包含菜单栏弹窗和独立窗口圆角修复，固定布局不会因切换状态或设置而出现滚动条。仅支持固件 3.1.0。

## 验证范围

- 固件原有离线检查覆盖协议、本地动画、原休眠路径、OTA 模型及可复现构建；正式发布重新核对完整 BIN 的头部、长度、Telink CRC 和 SHA-256，并核对三端程序源码与预发布版本一致。
- 使用者已确认 v3.1.0 在 USB 和 2.4G 下基础控灯、四种本地灯效及到期恢复正常。2.4G 首轮曾出现本地灯效无法切换，随后恢复，原因未定位。
- Windows 原生后端已通过 2.4G 实机电量、亮度、纯色、闪烁、呼吸、彩虹和恢复验证；完整灯效实机测试使用 1.0.0，同协议的 1.1.0 已再次确认读取固件和电量。**Windows 原生 USB 实机测试仍待补充**。
- Windows 11 上 11 组核心检查和 17 项打包应用 / CLI 检查通过；后者使用明确标识的隔离演示模式。38 张界面渲染覆盖深浅色与 100%～200% 缩放，26 个选择框文字中心偏差为 0 DIP。详情见 [Windows 验证记录](../windows/VALIDATION.json)。
- macOS 1.3.4 已通过 188 项核心、61 项布局、61 项应用 / CLI 端到端检查，共 310 项；实际独立窗口已在 macOS 27.0.1 的隔离演示环境中检查，四种灯效的选中文字清晰、玻璃胶囊等宽、设置页尺寸正常。ZIP / DMG 已检查完整性、通用架构、SDK 和签名。菜单栏弹窗本轮完成固定布局检查，电脑控制工具未能绑定原生弹窗，未补做实际弹窗外观验收；Intel 实机仍待验证。详情见 [Mac 验证记录](../macos/VALIDATION-v1.3.4.json)。
- 未测量电流、实际电池容量、物理刷新率或续航改善；Windows 10、多显示器 DPI 切换及其他硬件批次未实测。

固件与 Windows 二进制源码提交：[`18eed04`](https://github.com/xiaoliu-heng/q101-mq80-battery-bar/commit/18eed049e62a78874da4c72a86ec8575d170c10f)。Windows 包的 `BuildInfo.json` 与此提交一致。

macOS 1.3.4 对应源码标签 [`kryra-macos-v1.3.4`](https://github.com/xiaoliu-heng/q101-mq80-battery-bar/tree/kryra-macos-v1.3.4)，准确提交和安装包 SHA-256 见 `Kryra-1.3.4-universal-manifest.json`。原 `v3.1.0` 标签继续对应最初正式发布；本次更新安装包、说明和校验清单，不移动固件标签。固件与 Windows 二进制校验值保持不变。
