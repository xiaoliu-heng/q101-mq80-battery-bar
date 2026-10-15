# 电量条 v2 补丁

可直接刷写的完整镜像在 [Release](https://github.com/xiaoliu-heng/q101-mq80-battery-bar/releases/tag/battery-bar-v2)。为保持仓库精简，两份完整 BIN 在源码树中各保留一份，位于 `../simulator/assets/`；App 和离线检查使用同样的字节。

- `battery-bar-v2.bin`：126756 字节，Telink CRC `068F4D5A`。
- `stock.bin`：126292 字节，Telink CRC `B00C451E`。
- `source/battery_bar.c`：读取原电量缓存、绘制 9 段侧灯、计时和恢复原渲染。
- `source/trampoline.S` / `patch.ld`：入口跳转、原指令衔接及固定地址链接。
- `source/build_patch.py`：严格校验官方基底，编译、注入和更新长度/CRC。
- `source/validate_*.py`：离线机器码与状态检查，不访问设备。
- `validation/`：原始离线记录，保留当时的验证范围与限制。

## 重建相同 v2

只有需要研究/重建的人才需执行。普通使用者下载已校验的 Release 镜像即可。以下步骤仅生成本地文件，不刷写键盘。

要求 Python 3.9+、[xPack RISC-V bare-metal GCC 15.2.0-1](https://github.com/xpack-dev-tools/riscv-none-elf-gcc-xpack/releases/tag/v15.2.0-1)，编译器前缀为 `riscv-none-elf-`。在仓库根目录执行：

```sh
python3 -m venv .venv
.venv/bin/pip install -r firmware/source/requirements.txt
.venv/bin/python firmware/source/build_patch.py \
  --stock simulator/assets/stock.bin \
  --toolchain /absolute/path/to/xpack-riscv-none-elf-gcc-15.2.0-1/bin \
  --out build/firmware-v2
shasum -a 256 build/firmware-v2/*EXPERIMENTAL.bin
```

预期 SHA-256：`f3e86554be1ed5faf11750fceea85980c9ccd080912be4552e8317fb09ef7719`。编译器、参数或源码变化后不要沿用原验证结论。不要使用 `python -O`，检查依赖断言。

```sh
.venv/bin/python firmware/source/validate_offline.py \
  --build build/firmware-v2 --out build/firmware-v2/validation.json
.venv/bin/python firmware/source/validate_extended.py \
  --build build/firmware-v2 --out build/firmware-v2/extended-validation.json
.venv/bin/python firmware/source/validate_patch_rv32.py \
  --build build/firmware-v2 --out build/firmware-v2/independent-rv32-validation.json
```

OTA END / CRC 路径可在附带测试软件“场景与回归”中检查。原 16 场景历史结果在 `validation/ota-failure-simulation.json`；不将它称为掉电或强制恢复测试。

构建追加 464 字节代码；官方镜像原有代码/数据区域中实际变动 19 个字节，另更新末尾 CRC。RAM 使用原 BSS 末尾新增的 4 字节记录起始时间。详细地址见 `validation/build-manifest.json`。本补丁不是通用 QMK/VIA SDK，也不能应用到其他地址布局。

**只有完整的 `*EXPERIMENTAL.bin` 是刷写输入。** `.o`、`.elf`、`.machine.bin`、反汇编和 JSON 都不应导入官方升级工具。
