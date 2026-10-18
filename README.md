# Q101 / MQ80 · Fn+B 侧灯电量条

把 IQUNIX Q101 / MQ80 的 **Fn+B 电量查询**改成侧边灯条：长度表示剩余电量，颜色由红到绿，约 3 秒后恢复原灯效。附带原生 macOS 离线测试软件 **MQ80 Firmware Lab**。

**v2 和 v3 会并存，保留各自的下载入口，由使用者按需求选择。** 只需要 Fn+B 电量条可选 **v2**；需要电脑主动查询电量、灯光通知和 Skill 自动化可选 **v3**。已经使用 v2 且满足需求的用户，可以继续使用，无需为了版本号升级。

**项目使用者已在一台 Q101 的 v2 固件上确认：有线、2.4G、蓝牙和 VIA 均正常使用。** v3 的验证范围见[发布说明](docs/RELEASE-v3.1.0-rc.1.md#验证范围)。其他硬件批次及刷写失败后的强制恢复尚未验证。刷写前请阅读[风险与免责声明](DISCLAIMER.md)。

## 目录

- [v2 和 v3 怎么选](#v2-和-v3-怎么选)
- [功能与实测](#功能与实测)
- [Kryra 控制应用](#kryra-控制应用)
- [下载与校验](#下载与校验)
- [刷写步骤](#刷写步骤)
- [故障与恢复](#故障与恢复)
- [macOS 测试软件](#macos-测试软件)
- [Windows 原生 Kryra](#windows-原生-kryra)
- [目录与开发](#目录与开发)
- [用途与免责声明](#用途与免责声明)

## v2 和 v3 怎么选

| 对比项 | v2 · Fn+B 电量条 | v3 · 电量条 + 电脑控制 |
| --- | --- | --- |
| 适合谁 | 只想在键盘上查看电量 | 想在电脑上看电量、发送灯光提醒或接入自动化 |
| Fn+B 电量条 | 支持，约 3 秒后恢复原灯效 | 保留 |
| 电脑主动查询电量 | 不支持 | 支持 USB / 2.4G，Kryra 在菜单栏或系统托盘显示百分比 |
| 电脑发送灯光通知 | 不支持 | 纯色、闪烁、呼吸、彩虹，由固件本地播放并到期恢复 |
| 本机 API / CLI / Codex Skill | 不支持 | 通过 Kryra 查询电量、发送短时提醒 |
| 日常配套软件 | 使用 Fn+B 无需安装软件 | 电脑控制使用 Kryra，提供 macOS / Windows 原生应用 |
| 当前下载 | [电量条 v2](https://github.com/xiaoliu-heng/q101-mq80-battery-bar/releases/tag/battery-bar-v2) | [固件 v3.1.0 预发布](https://github.com/xiaoliu-heng/q101-mq80-battery-bar/releases/tag/v3.1.0-rc.1) |

两版的 Fn+B 都可独立使用，无需电脑应用常驻。v3 的电脑查询和新通知需要 Kryra 运行；上述四种通知灯效只需发送一次命令，无需持续发帧或心跳，原有无线休眠可提前结束通知。电脑端电量查询和控灯目前仅支持 USB / 2.4G，不支持蓝牙。

**当前 Kryra 仅支持固件 v3.1.0，不能搭配 v2 使用。** 固件与软件分别编号：固件 v3.1.0 对应本次提供的 Kryra macOS 1.3.2 / Windows 1.1.0。MQ80 Firmware Lab 是可选的离线测试软件，不用于读取真机电量或控制真机灯光。

每把键盘一次刷入一版固件，按[刷写步骤](#刷写步骤)核对所选版本的文件和校验值。两版都基于同一 MQ80 V0109 官方固件修改，均为实验固件；目前没有可用于比较两版续航的实测数据。

## 功能与实测

### 电量条效果

- 按 **Fn+B** 显示电量，松开后继续显示；再次按下会重新计时。
- 9 个逻辑侧灯按电量比例点亮，末段用部分亮度表示不足一格的电量。颜色连续变化：低电量偏红、50% 黄色、100% 全长绿色。
- 约 **3 秒**后恢复原侧灯状态：原本开启则恢复灯效，原本关闭则熄灭。查询期间临时覆盖连接或充电提示，主键区灯光不受电量条代码直接影响。

<img src="docs/images/q101-battery-full.jpg" width="660" alt="Q101 实机：Fn+B 查询时，满电显示全长绿色侧灯">

*Q101 实机满电效果。电量取自原固件的百分比缓存，实际长度和颜色观感会受灯珠及扩散罩影响。*

<a id="已验证范围"></a>

### v2 已验证范围

基于 **MQ80-CS836B-V0109-20241220** 官方固件修改。实测路径为 **Q101 → 官方 MQ80 V0109 → 电量条 v2**，以下均为修改版的使用者实机反馈：

| 项目 | 结果 |
| --- | --- |
| 刷写 | `OTA Success` |
| USB 有线 | 正常 |
| 2.4G | 正常 |
| 蓝牙 | 正常 |
| VIA | 正常使用 |
| Fn+B 电量显示 | 满电时侧灯全长绿色 |

以上来自单台 Q101，不能代表所有 Q101 / MQ80 批次。全部电量档位、长期稳定性、续航和强制恢复仍需进一步验证。[完整验证记录](docs/VALIDATION.md)

## Kryra 控制应用

Kryra 是日常使用的原生 macOS 菜单栏应用，直接通过 IOKit 访问键盘，不依赖 Python 或云服务。运行支持 macOS 13+；**1.3.2 使用 SDK 26.5 / Swift 6.3.3 构建，在 macOS 26+ 使用原生 Liquid Glass**，旧系统使用 Material。

- 菜单栏显示键盘电量，支持 USB 与 2.4G；默认每分钟查询，可设为 30 秒～1 小时，重启保留。
- 连续渐变的竖向灯带预览；纯色、闪烁、呼吸和彩虹均由固件自行播放，默认亮度 25%、持续 5 秒。编辑只预览，点击发送才下发一次通知。
- 主面板固定尺寸，设置独立成页；长提示、连接变化和模式切换不会引入滚动条或突然挤窄内容。
- 本机 API 与 `kryractl` 共用设备控制器，可供自动化程序调用；一键安装 skill 后，可让 Codex 查询电量或发送短时灯光提醒。

<img src="macos/previews/kryra-v1.3.0-light.png" width="310" alt="Kryra 浅色主面板：竖向渐变灯带、电量和四种本地通知灯效"> <img src="macos/previews/kryra-v1.3.0-settings.png" width="310" alt="Kryra 设置页：电量默认每一分钟查询，可配置间隔">

截图为 SDK 26.5 构建在当前 macOS 15.7.4 上的兼容外观，数据来自演示模式；macOS 26 的原生玻璃效果待新系统实测。[深色预览](macos/previews/kryra-v1.3.0-dark.png)。

安装、构建、API 与 skill 用法见 [Kryra README](macos/README.md)；Windows 版见 [Windows README](windows/README.md)。跨平台行为记录在 [产品与技术设计文档](docs/KRYRA_DESIGN.md)，其中包括 UI、轮询规则、USB/RF 协议、平台 HID 适配与验收清单。

使用者于 2026-10-17 [反馈此前 Kryra 真机连接测试正常](macos/hardware-confirmation-20261017.json)，未指定连接方式或功耗数据。新增 SDK 26 / 查询配置的离线结果见 [1.3.0 验证记录](macos/VALIDATION-v1.3.0.json)，不据此宣称续航改善。

## 下载与校验

### v2 · Fn+B 电量条

在 [电量条 v2 的 Assets](https://github.com/xiaoliu-heng/q101-mq80-battery-bar/releases/tag/battery-bar-v2) 下载固件、macOS 离线测试 App 和校验清单。此版本继续提供下载，适合只需要 Fn+B 电量条的用户。固件也可从仓库获取：[电量条固件](simulator/assets/battery-bar-v2.bin) · [官方 MQ80 镜像](simulator/assets/stock.bin)。测试软件源码位于 [`simulator/`](simulator/)。

Release 文件说明：

| 文件 | 用途 |
| --- | --- |
| `MQ80-CS836B-V0109-20241220-FnB-BatteryBar-v2-EXPERIMENTAL.bin` | 完整修改版固件，供官方工具导入 |
| `MQ80-CS836B-V0109-20241220-STOCK.bin` | 官方 MQ80 应用镜像，供符合恢复条件时使用；不是 Q101 整片备份 |
| `MQ80-Firmware-Lab-v2.1-native-macOS-arm64.zip` | 可双击的原生 macOS 离线测试 App，内置两份固件 |
| `SHA256SUMS.txt` | 下载文件的 SHA-256 清单 |
| `unicorn-2.1.4-source.tar.gz` | App 所用 Unicorn 的对应上游源码，普通使用者无需下载 |

仓库中的短文件名 BIN 与 Release 对应镜像内容相同。GitHub 的 `Source code` 压缩包为源码，不是 App。

| 固件 | codesize / 字节数 | CRC |
| --- | ---: | --- |
| 电量条 v2 | **126756** | **068F4D5A**（工具可能显示 `68F4D5A`） |
| 官方 MQ80 V0109 | 126292 | B00C451E |

v2 固件 SHA-256：

```text
f3e86554be1ed5faf11750fceea85980c9ccd080912be4552e8317fb09ef7719
```

Windows PowerShell 校验示例（按实际下载文件名调整）：

```powershell
Get-FileHash .\battery-bar-v2.bin -Algorithm SHA256
```

macOS / Linux：`shasum -a 256 battery-bar-v2.bin`。v2 发布文件校验值见 [SHA256SUMS.txt](firmware/SHA256SUMS.txt)。校验一致只证明文件完整，不代表硬件兼容。

### v3 · 电量条与 Kryra 控制

在 [v3.1.0-rc.1 的 Assets](https://github.com/xiaoliu-heng/q101-mq80-battery-bar/releases/tag/v3.1.0-rc.1) 下载，当前为预发布：

| 文件 | 用途 |
| --- | --- |
| `MQ80-CS836B-V0109-20241220-BatteryBar-SideHID-v3.1.0-USB-RF-EXPERIMENTAL.bin` | 完整刷机镜像，固件 3.1.0、构建 3 |
| `Kryra-Windows-1.1.0-win-x64.zip` | Windows 10 / 11 x64 原生便携包，附运行时 |
| [Kryra-1.3.2-universal.dmg](https://github.com/xiaoliu-heng/q101-mq80-battery-bar/releases/download/v3.1.0-rc.1/Kryra-1.3.2-universal.dmg) | macOS 13+ 安装镜像，Apple Silicon / Intel 通用版 |
| [Kryra-1.3.2-universal.zip](https://github.com/xiaoliu-heng/q101-mq80-battery-bar/releases/download/v3.1.0-rc.1/Kryra-1.3.2-universal.zip) | 相同 macOS 应用的 ZIP 归档 |
| `Kryra-1.3.2-universal-SHA256SUMS.txt` | Mac DMG / ZIP 的 SHA-256 校验清单 |
| `Kryra-1.3.2-universal-manifest.json` | Mac 版本、架构、源码对应关系与验证信息 |
| `SHA256SUMS.txt` | 固件与 Windows 附件的 SHA-256 校验清单 |
| `RELEASE-MANIFEST.json` | 固件与 Windows 的源码提交、版本、附件大小与校验结果 |

v3.1.0 固件的 **codesize 为 129732，CRC 为 F2979115**，SHA-256 为 `5757c625adf427a4932a9fb9e8e7be0e5a4a88311e4484278d771c5c1b29cf0a`。仓库中的 [`side-hid-rf-v3.1.0.bin`](firmware/experimental/side-hid-rf-v3.1.0.bin) 与上述长文件名镜像内容相同。Mac 安装时将 `Kryra.app` 拖入 `Applications`；软件包采用本地 ad-hoc 签名，未经过 Developer ID 公证。本次上传的包已通过解压后 61 项应用 / CLI 检查，四个附件均完成回下载与 SHA-256 核对，见 [Mac 发布核对记录](macos/release-v3.1.0-rc.1.json)。

Kryra 默认每 60 秒查询电量，更新间隔可设为 30～3600 秒；灯光编辑只做本机预览，点击发送后才下发通知。安装、API、CLI、Skill 及各平台验证范围见 [macOS 说明](macos/README.md)、[Windows 说明](windows/README.md)和[发布说明](docs/RELEASE-v3.1.0-rc.1.md)。

<a id="使用官方-windows-工具刷入"></a>

## 刷写步骤

### 1. 准备工具和键盘

1. 使用 **Windows 电脑、可靠的 USB 数据线和稳定供电**，保存重要 VIA 键位及宏配置，并预留另一把键盘或屏幕键盘。
2. 下载并解压 [IQUNIX 官方 MQ80 V0109 升级包](https://cdn.shopify.com/s/files/1/0676/7853/5986/files/MQ80-CS836B-V0109-20241220.exe.zip?v=1744019164)。[官方说明](https://iqunix.com/blogs/news/mq80-via-guide)
3. 连接键盘，切到 **USB 模式**（本项目设备为 Fn+5），关闭 VIA 等可能占用设备的工具，暂时拔下 2.4G 接收器。升级目标是键盘 `320F:5055`，不要选择接收器 `320F:5088`。
4. 先确认自己的具体硬件适用 MQ80 固件。项目实测先升级官方 V0109，再刷修改版；跨刷后设备可能识别为 MQ80。官方版按[官方升级步骤](https://iqunix.com/pages/mg-series-firmware-update-steps-guide)操作，确认连接与输入正常后再继续。

以下调试入口对应上述包内的 EXE：大小 `2580992` 字节，SHA-256 为 `ae26c52d3f4b525870530d7acb387a294b512cae1659038127574dbb7fd1b938`。

### 2. 导入并核对固件

1. 运行解压后的 EXE，选择 **Get Device Fw Version**，确认设备有回复并记录当前 CRC；官方 V0109 为 `B00C451E`，已刷修改版的设备应与当前版本的校验值一致。无回复或版本无法确认时，先排查再继续。
2. 打开 **Debug Mode**，输入小写 **`telink`**，点击 **show Debug surface**。
3. 点击 **open bin**，选择完整修改版 `.bin`。
4. 核对文件路径及所选固件的校验值：**v2 为 codesize = 126756、crc = 068F4D5A / 68F4D5A**；**v3.1.0 为 codesize = 129732、crc = F2979115**。工具默认加载内嵌官方镜像，不能只看文件名或按钮状态。

**导入文件不会刷写，点击 Start 才开始写入键盘。**

### 3. 刷写并检查

1. 点击 **Start**，保持 USB 和供电，不拔线、不切模式、不让电脑休眠。
2. 等待 **进度 100% 且显示 `OTA Success`**，设备重新出现后再查询 CRC，应与所刷版本一致：**v2 为 068F4D5A，v3.1.0 为 F2979115**。
3. 检查有线输入、Fn+B 电量条及约 3 秒后的灯效恢复；再分别检查 2.4G、蓝牙与 VIA。2.4G 切换键在本项目设备上为 Fn+4，改过键位时以自己的配置为准。

本项目 macOS 测试软件用于离线验证，不能代替官方工具刷写键盘。

## 故障与恢复

| 状态 | 处理方式 |
| --- | --- |
| 传输或 CRC 报错 | 结束失败会话，保持供电并等待退出 OTA；分析到的超时路径约 120 秒，但不保证所有故障都能退出。先重新查询版本，有回复再重试 |
| 功能异常，但工具仍能查询版本 | 用同一调试入口导入官方 `STOCK.bin` / `stock.bin`，核对 **126292 / B00C451E** 后按正常流程恢复 |
| USB 不识别或工具始终无回复 | **没有已验证的纯 USB 强制恢复步骤。** 联系厂商，可能需要拆机或硬件编程器处理 |

`Stop` 不会自动恢复固件，恢复出厂设置也不是已证实的救砖入口。离线测试不能证明新镜像启动失败后会自动回滚。官方 MQ80 镜像不包含 Q101 联名固件、个人设置或配对信息备份。

## macOS 测试软件

**MQ80 Firmware Lab 2.1** 使用原生 SwiftUI / AppKit 窗口，内置官方固件和电量条固件，可加载相同地址布局的 `.bin`，执行已建模路径的真实固件指令。

<img src="docs/images/simulator-workbench.png" width="1000" alt="MQ80 Firmware Lab：虚拟键盘、侧灯预览、实体键盘输入和环境设置">

| 功能 | 说明 |
| --- | --- |
| 按键测试 | 虚拟键盘、实体键盘组合键、Fn+B、释放所有按键、HID 报告 |
| 灯光与环境 | 主灯及侧灯 RGB 预览，电量、充电和连接条件设置 |
| 回归与检查 | 10 个内置场景、双固件对比、虚拟 OTA 分支、寄存器和内存查看、报告导出 |

### 快速使用

1. 解压 App ZIP，双击 `MQ80 Firmware Lab 2.1.app`。支持 **Apple Silicon，目标 macOS 13+**；当前在 macOS 26.5.1 实测。Python 已打包。应用未公证，首次打开先核对来源与校验值，再按系统“隐私与安全性”提示处理。
2. 选择电量条固件，设置电量，点击“Fn+B 查询电量”。运行时钟后约 3 个**虚拟秒**恢复原灯效；暂停会冻结倒计时。
3. 用 ⇧⌘K 开启实体键盘输入，默认 **右 Command+B** 对应虚拟 Fn+B；⌘. 退出。取消“组合键”或点击“松开所有键”可释放按键。
4. 在“场景与回归”运行测试，用 ⌘S 导出报告、⌘O 导入兼容固件。[详细说明与构建方法](simulator/README.md)

软件不连接或刷写实体键盘；电量和连接条件是测试输入。它不是完整芯片模拟器，不能验证真实功耗、无线首键必达或救砖能力。

## Windows 原生 Kryra

[Kryra Windows 1.1.0](windows/README.md) 参考 macOS 版的固定尺寸面板与连续灯带，使用 C# / WPF 和原生 Windows HID 接口实现，目标为 Windows 10 / 11 x64、键盘固件 **v3.1.0**。支持 USB / 2.4G 电量查询、系统托盘电量、四种本地提醒、颜色与亮度、定时恢复、深浅色、本机 HTTP API、原生 `kryractl.exe` 和 Codex Skill 安装／更新。

运行 `python windows/build.py` 生成附带运行时的便携包，解压后打开 `Kryra.exe`；成品无需安装 Python、Node 或 .NET。预览与编辑不发送 HID，点击发送才下发一次 PLAY。验证范围和当前实机限制见 [Windows 验证记录](windows/VALIDATION.json)。

## 目录与开发

```text
firmware/      补丁源码、构建说明和离线验证记录
macos/         原生 macOS Kryra、HID 后端、CLI、Skill 与构建脚本
simulator/     原生 macOS 测试软件、测试和内置固件
windows/       原生 Windows Kryra、HID 后端、CLI、Skill 与构建脚本
docs/          实机验证记录、发布检查和图片
scripts/       固件完整性检查
third-party/   第三方许可及来源
```

厂商完整固件未在本项目开源，仓库提供新增补丁及测试软件源码。[补丁构建](firmware/README.md) · [验证记录](docs/VALIDATION.md) · [发布检查](docs/PUBLICATION_CHECKS.json)

## 用途与免责声明

本项目仅供**个人学习、研究和自用**，与 IQUNIX、Telink 无官方关联。固件及软件按现状提供，不承诺兼容性、稳定性、保修或可恢复性；刷写可能导致设备失效、设置丢失，甚至无法通过 USB 恢复。

请阅读[完整风险与免责声明](DISCLAIMER.md)及[第三方说明](third-party/README.md)。个人学习用途不改变第三方许可证或厂商权利。反馈问题时请提供型号、固件校验值、连接方式和复现步骤，避免上传个人信息或未脱敏日志。
