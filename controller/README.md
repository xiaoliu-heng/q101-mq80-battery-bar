# 侧灯 HID 控制工具

实验固件的电脑端工具，支持整条颜色、9 段独立 RGB、彩虹动画、电量缓存查询和恢复原灯效。macOS 使用系统 IOKit，无第三方依赖。Windows/Linux 提供 hidapi 后端，尚未实测。

在项目根目录运行：

```sh
python3 controller/side_light.py info
python3 controller/side_light.py solid FF8000 --seconds 10 --brightness 50
python3 controller/side_light.py rainbow --seconds 10 --fps 20
python3 controller/side_light.py release
```

工具先查询扩展标识，确认后才允许灯光命令。原电量条版和官方固件会被拒绝。需要连接键盘 USB 数据线并切到 USB 模式。默认停止发包约 2 秒后恢复原灯效，正常退出和 Ctrl+C 会尝试立即释放控制。

使用 `--simulate` 可通过项目的固件引擎离线运行；先安装 `simulator/requirements.txt`。示例：

```sh
python3 controller/side_light.py --simulate rainbow --seconds 2
```

仅检查 2.4G 接收器能力：`python3 controller/side_light.py --receiver info`。本实验版通过接收器允许查询，灯光写入仍限定 USB。

固件构建、报文格式、优先级和验证边界见 [SIDE_HID.md](../docs/SIDE_HID.md)。此工具不包含刷写功能。
