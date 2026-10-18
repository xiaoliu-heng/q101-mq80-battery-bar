# 侧灯 HID 控制工具

实验固件的电脑端工具，支持整条颜色、9 段独立 RGB、彩虹动画、电量缓存查询和恢复原灯效。macOS 使用系统 IOKit，无第三方依赖。Windows/Linux 提供 hidapi 后端，尚未实测。

日常 macOS 使用可选择 [Kryra](../macos/README.md)：常驻菜单栏显示电量，提供原生界面、本机 API 和一键安装的 Codex skill。下述 Python 工具继续作为独立调试客户端；调试前先停止应用中的灯光控制，避免两个独立发送端同时控灯。其他程序优先调用应用的 API 或 `kryractl`，由应用统一收发。

在项目根目录运行：

```sh
python3 controller/side_light.py info
python3 controller/side_light.py solid FF8000 --seconds 10 --brightness 50
python3 controller/side_light.py rainbow --seconds 10 --fps 20
python3 controller/side_light.py release
```

工具先查询扩展标识，确认后才允许灯光命令。原电量条版和官方固件会被拒绝。USB 控制需要连接数据线并切到 USB 模式，2.4G 使用下文的 `--receiver` 参数。默认停止发包约 2 秒后恢复原灯效，正常退出和 Ctrl+C 会尝试立即释放控制。

当前固件版本为 **3.0.1，构建号 2**。`info` 返回 `firmware_version`、`firmware_build`、`firmware_feature_flags` 和 `firmware_features`，当前功能为 `fn_b_battery_bar`、`battery_query`、`usb_side_rgb`、`rf_side_rgb`。文件名包含版本号；`--simulate` 默认载入 `firmware/experimental/side-hid-rf-v3.0.1.bin`。固件版本与扩展协议版本 1、VIA 协议版本 11 相互独立。

早期 USB / 2.4G 实验固件没有这个版本字段，仍可使用已开放的控制通道，版本返回 `null`；工具不会据此猜成 v2 或 v3。旧电量条 v2 没有扩展 INFO，需要通过已知 BIN 的 SHA256 区分。

使用 `--simulate` 可通过项目的固件引擎离线运行；先安装 `simulator/requirements.txt`。示例：

```sh
python3 controller/side_light.py --simulate rainbow --seconds 2
```

USB + 2.4G 实验固件可通过接收器控制：键盘切到 2.4G 模式后运行 `python3 controller/side_light.py --receiver info`，再运行 `python3 controller/side_light.py --receiver rainbow --seconds 10`。默认 USB 20 FPS、2.4G 10 FPS；它们是发送目标，真实无线吞吐尚未实测。已休眠时先按键唤醒。旧 USB 实验固件仍可查询，但客户端会拒绝无线灯光写入。

离线测试：`python3 controller/side_light.py --simulate --receiver rainbow --seconds 2`。2.4G 控制有效时暂缓休眠，释放或停止发送约 2 秒后恢复原空闲计时；持续播放会增加耗电。此实现只验证了键盘端机器码，尚未刷入实机测试。

固件构建、报文格式、优先级和验证边界见 [SIDE_HID.md](../docs/SIDE_HID.md)。此工具不包含刷写功能。
