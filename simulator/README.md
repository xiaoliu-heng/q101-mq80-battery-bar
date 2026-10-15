# MQ80 Firmware Lab 2.1

原生 macOS 离线测试工具：SwiftUI / AppKit 界面，Python / Unicorn 固件引擎。App 没有内嵌网页，也不连接或刷写实体键盘。适合研究 Fn+B 电量条、灯效、按键/组合键、休眠返回路径和 OTA 结束校验。

<img src="../docs/images/simulator-workbench.png" width="1000" alt="MQ80 Firmware Lab 2.1 原生工作台和实体键盘输入控件">

## 下载和打开

从 [Release](https://github.com/xiaoliu-heng/q101-mq80-battery-bar/releases/tag/battery-bar-v2) 下载 `MQ80-Firmware-Lab-v2.1-native-macOS-arm64.zip`，解压后双击 App。不要把 GitHub 的源码 ZIP 当成应用。

当前包适用于 Apple Silicon，目标 macOS 13+，已在 macOS 26.5.1 验证；Intel 和其他系统版本未实测。Python / Unicorn 已打包。应用为本地 ad-hoc 签名，未公证；首次打开时先核对 Release 校验值，再按系统“隐私与安全性”提示处理，不需要全局关闭安全保护。

## 五个工作区

| 工作区 | 可做的测试 |
| --- | --- |
| 工作台 | 84 键矩阵、实体键盘组合键、Fn+B、9 颗侧灯、HID 报告、虚拟主机字符、休眠检查点 |
| 灯效预览 | 运行实际灯效函数，显示原始 LED 索引 0–92，调节主/侧灯、亮度和速度 |
| 场景与回归 | 10 个内置场景、JSON 编辑/导入/导出、重放、双镜像比较和虚拟 NOR OTA END / CRC 检查 |
| 运行时检查 | 虚拟内存、通用寄存器、追踪、统计、快照和报告导出 |
| 建模覆盖 | 每项功能的真实代码、模型和未支持范围 |

## 第一次测试

1. 顶部选择“Fn+B 侧灯电量条 v2”，右侧设置电量，例如 50%，点击“Fn+B 查询电量”。观察黄色半条，到期后恢复原侧灯效果。
2. “继续 / 暂停”控制虚拟时钟。暂停时灯和倒计时冻结；3 秒指虚拟时间。实时预览适合看灯效；严格仿真用于休眠和首键路径检查。
3. 点击“使用实体键盘”或 ⇧⌘K，按 Shift/Ctrl/Option 等组合；默认右 Command 对应虚拟 Fn，右 Command+B 查询电量。没有右 Command 时可勾选“按住 Fn”再按 B。
4. ⌘. 退出实体输入。“松开所有键”、关闭“组合键”、离开工作台或失去焦点会释放虚拟输入。实体键盘自己的 Fn 和 macOS 保留快捷键不一定传给软件。
5. “仿真”菜单可进入全关灯休眠检查点，随后发送 A，观察主机报告。检查点预置计数，不能当作真实经过某段时间的测量。
6. 在“场景与回归”运行全部回归；电量条 v2 基准为 10/10。⌘S 导出报告；⇧⌘S 保存快照。

App 内置 `assets/stock.bin` 和 `assets/battery-bar-v2.bin`，与 Release 固件完全相同。⌘O 导入的是同一固定地址配置兼容的应用 BIN，不支持 EXE、VIA JSON 或任意型号固件。CRC 合格也不保证地址布局兼容。

详细操作见[使用说明](使用说明.txt)。本机数据位于 `~/Library/Application Support/MQ80 Firmware Lab/`；报告与录制可能包含测试输入，公开前请检查。

## 从源码构建

在 Apple Silicon Mac 安装 Xcode Command Line Tools，使用 Python 3.9+。以下命令从本目录执行：

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt pyinstaller==6.16.0
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
mkdir -p test-results
.venv/bin/python -m mq80lab.cli --out test-results/regression.json
.venv/bin/python build_macos.py
```

产物为 `dist/MQ80 Firmware Lab 2.1.app`。运行独立的 Swift 控制器和打包引擎检查：

```sh
xcrun swiftc -O -parse-as-library -target arm64-apple-macosx13.0 \
  native/Models.swift native/Engine.swift native/PhysicalInput.swift \
  tests/NativeControllerTests.swift -framework AppKit -framework SwiftUI \
  -o build/native-controller-tests
build/native-controller-tests \
  "$PWD/dist/MQ80 Firmware Lab 2.1.app/Contents/Resources" \
  test-results/native-controller.json
.venv/bin/python validate_native_package.py \
  "dist/MQ80 Firmware Lab 2.1.app/Contents/Resources/engine/MQ80LabEngine" \
  --out test-results/native-package.json
```

已发布的 2.1 原始基准：41 项 Python 检查、33 项 Swift 控制器检查、10 个固件回归、16 项打包引擎检查通过；另做过原生界面操作与解压迁移检查。原始记录在 `VALIDATION-v2.1.json`。源码树已精简，该历史记录中的旧打包脚本、完整日志及文件哈希不是当前目录清单；本次发布检查见 [`../docs/PUBLICATION_CHECKS.json`](../docs/PUBLICATION_CHECKS.json)。

## 架构和限制

`native/` 管理原生窗口、实体按键和串行命令；`run_native.py` / `mq80lab/native_rpc.py` 使用标准输入输出 JSON lines 连接引擎。`mq80lab/server.py` 保留共用 Lab 服务类及历史 HTTP 测试适配器，因为现有测试依赖它；原生入口不启动 HTTP，仓库不附旧网页界面。

`engine.py` / `machine.py` 执行已分析的真实机器码。`peripherals.py` 提供计时、GPIO/主机/电池输入和虚拟 NOR；`scenarios.py` 定义测试；`ota.py` 将候选数据预载入虚拟 Flash 后执行实际 OTA END / CRC 路径，不是完整 USB 升级传输实现。

固定配置为 MQ80 CS836B V0109。执行启动数据复制和 BSS 清零后，从已连接的检查点进入；没有完整上电、PLL、RF 空口、BLE 控制器/配对/加密、USB 枚举、ADC、电流、bootloader 或任意掉电恢复模型。快照未保存完整特权 CSR / 中断控制器，不能恢复任意芯片瞬间。

主灯灯位来自几何表推导，未逐灯实物校准；部分按键触发灯效不完整。10 ms 动画节拍、1 ms 调度等是模型假设；实体输入每次矩阵变化至少推进 35 ms 以完成消抖和报告，不能测量真实硬件输入延迟。

此前 UI 验证和本次截图使用独立 QA 副本隐藏无障碍树，以避开自动化服务遍历崩溃；发布 App 未设置该开关，程序代码保持一致。完整 VoiceOver 验收未完成。

完整边界见 [`MODEL_COVERAGE.json`](MODEL_COVERAGE.json) 和[项目免责声明](../DISCLAIMER.md)。模拟通过不能证明实物无线、功耗或刷写失败后的恢复能力。
