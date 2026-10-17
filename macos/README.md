# Kryra

真实键盘的原生 macOS 菜单栏应用。Swift / SwiftUI / AppKit 编写，直接经 IOKit 访问 Q101 / MQ80 的 Raw HID 接口；应用及内附 CLI 都不依赖 Python、网页容器或云端服务。Kryra v1.1.0 使用本项目 v3.1.0（构建 3）的本地通知灯效。v3.0.1 的 USB / 2.4G 真机反馈属于历史版本；本次 v3.1.0 / v1.1.0 只进行离线验证，尚未刷写或进行功耗测量。

## 使用

将 `Kryra.app` 放入 `/Applications` 或 `~/Applications` 后打开。首次打开显示控制面板，关闭面板后仍留在菜单栏；菜单栏显示键盘电量，点击即可打开面板。

- 自动选择优先使用可响应的 USB，也可在设备菜单指定接收器或键盘。连接数据线并切到 USB 模式，或插入接收器并切到 2.4G；正在休眠的键盘先按键唤醒。
- 电量约每 30 秒读取；没有检测到设备时每 5 秒重试，收包失败时逐渐退避。断线或休眠保留并标注上次读数，不显示虚构的零电量。手动刷新可立即重试。
- 在“灯光”选择纯色、闪烁、呼吸或彩虹，设置颜色、亮度、周期和时长后发送提醒。默认亮度 25%，持续 5 秒；四种灯效支持 0.1～60 秒，闪烁/呼吸默认周期 1 秒，彩虹默认 5 秒。单个色块仍可设置九段静态颜色，支持 0.5～10 秒。
- 四种灯效均由键盘本地播放：查询能力后只发送一次带完整参数的 PLAY，无 RGB 帧流、无心跳，到期不需要电脑再发送取消。新通知立即替换旧通知，恢复按钮主动取消。程序失联时也由固件结束。旧固件会提示需要升级，不回退为逐帧发送。
- 本地灯效不重置无线空闲计数；原有休眠可提前结束通知，唤醒不重播。九段颜色保留旧 FRAME 协议及有限租约，期间仍保持无线接收。退出、电脑休眠和切换设备时尝试取消自身通知；唤醒后仅恢复电量查询。没有无限期控灯模式。
- “集成与设置”提供本机 API、skill 安装和可选的登录时启动。登录项默认关闭；应用退出会释放自己取得的灯光控制，单纯查询后退出不会释放其他程序的控制。

应用只打开 Vendor `320F`、PID `5055` / `5088`、Usage `FF60:61` 的自定义接口。没有键盘输入捕获或固件刷写功能。

## Codex skill

在“集成与设置”点击“一键安装 Skill”，默认写入 `~/.agents/skills/kryra`，包含 `SKILL.md`、UI 元数据和自包含的原生 `scripts/kryractl`。可选择其他 skill 父目录；安装器只更新自身创建的目录，遇到其他同名 skill 会保留原内容并提示。

安装后可对 Codex 说“查键盘电量”“侧灯变蓝，亮度一半”“播放十秒彩虹”，或显式使用 `$kryra`。默认路径及新 skill 的发现行为依照 [Codex 官方说明](https://learn.chatgpt.com/docs/build-skills)；若没有出现，重新打开 Codex。

工具自动读取本机 API 的端口和密钥，不需要让模型复制 token。单次状态提醒优先使用低亮度纯色，默认 5 秒后自动恢复；`--seconds` 可指定其他时长。持续时间可显式设置至 60 秒，不支持 `--continuous`。应用需运行以接收新命令，已接受的灯效由固件自行结束；`kryractl launch` 可打开已经安装并运行过一次的应用。

```sh
~/.agents/skills/kryra/scripts/kryractl battery
~/.agents/skills/kryra/scripts/kryractl battery --refresh
~/.agents/skills/kryra/scripts/kryractl solid 0080FF
~/.agents/skills/kryra/scripts/kryractl solid 34D399 --brightness 25 --seconds 3
~/.agents/skills/kryra/scripts/kryractl blink FFAA33 --period 1000 --seconds 3
~/.agents/skills/kryra/scripts/kryractl breathe 34D399 --period 1000 --seconds 3
~/.agents/skills/kryra/scripts/kryractl rainbow --period 5000 --seconds 5
~/.agents/skills/kryra/scripts/kryractl release
```

## 本机 API

详见应用内“API 说明”，或 [API.html](API.html)。HTTP 仅绑定 `127.0.0.1` 的动态端口；不监听局域网。当前用户的 `~/Library/Application Support/Kryra/api.json` 保存 `host`、`port`、`token`、`pid`。目录权限 0700，配置权限 0600；每次启动重新生成 256 位 token，正常退出删除运行配置。所有路由都需要 `Authorization: Bearer TOKEN`。

| 请求 | 用途 |
| --- | --- |
| `GET /v1/health` | 应用状态 |
| `GET /v1/status` | 连接、固件、电量和灯光状态 |
| `GET /v1/battery` | 缓存电量，`?refresh=true` 立即查询 |
| `POST /v1/refresh` | 刷新连接和电量 |
| `POST /v1/lighting` | 设置 `solid` / `frame` / `rainbow` / `release` |
| `POST /v1/lighting/release` | 恢复原灯效 |

```json
{"mode":"solid","color":"0080FF","brightness":25,"duration_seconds":5}
```

未传亮度或时长时统一使用 25% 和 5 秒。四种模式为 `solid`、`blink`、`breathe`、`rainbow`；前三种接受 `color`，后两种及闪烁接受 `period_ms`。`continuous: true` 返回 400。可选 `request_id` 为 1～4294967295 的整数，同一次请求重试使用同一个编号和参数，不能延长或重启已到期/取消的通知；省略时应用生成随机编号。CLI 对应 `--request-id`。状态包含 `lighting.execution`、`commands_sent`、`remaining_ms` 和 `request_id`；`frames_sent` 是兼容旧接口的命令计数，不表示键盘动画帧数。`prevents_idle_sleep` 只在九段颜色的旧无线租约期间为 true。本机状态根据最近回复推算；键盘提前休眠需再次查询才能确认。

UI、API 和 CLI 共用单个专用 HID 线程、校验器和控灯任务，新的控灯操作替代旧操作。查询不取得灯光控制权。API 拒绝浏览器 Origin、错误 Host、无效 token、重复报文头和过大请求体；单连接仅处理一次请求，并限制并发连接数和超时时间。

## 构建与验证

macOS 13+，需要 Xcode Command Line Tools。Python 仅用于开发时组装应用；成品无 Python 运行时依赖。

```sh
python3 macos/build.py --universal
```

输出 `dist/Kryra.app`，包含 Apple Silicon / Intel 通用二进制及本地 ad-hoc 签名。当前 Mac 为 Apple Silicon；Intel 实机尚未验证，应用尚未使用 Developer ID 公证。

原生单元与生命周期检查使用 `CoreTests.swift`；协议样本通过 `fixtures.py` 执行真实固件生成。端到端检查启动显式 demo 模式，测试真实网络监听器和原生 CLI，使用隔离的运行目录，不访问真机：

```sh
.venv/bin/python macos/tests/fixtures.py
# CoreTests 的编译与验证命令见 scripts/check_macos.py。
.venv/bin/python scripts/check_macos.py
.venv/bin/python macos/tests/validate_app.py \
  --app 'dist/Kryra.app' --out test-results/kryra-app-e2e.json
```

演示模式始终显示“演示”，API 返回 `demo: true`。它只用于测试，不能当作真机读数。发布应用默认启用真实 IOKit，`Info.plist` 不设置演示环境。运行记录见 [VALIDATION-v1.1.0.json](VALIDATION-v1.1.0.json)。

v1.0.0 在 2026-10-17 的 [USB 真机验收](VALIDATION-USB-20261017.json) 通过电量、纯色、九段颜色、彩虹收发、定时释放和原生界面控灯检查；彩虹在约 4 秒采样窗口内收到 81 帧回复，约 20.1 FPS。结束后已恢复原灯效。这些检查确认协议收发与应用状态，未进行灯珠颜色的光学测量；本轮未验收新应用的 2.4G 真机路径。

v1.1.0 的四种本地灯效需要新的 v3.1.0 固件。新固件与应用尚未进行真机灯光、休眠、电流或续航验收；离线结果证明命令次数、执行路径与恢复行为，不代表快速掉电问题已经实测修复。历史 v1.0.0 / v1.0.1 的验证记录和发布归档保留不变。
