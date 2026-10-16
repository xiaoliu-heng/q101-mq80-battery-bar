# USB 侧灯 HID 控制实验

在原 Fn+B 电量条补丁上增加电脑端控制。基于固定版本 **MQ80-CS836B-V0109-20241220**。截至 2026-10-16，新镜像完成离线验证，尚未刷写或实测其灯光控制。主 README 中已有的实机结论适用于原电量条版。

## 功能

- 电脑发送 9 组 RGB，分别控制侧灯的 9 个逻辑段；可做纯色、进度条或电脑生成的动画。
- 保留 Fn+B：临时显示电量约 3 秒，结束后恢复仍在有效期内的电脑画面。
- 默认 2 秒未收到帧或有效心跳就释放控制，可配置为 500～10000 毫秒。
- 退出工具时发送释放命令，恢复原侧灯状态，包括原本关闭的状态。原固件的连接、充电等提示仍按原逻辑恢复。
- INFO 命令提供扩展版本、电量缓存、连接方式、控制状态和剩余超时。
- 灯帧保存在 RAM，不写入 Flash。输入 RGB 为 0～255，显示时按 `floor(channel × 162 / 255)` 缩放，沿用原电量条的亮度上限。

灯光写入仅允许键盘 **USB 模式**。2.4G 通道开放 INFO 查询；无线灯光控制尚未启用。蓝牙未增加新报告。

## 电脑端使用

先刷入对应实验镜像，再接 USB 数据线并切到 USB 模式；当前设备的切换组合为 Fn+5。刷写流程和恢复限制见主 README。本次开发没有实际刷写。

macOS 使用系统 IOKit，Python 3.9 或更新版本即可运行，无需 pip 依赖。Windows/Linux 的可选 hidapi 后端需要 `python -m pip install hidapi`，该后端尚未实测。

在项目根目录运行：

```sh
# 先查询扩展能力和原固件电量缓存
python3 controller/side_light.py info

# 橙色，显示 10 秒，亮度 50%
python3 controller/side_light.py solid FF8000 --seconds 10 --brightness 50

# 9 段分别指定颜色
python3 controller/side_light.py frame FF0000 FF4000 FF8000 FFFF00 80FF00 00FF00 00FFFF 0080FF 0000FF --seconds 10

# 电脑生成动画；20 FPS 是示例发送频率，未实测物理刷新率
python3 controller/side_light.py rainbow --seconds 10 --fps 20

# 立即释放控制
python3 controller/side_light.py release
```

启动时先检查协议魔数、设备标识、协议版本和能力。官方固件及原电量条版不会收到随后灯帧命令，工具会提示不支持扩展。`--receiver` 参数只允许 `info`，避免向接收器发送灯光写入。

读取的电量来自原固件的百分比缓存，不表示重新测量或校准过电池荷电量。

## 构建与离线运行

开发分支保存了已构建的[实验镜像](../firmware/experimental/side-hid.bin)，与下述构建结果字节一致。安装模拟器依赖后，可直接测试，无需先配置交叉编译器：

```sh
python3 -m pip install -r simulator/requirements.txt
python3 controller/side_light.py --simulate --firmware firmware/experimental/side-hid.bin rainbow --seconds 2
```

使用 RISC-V bare-metal GCC，架构 `RV32IM / ILP32`：

```sh
python3 firmware/hid-source/build_hid.py \
  --stock simulator/assets/stock.bin \
  --toolchain /path/to/riscv-toolchain/bin \
  --out build/side-hid

python3 -m pip install -r simulator/requirements.txt
python3 firmware/hid-source/validate_hid.py --battery-regression
python3 controller/side_light.py --simulate solid FF8000 --seconds 1
python3 controller/side_light.py --simulate rainbow --seconds 2
```

生成镜像：`build/side-hid/MQ80-CS836B-V0109-20241220-BatteryBar-SideHID-EXPERIMENTAL.bin`。

本次构建 codesize **128148**，CRC **3DA2170D**，SHA-256：

```text
f380297767d86cba31d4264b1b419a69a17e7e6b7370413992eeb3b47d92a61c
```

该 BIN 可作为自定义固件加载到 MQ80 Firmware Lab，检查既有按键和灯光功能。新 HID 控制由命令行工具的 `--simulate` 或验证脚本测试；现有原生 App 尚未增加 HID 控制面板。

## HID 协议

复用原 VIA Raw HID 接口：VID `320F`、键盘 PID `5055`、Usage Page `FF60`、Usage `61`，固定 32 字节、无编号报告。hidapi 写入时在前面附加零 Report ID；IOKit 的 Report ID 参数为零，数据仍为 32 字节。VIA 原命令和 OTA 接口没有被替换。

| 字节 | 请求内容 |
| --- | --- |
| 0～2 | `FE 53 4C`，扩展魔数 |
| 3 | `0x10 OR command`；高 4 位为协议版本 1 |
| 4 | 序号，回复原样带回 |
| 5～31 | 27 字节负载，未用部分填零 |

| command | 名称 | 负载与行为 |
| --- | --- | --- |
| 0 | INFO | 查询，不续期；USB 和 2.4G 均可进入键盘端处理 |
| 1 | FRAME | 27 字节，按段顺序排列 `R,G,B`；开始控制并续期 |
| 2 | RELEASE | 释放控制，下次灯光帧恢复原逻辑 |
| 3 | KEEPALIVE | 只对仍有效的控制续期，已过期不能复活 |
| 4 | TIMEOUT | 负载前 2 字节为小端毫秒数，范围 500～10000 |

回复字节 3 为 `0x18 OR command`，表示响应。字节 5 是状态：0 成功，1 版本错误，2 未知命令，3 参数错误，4 控制未激活或已过期，5 当前连接不允许控制，6 报告长度错误。

回复同时提供：协议版本（6）、段数（7）、控制/Fn+B 标志（8）、电量缓存（9）、连接方式（10，蓝牙=0、2.4G=1、USB=2）、末帧序号（11）、允许控制的传输位图（12，当前仅 bit0=USB）、超时毫秒（13～14，小端）、剩余毫秒（15～16，小端）、设备标识 `Q101`（17～20）、通道亮度上限（21）及能力位图（22）。

短 USB 报文不能利用缓冲区残留尾部提交灯帧。两个原 USB 接收入口都记录实际报告长度；只有完整 32 字节扩展报文才执行命令。

## 灯光优先级与恢复

每个灯光帧先检查控制超时及连接方式，再执行原电量条/原固件帧任务，最后按优先级覆盖侧灯：

1. Fn+B 电量查询。
2. 仍有效的电脑侧灯画面。
3. 原侧灯与原连接、充电等提示。

Fn+B 不自动延长电脑控制时间。若显示电量期间电脑继续发送心跳，查询结束后恢复电脑画面；若已经超时，则恢复原逻辑。切离 USB 模式时撤销电脑控制。

固件只采样一次当前帧的有效状态，避免在同一帧的不同渲染阶段跨过时间边界产生残留。

## 2.4G 检查结果

已定位键盘端真实路径：

- 接收回调 `0x1840` 验证外层 CRC 和内层校验，将配置负载复制到 `0x80BE8`，并设置待处理标志。
- 共享分派函数 `0x2000E190` 在 USB/2.4G 下处理同一个 32 字节缓冲区。
- 无线回复函数 `0x20010DFC` 将回复重新封包提交给 RF 发送边界。

离线执行了上述机器码，INFO 请求的 32 字节内容完整到达扩展处理器，回复完整进入无线发送包。无效 RF CRC 在分派前被拒绝。这部分没有模拟接收器固件或真实空口。

另外对真实 `320F:5088` 接收器发送了只读能力探测。带有 27 字节递增测试内容的请求为：

```text
FE 53 4C 10 8A 00 01 02 03 04 05 06 07 08 09 0A 0B
0C 0D 0E 0F 10 11 12 13 14 15 16 17 18 19 1A
```

回复符合当前键盘固件的未知命令回退行为，27 字节测试内容全部保留。这强烈支持接收器能够转发完整自定义报文；新固件经真实接收器读写和灯光显示仍待刷入后验证。因此当前先开放 USB 灯光控制，保留通过接收器查询新协议能力的入口。

探测没有发送灯光写入、OTA 或接收器刷写命令。记录见 [`receiver-pattern-probe.json`](../firmware/hid-validation/receiver-pattern-probe.json)。

## 验证与修改范围

固件新增代码位于原镜像末尾；保留原重定位 RAM 代码、描述符、表格和初始化数据。共额外保留 44 字节 BSS：原查询计时 4 字节，以及主机颜色/超时/长度等状态 40 字节。USB、无线和 OTA 底层驱动继续使用原实现。

离线测试覆盖 USB 两条接收路径、1～31 字节短报文、协议错误、逐通道亮度、序号回绕、时间回绕和精确过期、释放及断线超时恢复、Fn+B 覆盖、按键输入、VIA 原读取命令、无线配置接收/回复及原电量条回归。测试执行实际补丁和相关原固件 RV32/Andes 指令。

本次通过 **26 项 HID 专项测试、6 组电量条机器码回归、41 项原测试软件测试，以及 16 个双 bank OTA END/CRC 场景**。独立目录重复构建的固件字节完全一致。OTA 场景只验证虚拟 NOR 上的校验和提交，不证明启动失败后能够强制恢复。

模拟环境的 USB FIFO、主机端点、射频边界和电量缓存仍由模型提供。测试不代表真实 USB 调度、空口性能、电流、所有硬件批次或刷写恢复已经验证。构建清单和测试结果见 [`firmware/hid-validation/`](../firmware/hid-validation/)。

本实验为非官方固件开发，仅供个人学习交流；[项目风险与免责声明](../DISCLAIMER.md)同样适用。
