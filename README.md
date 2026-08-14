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

坏行（列数 ≠7 / 时间解析失败 / 数值解析失败）在 parse 期跳过并计数，不产出 Record；load 期不做全文件扫描（只做头部/尾部采样），`record_count_hint` 为按样本估算的近似值，精确值以 parse 返回的 `records_total` 为准。

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

## load 语义（P0：10s 超时预算）

协议 `load_file` 仅 10s 预算，插件不在 load 期全文件预扫描建索引或做精确统计，只做 O(表头+样本) 工作：

- **头部采样**：读前 20 行样本；`time_format` 用首行探测（首行非法即拒），"not a BatteryInfoView log" 判据为样本中可解析行 ≥1；
- **尾部采样**：seek 到文件尾部 4096 字节窗口，取末 5 行解析出 `last_ts`（全坏 → 省略 `time_range`）；
- `first_ts` 取样本首行 ts，`record_count_hint` = `int(文件字节 / 样本行均字节数)`（估算，UI 提示用）；
- `BivIndex` 构建与 `bad_lines` 统计推迟到 `on_parse`（parse 有 15min 预算与进度上报）；parse 之前 `key_values` 返回 `{"entries": []}`。

## 运行依赖

- Python 3.10+（开发机 `pip install analysisbuddy-sdk` 一次安装；插件本体纯 stdlib，零第三方依赖）
- 仓库内无构建产物；发布包由 `scripts\pack.ps1` 生成（见「打包与发布」），`.git/`、`tests/` 等无关文件不进入发布包

## 开发期测试

```powershell
python -m pytest tests/ -q
```

SDK 未安装时 tests 自动用 `tests/analysisbuddy_stub.py` 替身；parser.py（BIV-01）未提交时自动用 `tests/parser_stub.py` 替身，BIV-01 落地后自动走真实实现。

## 后续格式更新维护指引

- **时间格式变更**（如新增秒小数位 / 24 小时制）：改 `parser.py` 的 `_BIV_TIME_RE`/`parse_timestamp` 与 `main.py` 的 `on_can_handle` 时间正则；`config.json` 的 `time_format` 是扩展点，主流程无需改动。
- **列顺序 / 新增列**：改 `parser.py` 的 `parse_line` 列定义与本文列定义表；指标增删改在 `on_schema`（当前静态 4 指标）。
- **新增状态类字段（key_values）**：在 `parser.py` 的 `BivIndex` 增加对应列表，`main.py` 的 `on_key_values` 照现有模式追加 entry。

## 打包与发布

发布包要求 `plugin.json` 位于 zip 根（协议 §7.3），且除清单文件外不混入 `.git/`、`tests/`、`.github/` 等无关内容。用仓库自带脚本打包：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\pack.ps1
# 输出 dist\AnalysisBuddy_batteryinfoview_v0.1.0.zip + dist\SHA256SUMS.txt
# 可选参数：-OutDir <目录>  -Version <版本>（默认取 plugin.json 的 version）
```

脚本内嵌校验（失败即中止）：zip 条目无绝对路径 / `..` 越界、`plugin.json` 位于 zip 根、zip 内 manifest 的 `id`/`version` 与打包参数一致；随后生成 `SHA256SUMS.txt`（`hash  filename` 格式）。

**发布流程**：打 tag（`v*` 前缀，如 `v0.1.0`）并 push → GitHub Actions（`.github/workflows/release.yml`）在 `windows-latest` 上运行 pack.ps1，并把 zip + SHA256SUMS.txt 上传为对应 tag 的 Release 资产：

```powershell
git tag v0.1.0
git push origin v0.1.0
```

**tag 规范**：tag 名 `v<semver>`，与 `plugin.json` 的 `version`（如 `0.1.0`）一一对应；发新版本时先改 manifest `version` 并在 `changelog` 顶部追加条目，再打同版本 tag。本地也可手动验证：`dist\SHA256SUMS.txt` 与 zip 的 `Get-FileHash -Algorithm SHA256` 一致即为合规资产。

## 更新链路

`update_url` 为 `https://github.com/PegionFish/AnalysisBuddy_BatteryInfoView`（全 URL 形式，宿主按 `owner/repo` 消费）。更新契约要点：

- 宿主按 Release 上的**唯一 zip 资产**拉取更新——一个 Release 只挂一个插件 zip（另有 SHA256SUMS.txt 校验清单），挂多个 zip 会破坏「单资产」假设；
- zip 根必须直接含 `plugin.json`（本仓库 pack.ps1 已保证）；版本比对以 zip 内 `plugin.json` 的 `version` 为准，须高于宿主已装版本；
- 更新后插件目录整体替换，插件内不保留任何跨版本状态（见「数据纪律」）；`config.json` 的升级迁移见下文。

## 数据纪律（NDA）

- **只写插件自身目录**：插件运行时任何落盘数据（如缓存、临时文件、用户配置的衍生物）只允许写入宿主分配给插件的自身目录，绝不向任意路径写文件；`config.json` 是唯一持久配置载体，其余数据不落盘。
- **卸载即彻底清除**：宿主卸载插件时删除插件目录整体，插件不得在插件目录之外（如用户目录、系统临时目录）留存任何数据；本插件当前运行时零落盘，天然满足。
- **升级时 config.json 需插件自迁移**：宿主升级流程先删旧插件目录再解压新包，`config.json` 属插件私有配置，升级后不会自动保留——用户手动修改过的配置（如固定 `time_format`/`encoding`）请在升级前手动备份，或在升级后按需重新配置；插件侧对缺失/损坏的 config.json 一律回退默认值并 WARN（`main.py` `_load_config`），不因配置缺失而拒绝加载。
