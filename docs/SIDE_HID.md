# USB / 2.4G 侧灯 HID 控制实验

在原 Fn+B 电量条补丁上增加电脑端控制。基于固定版本 **MQ80-CS836B-V0109-20241220**。2026-10-17，使用者反馈已刷入 v3.0.1，并在真机 USB 和 2.4G 连接下确认可读取电量与控制灯光。[反馈记录](../firmware/v3.0.1-validation/user-hardware-confirmation-20261017.json)与此前离线验证分开保存；没有据此宣称真实空口吞吐、功耗或刷写恢复已经验证。日常控制可使用 [Kryra 原生应用](../macos/README.md)。

## 旧 FRAME 协议功能

- 电脑发送 9 组 RGB，分别控制侧灯的 9 个逻辑段；可做纯色、进度条或电脑生成的动画。
- 保留 Fn+B：临时显示电量约 3 秒，结束后恢复仍在有效期内的电脑画面。
- 默认 2 秒未收到帧或有效心跳就释放控制，可配置为 500～10000 毫秒。
- 退出工具时发送释放命令，恢复原侧灯状态，包括原本关闭的状态。原固件的连接、充电等提示仍按原逻辑恢复。
- INFO 命令提供扩展版本、电量缓存、连接方式、控制状态和剩余超时。
- 灯帧保存在 RAM，不写入 Flash。输入 RGB 为 0～255，显示时按 `floor(channel × 162 / 255)` 缩放，沿用原电量条的亮度上限。

历史 `side-hid-rf-v3.0.1.bin` 允许键盘 **USB 和 2.4G 模式**的灯光控制，并提供可查询的固件版本和功能。早期 `side-hid-rf.bin` 保留为未标版本的 USB/2.4G 实验版；`side-hid.bin` 是旧 USB 实验版，无线只允许 INFO。客户端先查询能力位图，未开放无线控制的旧固件不会收到无线灯帧。蓝牙未增加新报告。

## 固件版本与功能

| 镜像 | 功能 | 可查询的固件版本 |
| --- | --- | --- |
| `battery-bar-v2.bin` | Fn+B 电量条 | 旧版未提供扩展版本查询；v2 是历史发布名称 |
| `side-hid.bin` | 电量条、电量查询、USB 控灯 | 未标版本，返回 `null` |
| `side-hid-rf.bin` | 电量条、电量查询、USB/2.4G 控灯 | 未标版本，返回 `null` |
| `side-hid-rf-v3.0.0.bin` | 历史测试版；调整超时可能重新激活已过期颜色 | `3.0.0`，构建号 `1` |
| `side-hid-rf-v3.0.1.bin` | 修复上述超时边界，保留 USB/2.4G 功能及版本/功能查询 | `3.0.1`，构建号 `2` |
| `side-hid-rf-v3.1.0.bin` | 新增四种本地通知灯效，有限时长，原休眠可取消通知；仅离线验证 | `3.1.0`，构建号 `3` |

版本配置源为 [`firmware/hid-source/version.json`](../firmware/hid-source/version.json)，构建脚本据此生成 C 常量、镜像文件名和构建清单。版本号采用 `主版本.次版本.补丁`，各段 0～255；构建号为 1～65535。版本元数据只进入新增只读常量及扩展回复，不借用官方版本字段，也不写入运行时 Flash 设置。codesize 和镜像 CRC 正常重算。

`python3 controller/side_light.py --receiver info` 可在新版固件上读取 `firmware_version`、`firmware_build` 及 `firmware_features`。固件版本 `3.0.1`、扩展协议版本 `1`、VIA 协议版本 `11`、官方基线 `V0109` 及测试 App 版本 `2.4.1` 分别标识不同对象。VIA 版本不能用来推断当前固件功能，早期零尾部回复也不会被猜测成某个固件版本。

## 旧 FRAME 协议诊断工具

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

2.4G 使用同一工具，键盘切到 2.4G 模式（本项目设备为 Fn+4），接收器保持连接。若键盘已经休眠，先按任意键唤醒：

```sh
python3 controller/side_light.py --receiver info
python3 controller/side_light.py --receiver solid 0080FF --seconds 10
python3 controller/side_light.py --receiver rainbow --seconds 10
python3 controller/side_light.py --receiver release
```

彩虹默认 USB 20 FPS、2.4G 10 FPS，可用 `--fps 1～30` 调整。这是电脑发送目标，尚未测得真实无线吞吐或物理刷新率。静态颜色每 250 ms 续期。每条命令等待匹配序号的回复后再发送下一条，实际帧率会受到收发耗时影响。

启动时先检查协议魔数、设备标识、协议版本和当前通道能力。官方固件及原电量条版不会收到随后灯帧命令，工具会提示不支持扩展。

读取的电量来自原固件的百分比缓存，不表示重新测量或校准过电池荷电量。

### 2.4G 彩虹快速掉电排查

2026-10-17，使用者反馈 2.4G 彩虹期间电量约几秒下降 1%，恢复原灯效后读数不回升。按使用者要求继续做代码分析；[排查记录](../firmware/v3.0.1-validation/battery-drop-investigation-20261017.json)和[可复现的离线执行结果](../firmware/v3.0.1-validation/power-code-audit-20261017.json)单独保存。Kryra 1.0.1 的低亮度、短时默认值只限制控灯时间与亮度，目前没有验证它修复了快速下降问题。

当前彩虹由电脑逐帧发送。每个 RGB 更新在原无线配置路径上提交一次 `0x30` 回复和三次 `0xB7` 收尾发送；目标 10 FPS 对应每秒 40 次键盘发送提交，另有收包与确认处理。这不是实测空口包数或电流。主机颜色使用固定 `162/255` 缩放，绕过原侧灯亮度档位，因此与键盘原灯效未必是相同亮度。

原厂 ADC 函数与本补丁中对应字节一致。在执行的正常放电路径中，主循环采样间隔大于 4 秒；累计五次较低估计后降 1%，输入恢复后保持已下降的百分比，间隔的较高估计也不清零累计计数。启动采样阶段另有约 80 ms、2 秒的调度与直接更新路径；尚不知道真机当时处于哪个阶段。固定 ADC 输入的 25% / 65% 亮度彩虹各 100 帧测试中，电量保持 80%，控灯收发和渲染没有写入监控的电量状态、校准值或采样时间戳。ADC 时间戳 `0x80a74` 与控灯清零的休眠计数 `0x80a64` 是不同字段。

这些结果能复现“下调后不回升”的估算行为，尚不能判定真机下降中实际容量损失的比例或电气原因。测试注入了 ADC、初始状态和 GPIO，收发确认即时建模；没有模拟完整启动、并发中断、无线时序或实际负载，也没有修改或刷写固件。

```sh
.venv/bin/python firmware/hid-source/analyze_power.py \
  --out test-results/power-code-audit.json
```

## 历史 v3.0.1 构建记录

以下校验值来自历史构建；当前源码默认构建 v3.1.0，见后文。本项目保存的[v3.0.1 实验镜像](../firmware/experimental/side-hid-rf-v3.0.1.bin)，与下述构建结果字节一致。旧 USB、未标版本的 USB/2.4G 镜像及历史验证记录均保留。安装模拟器依赖后，可直接测试，无需先配置交叉编译器：

```sh
python3 -m pip install -r simulator/requirements.txt
python3 controller/side_light.py --simulate --receiver rainbow --seconds 2
```

使用 RISC-V bare-metal GCC，架构 `RV32IM / ILP32`：

```sh
python3 firmware/hid-source/build_hid.py \
  --stock simulator/assets/stock.bin \
  --toolchain /path/to/riscv-toolchain/bin \
  --out build/side-hid-rf-v3.0.1

python3 -m pip install -r simulator/requirements.txt
python3 firmware/hid-source/validate_hid.py --battery-regression
python3 controller/side_light.py --simulate solid FF8000 --seconds 1
python3 controller/side_light.py --simulate rainbow --seconds 2
python3 controller/side_light.py --simulate --receiver rainbow --seconds 2
```

生成镜像：`build/side-hid-rf-v3.0.1/MQ80-CS836B-V0109-20241220-BatteryBar-SideHID-v3.0.1-USB-RF-EXPERIMENTAL.bin`。

本次使用 xPack RISC-V GCC 15.2.0-1 构建，codesize **128372**，CRC **47D6766F**，SHA-256：

```text
7b800e3edab6b2e8c421c3b7729464e990edfd2e5a2647ded6059c1901006534
```

开发版 MQ80 Firmware Lab 2.4.1 已内置该 BIN，“侧灯 HID”工作区（⌘6）直接显示固件版本、构建号与功能。选择“虚拟 USB · 20 FPS”或“虚拟 2.4G · 10 FPS”，即可调色、播放彩虹，观察 Fn+B 覆盖、释放和超时。所有预览均属于离线模拟。构建及操作见 [测试软件说明](../simulator/README.md#侧灯-hid-实时预览)。

## 本地通知灯效 v3.1.0

新版 [v3.1.0，构建 3](../firmware/experimental/side-hid-rf-v3.1.0.bin) 在原协议版本 1 中新增 `PLAY=5`，保留旧 FRAME / KEEPALIVE 行为。功能元数据字节 29 的 bit4 及能力字节 22 的 bit4 同时表示本地灯效支持；新版两者均为 `1F`。Kryra v1.1.0 的纯色、闪烁、呼吸、彩虹都通过一次 PLAY 执行，旧固件只查询能力后提示升级，不自动回退为帧流。

请求仍是 `FE 53 4C 15 sequence` 加 27 字节负载；回复命令字节为 `1D`，其他字段沿用下面的协议定义。PLAY 的负载偏移如下（相对整个报告再加 5）：

| 负载偏移 | 内容 |
| --- | --- |
| 0 | 灯效：1 纯色，2 闪烁，3 呼吸，4 彩虹 |
| 1 | 亮度 0～100 |
| 2～4 | RGB；彩虹必须为零 |
| 5～6 | 小端周期毫秒：动态灯效 500～20000，纯色必须为零 |
| 7～8 | 小端持续毫秒：100～60000，必须有限 |
| 9～12 | 非零小端 32 位请求编号 |
| 13～26 | 保留，必须填零 |

参数校验失败不修改既有通知。新编号替换当前灯效；最近一次 PLAY 编号及参数完全相同的重试只回复，不重新计时或播放，过期、RELEASE、连接方式变化或休眠取消后也不会因该重试重启。编号相同但参数不同返回参数错误。编号去重保留在 RAM 中，重启或改用旧 FRAME 后不保证继续保留；它不是全局请求历史。INFO 和旧 KEEPALIVE 不延长本地通知，旧 TIMEOUT 只设置后续旧 FRAME 的时限。

固件按系统时间和原灯光任务生成画面。闪烁采用半周期亮、半周期灭；呼吸采用往返亮度曲线，起点为灭；彩虹是九段错相的 RGB 色轮。亮度最后仍受固定 `162/255` 输出缩放限制。Fn+B 电量条优先，通知计时在其显示期间继续。超时或主动取消后恢复原灯效，不写 Flash。

PLAY 不清零原无线空闲计数。原有快速/普通休眠分支选中后，通过保存寄存器的钩子取消本地通知，再继续原睡眠流程；唤醒后不重播。因此休眠可以早于请求时长结束提醒。USB 没有新增无线保活行为，已休眠的无线键盘也不会因本接口远程唤醒。旧 FRAME 的无线保活行为保留用于兼容；Kryra 的九段静态颜色只使用 0.5～10 秒的有限旧租约，不发送心跳。

新增状态共 52 字节，启动 BSS 结束于 `0x826D0`；旧 USB 长度记录偏移保持不变。新增两个休眠钩子位于原镜像 `0x112AC`、`0x12A4E`，分别重放原序言或参数设置，不改变睡眠参数、唤醒源及原 ADC 函数。

本轮仅离线验证，[记录目录](../firmware/v3.1.0-validation/)包含固件身份、原协议和电量条回归、本地动画、独立 RV32 解析器、休眠/唤醒及 OTA 模型检查。没有真机电流、物理刷新率或续航改善结论。

```sh
.venv/bin/python firmware/hid-source/build_hid.py \
  --stock simulator/assets/stock.bin \
  --toolchain build/toolchains/xpack-riscv-none-elf-gcc-15.2.0-1/bin \
  --out build/side-hid-rf-v3.1.0
.venv/bin/python firmware/hid-source/validate_hid.py --battery-regression \
  --out build/side-hid-rf-v3.1.0/hid-validation.json
.venv/bin/python firmware/hid-source/validate_effects.py \
  --out build/side-hid-rf-v3.1.0/effects-validation.json
```

Python 协议库提供 `Client.play('breathe', color=bytes.fromhex('34D399'), duration_ms=3000)`。上文 `controller/side_light.py` 和测试软件的帧流演示属于保留的旧协议诊断工具；日常通知使用 Kryra / skill 的本地灯效。

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

回复同时提供：协议版本（6）、段数（7）、控制/Fn+B 标志（8）、电量缓存（9）、连接方式（10，蓝牙=0、2.4G=1、USB=2）、末帧序号（11）、允许控制的传输位图（12，bit0=USB、bit1=2.4G，新版值为 3）、超时毫秒（13～14，小端）、剩余毫秒（15～16，小端）、设备标识 `Q101`（17～20）、通道亮度上限（21）及能力位图（22）。

v3.0.0 在扩展回复原先填零的偏移 23～31 增加以下元数据，其余 0～22 字节的定义及协议版本 1 保持不变。旧客户端可继续忽略尾部；新客户端遇到未标版本或未知元数据格式时将固件版本标为未知。

| 回复偏移（从 0 开始） | 内容 |
| --- | --- |
| 23～24 | `46 56`，ASCII `FV` |
| 25 | 元数据格式 `1` |
| 26～28 | 固件主/次/补丁版本，当前 `03 00 01` |
| 29 | 功能位：bit0 电量条、bit1 电量查询、bit2 USB 控灯、bit3 2.4G 控灯，当前 `0F` |
| 30～31 | 小端构建号，当前 `02 00` |

短 USB / 2.4G 报文不能利用缓冲区残留尾部提交灯帧。两个原 USB 接收入口记录实际报告长度，无线使用原 RF 长度字段；只有完整 32 字节扩展报文才执行命令。

## 27 字节负载与原命令的隔离

偏移 5～31 是本扩展在 32 字节配置报告内定义的负载，原报告缓冲区 `0x80BE8` 按命令复用，并非固件全局未使用的空间。原命令已经会使用这些位置：例如 `0x0E` 的数据读取以报告偏移 4 为输出起点，`0x0F` 从偏移 4 起复制传入数据，`0x13` 的配置更新读取偏移 4、5。未知命令保留请求尾部的回显，只能证明该命令路径的往返行为。

新版在原命令分派前的 `0x2000E22C` 插入检查：匹配 `FE 53 4C` 的报告由扩展处理，随后直接进入共同回复入口 `0x2000E2F4`，跳过原分派入口 `0x2000E230`；匹配魔数但长度、版本或子命令错误时也返回扩展错误，不再交给原命令解释。其余报告继续执行原分派。固定 V0109 的原分派表范围为 `0x00～0x15`，`0xFE` 原本进入未知命令回退。

持久保留的主机 RGB 会复制到另行保留的 `side_host` 状态，随后普通 VIA 命令可以继续复用报告缓冲区。电量仅填入扩展 INFO 回复，不能无条件修改所有配置回复的同一个字节。原传输层仍按原逻辑读取报告、封包和发送。

2026-10-17 的[命令隔离检查](../firmware/rf-hid-validation/command-namespace-audit-20261017.json)覆盖三种接收路径下的 27 个扩展用例、18 个普通/其他魔数用例，以及报告缓冲区复用后 RGB 状态保持，共 46 项检查通过。这是固定镜像已执行路径的证据，不是所有别名访问、中断并发或接收器固件的完整证明。原 `0x0E` 命令在本轮执行中遇到未支持的 Andes 指令，其负载使用结论来自静态指令检查，未计入完整动态覆盖。

v3.0.1 修复了 v3.0.0 的超时边界：在灯光任务尚未处理过期状态时，TIMEOUT 也会先确认旧控制是否过期，再应用新时限；缩短时限造成的过期同样立即生效。调整 TIMEOUT 不刷新末帧时间，已经过期的颜色只能由新的 FRAME 重新激活。

## 灯光优先级与恢复

每个灯光帧先检查控制超时及连接方式，再执行原电量条/原固件帧任务，最后按优先级覆盖侧灯：

1. Fn+B 电量查询。
2. 仍有效的电脑侧灯画面。
3. 原侧灯与原连接、充电等提示。

Fn+B 不自动延长电脑控制时间。若显示电量期间电脑继续发送心跳，查询结束后恢复电脑画面；若已经超时，则恢复原逻辑。连接模式改变时撤销当前控制，USB 画面不会自动迁移到 2.4G，反向同样如此。

固件只采样一次当前帧的有效状态，避免在同一帧的不同渲染阶段跨过时间边界产生残留。

## 2.4G 与休眠

原无线配置收包本身不会清零空闲计数。为了使连续动画不被休眠中断，新版在有效无线 FRAME / KEEPALIVE 以及有效控制期间的灯光帧中，将原快速空闲计数 `0x80B30` 和空闲秒数 `0x80A64` 清零。两个字段均为 32 位。INFO、无效命令、过期心跳不会延长控制或阻止休眠。

这意味着 **2.4G 持续控灯会暂缓自动休眠并增加耗电**。RELEASE 立即交回灯效，停止发送则默认约 2 秒后释放；此后原计数重新累计、原休眠流程恢复，不是停发两秒就立刻睡眠。补丁没有写入永久休眠设置，没有改 RF/PM 驱动或 Flash。已经休眠时不能保证接收灯光命令，应先按键唤醒。

## 2.4G 检查结果

已定位键盘端真实路径：

- 接收回调 `0x1840` 验证外层 CRC 和内层校验，将配置负载复制到 `0x80BE8`，并设置待处理标志。
- 共享分派函数 `0x2000E190` 在 USB/2.4G 下处理同一个 32 字节缓冲区。
- 无线回复函数 `0x20010DFC` 将回复重新封包提交给 RF 发送边界。

无线配置的逻辑格式为下行 `B0 | 20 | data[32] | sum8`，上行为 `30 | 20 | reply[32] | sum8`，外层仍有原固件 CRC16。`data[32]` 就是本协议完整报文，5 字节头和 27 字节 RGB；电脑仍向接收器的 Raw HID 接口发 32 字节，由原接收器负责外层封包。

离线执行了真实 B0 收包、共享分派、无线回复、B2 确认及三个 B7 收尾发送/ACK 的机器码，待处理标志在每次交易后由原代码释放，可连续发送灯帧。无效 RF CRC / 内层校验在分派前被拒绝。模拟器为配置交易即时交付接收器回复与 ACK；键盘上行报告保留原延迟/丢包模型。配置链路的重传、空口时序和接收器固件没有建模。

另外对真实 `320F:5088` 接收器发送了只读能力探测。带有 27 字节递增测试内容的请求为：

```text
FE 53 4C 10 8A 00 01 02 03 04 05 06 07 08 09 0A 0B
0C 0D 0E 0F 10 11 12 13 14 15 16 17 18 19 1A
```

回复符合当时键盘固件的未知命令回退行为，27 字节测试内容全部保留。这支持接收器能够转发完整自定义报文；该探测属于此前 USB 实验阶段，新 USB/2.4G 固件经真实接收器读写和灯光显示仍待刷入后验证。

探测没有发送灯光写入、OTA 或接收器刷写命令。记录见 [`receiver-pattern-probe.json`](../firmware/hid-validation/receiver-pattern-probe.json)。

2026-10-17 再次使用本机连接的真实 `320F:5088` 接收器做只读检查：12 次 INFO 测试全部收到 32 字节回复，包含序号回绕及不同负载，27 字节测试内容全部保留，且与原电量条 v2 的机器码回退回复逐字节一致。12 次查询往返为 7.639～19.103 ms，标准 VIA 协议版本读取返回 11。当前通道未返回 `FE 53 4C` / `Q101` 扩展标识，不能据此读取电量或宣称新控灯功能通过实机测试；也不能从 VIA 协议版本确定已刷固件的 SHA256。此次抓取位于接收器 USB Raw HID 接口，不包含真实空口包头或 CRC，没有发送控灯、OTA 或刷写命令。[完整实机记录](../firmware/rf-hid-validation/receiver-live-read-only-probe-20261017.json)。

## 验证与修改范围

固件新增代码位于原镜像末尾；保留原重定位 RAM 代码、描述符、表格和初始化数据。v3.0.1 共额外保留 44 字节 BSS；v3.1.0 共 56 字节：原查询计时 4 字节，以及主机颜色/超时/长度/本地灯效状态 52 字节。USB、无线和 OTA 底层驱动继续使用原实现。

离线测试覆盖 USB 两条接收路径、1～31 字节短报文、协议错误、逐通道亮度、序号回绕、时间回绕和精确过期、释放及断线超时恢复、Fn+B 覆盖、按键输入、VIA 原读取命令、无线配置接收/回复及原电量条回归。测试执行实际补丁和相关原固件 RV32/Andes 指令。

v3.0.1 刷入前检查记录见 [`firmware/v3.0.1-validation/preflash-summary.json`](../firmware/v3.0.1-validation/preflash-summary.json)。除前述 HID 回归外，可分别运行三个专项检查（默认连续收发为每通道 300 秒虚拟时间）：

```sh
for group in core soak ota; do
  python3 firmware/hid-source/validate_preflash.py \
    --build build/side-hid-rf-v3.0.1 --group "$group" \
    --out "build/side-hid-rf-v3.0.1/preflash-$group.json"
done
```

v3.0.0 历史版本与兼容性测试记录见 [`firmware/v3.0.0-validation/`](../firmware/v3.0.0-validation/) 及 [`simulator/VALIDATION-v2.4.json`](../simulator/VALIDATION-v2.4.json)。未标版本 USB/2.4G 实验的历史记录保留在 [`firmware/rf-hid-validation/`](../firmware/rf-hid-validation/) 和 [`simulator/VALIDATION-v2.3.json`](../simulator/VALIDATION-v2.3.json)。OTA 场景只验证虚拟 NOR 上的校验和提交，不证明启动失败后能够强制恢复。

模拟环境的 USB FIFO、主机端点、射频边界和电量缓存仍由模型提供。测试不代表真实 USB 调度、空口性能、电流、所有硬件批次或刷写恢复已经验证。旧 USB 实验历史记录见 [`firmware/hid-validation/`](../firmware/hid-validation/)。

本实验为非官方固件开发，仅供个人学习交流；[项目风险与免责声明](../DISCLAIMER.md)同样适用。
