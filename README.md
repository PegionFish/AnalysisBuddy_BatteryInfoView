# batteryinfoview — BatteryInfoView 电池日志解析插件

解析 NirSoft BatteryInfoView 导出的 TXT 电池日志（无表头、逗号分隔、7 列），用 `analysisbuddy-sdk` 编写。仓库根即插件目录，`git clone` 后由宿主按 `plugin.json` entry 拉起（`entry: {command: "python", args: ["main.py"]}`）。

## 输入格式（spec 3.1，七列）

```text
8/6/2026 3:30:57 PM,AC Power,100.0%,"99,072",0,"17,850",Timer
```

| 位置 | 字段 | 类型 | 示例 | 说明 |
|------|------|------|------|------|
| 1 | `timestamp` | 时间 | `8/6/2026 3:30:57 PM` | `M/D/YYYY h:mm:ss AM/PM` 12 小时制，本地时间；直读 UTC 毫秒，不换算 |
| 2 | `power_state` | 文本 | `AC Power` | `AC Power` / `DC Power` 等 |
| 3 | `battery_level` | 数值 | `100.0%` | 电池电量百分比 |
| 4 | `full_capacity` | 数值 | `"99,072"` | 满充容量（引号 + 千分位） |
| 5 | `current_capacity` | 数值 | `0` | 当前容量（可为无引号整数） |
| 6 | `design_capacity` | 数值 | `"17,850"` | 设计容量（引号 + 千分位） |
| 7 | `log_type` | 文本 | `Timer` | 日志类型（`Timer`/`Event` 等） |

坏行（列数 ≠7 / 时间解析失败 / 数值解析失败）跳过并计数，汇总进 load 返回的 `note`。

## 指标（spec 3.5，静态 4 指标）

| metric id | name | unit | aggregation |
|-----------|------|------|-------------|
| `battery_level` | 电池电量 | `%` | `avg` |
| `full_capacity` | 满充容量 | `mWh` | `avg` |
| `current_capacity` | 当前容量 | `mWh` | `avg` |
| `design_capacity` | 设计容量 | `mWh` | `avg` |

单位语义：NirSoft 以 mWh 展示容量，插件按 mWh 处理并标注（千分位剥离后为整数）。每条日志行产出 4 条 `Record`（同 timestamp，不同 metric）；`raw_line` 每 500 条抽样一条。

## key_values

`power_state` 与 `log_type` 的 ≤T 最新值（`BivIndex.state_at` 二分查询，O(log n)）：

```json
{"entries":[{"key":"power_state","value":"AC Power"},{"key":"log_type","value":"Timer"}]}
```

`annotate` 不实现（`capabilities.annotate: false`）。

## config.json（插件私有配置，spec 3.3）

| 键 | 取值 | 语义 |
|----|------|------|
| `time_format` | `"auto"` / `"m.d.y"` / `"d.m.y"` | `auto`：load 期用首行探测一次（先 `"m.d.y"` 后 `"d.m.y"`）并记住 `time_format_resolved`；解析行时若 `tfmt=="m.d.y"` 失败按行级回退再试 `"d.m.y"` 一次 |
| `encoding` | `"auto"` / `"utf-8"` / `"gbk"` | `auto`：BOM 探测 → UTF-8 宽松 → 乱码（≥10% 替换符）回退 GBK |

config.json 缺失 → 全默认；JSON 解析失败 → 全默认 + stderr WARN；未知键忽略。

## 运行依赖

- Python 3.10+（开发机 `pip install analysisbuddy-sdk` 一次安装；插件本体纯 stdlib，零第三方依赖）
- 仓库内无构建产物、无打包步骤；`.git/`、`tests/` 等无关文件宿主全无视

## 开发期测试

```powershell
python -m pytest tests/ -q
```

SDK 未安装时 tests 自动用 `tests/analysisbuddy_stub.py` 替身；parser.py（BIV-01）未提交时自动用 `tests/parser_stub.py` 替身，BIV-01 落地后自动走真实实现。

## 后续格式更新维护指引

- **时间格式变更**（如新增秒小数位 / 24 小时制）：改 `parser.py` 的 `_BIV_TIME_RE`/`parse_timestamp` 与 `main.py` 的 `on_can_handle` 时间正则；`config.json` 的 `time_format` 是扩展点，主流程无需改动。
- **列顺序 / 新增列**：改 `parser.py` 的 `parse_line` 列定义与本文列定义表；指标增删改在 `on_schema`（当前静态 4 指标）。
- **新增状态类字段（key_values）**：在 `parser.py` 的 `BivIndex` 增加对应列表，`main.py` 的 `on_key_values` 照现有模式追加 entry。
