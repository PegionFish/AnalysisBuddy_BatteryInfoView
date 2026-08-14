# batteryinfoview 插件行为单测（BIV-02 DoD）：can_handle 打分全分支、load 语义与
# 失败路径（含 time_format auto 首行探测）、schema 恰 4 指标、parse 计数与
# raw_line 抽样、行级 d.m.y 回退、key_values ≤T 最新语义、unload、manifest 逐字段。
# EmitContext 按正式 SDK 契约构造（file_id + sender），收集通知断言；
# parser 未就绪时 conftest 注入 tests/parser_stub.py，就绪后自动走真实 parser.py。

import json
import os

import pytest

from analysisbuddy import EmitContext, FileLoadFailedError
from parser import parse_timestamp

# 基于 ref_batteryinfoview.txt 的行格式（7 列，数值含千分位引号）
LINE_MDY_1 = '8/6/2026 3:30:57 PM,AC Power,100.0%,"99,072",0,"17,850",Timer\n'
LINE_MDY_2 = '8/6/2026 3:31:07 PM,AC Power,100.0%,"99,072",0,"17,850",Timer\n'
LINE_DC = '8/6/2026 3:40:00 PM,DC Power,63.5%,"54,432","31,120","60,000",On Battery\n'
# d.m.y 顺序且 m.d.y 无法解析（day=13 > 12 → %m/%d 失败）→ 必须行级回退
LINE_DMY_ONLY = '13/8/2026 3:31:07 PM,AC Power,100.0%,"99,072",0,"17,850",Timer\n'
BAD_LINE = "this is not a battery log line\n"
BAD_COLS = '8/6/2026 3:32:00 PM,AC Power,100.0%\n'

TS_MDY_1 = "8/6/2026 3:30:57 PM"
TS_MDY_2 = "8/6/2026 3:31:07 PM"
TS_DC = "8/6/2026 3:40:00 PM"
TS_DMY_ONLY = "13/8/2026 3:31:07 PM"

HEAD_POWER = LINE_MDY_1
HEAD_NO_POWER = '8/6/2026 3:30:57 PM,Unknown,100.0%\n'
HEAD_NO_TIME = 'AC Power,100.0%,"99,072"\n'
HEAD_DC = '8/6/2026 3:40:00 PM,DC Power,63.5%\n'
HEAD_LOWER = "8/6/2026 3:30:57 pm,ac power,100.0%\n"


@pytest.fixture
def tmp_log(tmp_path):
    """3 好行 + 1 坏行（坏列数行）。"""
    path = tmp_path / "input.txt"
    path.write_text(LINE_MDY_1 + LINE_MDY_2 + LINE_DC + BAD_COLS, encoding="utf-8")
    return str(path)


@pytest.fixture
def emitted():
    """收集 SDK EmitContext 发往宿主的所有通知（RecordBatch/progress）。"""
    notifications = []

    def sender(method: str, params: dict):
        notifications.append((method, params))

    ctx = EmitContext("f1", sender)
    return notifications, ctx


def _finish(emitted):
    """宿主行为：on_parse 返回后 SDK flush 残余 + done:true 末批。"""
    emitted[1].finish()


def _records(emitted):
    """从 RecordBatch 通知中收集全部 Record。"""
    records = []
    for method, params in emitted[0]:
        if method == "RecordBatch":
            records.extend(params["records"])
    return records


def _load(plugin, path, file_id="f1"):
    return plugin.on_load_file({"file_id": file_id, "path": path})


class TestCanHandle:
    def test_txt_time_power_090(self, plugin):
        assert plugin.on_can_handle(
            {"ext": "txt", "head_sample": HEAD_POWER}) == {
            "can_handle": True, "confidence": 0.9,
            "reason": "BatteryInfoView log format detected"}

    def test_txt_time_dc_power_090(self, plugin):
        result = plugin.on_can_handle({"ext": "txt", "head_sample": HEAD_DC})
        assert result["can_handle"] is True and result["confidence"] == 0.9

    def test_txt_time_lowercase_power_090(self, plugin):
        result = plugin.on_can_handle({"ext": "txt", "head_sample": HEAD_LOWER})
        assert result["confidence"] == 0.9

    def test_txt_time_no_power_050(self, plugin):
        # skip-if-empty：reason 无值时不输出键
        assert plugin.on_can_handle(
            {"ext": "txt", "head_sample": HEAD_NO_POWER}) == {
            "can_handle": True, "confidence": 0.5}

    def test_non_txt_abstains(self, plugin):
        assert plugin.on_can_handle(
            {"ext": "csv", "head_sample": HEAD_POWER}) == {
            "can_handle": False, "confidence": 0.0}

    def test_no_time_abstains(self, plugin):
        assert plugin.on_can_handle(
            {"ext": "txt", "head_sample": HEAD_NO_TIME}) == {
            "can_handle": False, "confidence": 0.0}

    def test_missing_head_sample_abstains(self, plugin):
        assert plugin.on_can_handle({"ext": "txt"}) == {
            "can_handle": False, "confidence": 0.0}


class TestLoadFile:
    def test_summary_hint_time_range_note(self, plugin, tmp_log):
        # P0：load 只做采样——hint 为样本估算（≤20 行小文件 == 行数）；
        # time_range start=样本首行 ts、end=尾部采样末行 ts
        summary = _load(plugin, tmp_log)
        assert summary["record_count_hint"] == 4
        tr = summary["time_range"]
        assert tr["start_ms"] == parse_timestamp(TS_MDY_1, "m.d.y")
        assert tr["end_ms"] == parse_timestamp(TS_DC, "m.d.y")
        assert tr["start_ms"] <= tr["end_ms"]
        assert summary["note"] == "batteryinfoview: ~4 records (head-sample estimate of 4 lines)"

    def test_missing_file_raises(self, plugin):
        with pytest.raises(FileLoadFailedError) as excinfo:
            _load(plugin, os.path.join("nonexistent", "missing.txt"))
        assert "file not found" in str(excinfo.value)
        assert excinfo.value.data == {"path": os.path.join("nonexistent", "missing.txt")}

    def test_single_good_line_loads(self, plugin, tmp_path):
        # 样本判据（首行可解析 且 样本中可解析行 ≥1）取代旧的 line_count≥2 整文件统计：
        # 单好行文件应能加载，hint 按行数估算
        path = tmp_path / "one.txt"
        path.write_text(LINE_MDY_1 + BAD_LINE, encoding="utf-8")
        summary = _load(plugin, str(path))
        assert summary["record_count_hint"] == 2
        assert summary["time_range"]["start_ms"] == parse_timestamp(TS_MDY_1, "m.d.y")

    def test_first_line_bad_raises(self, plugin, tmp_path):
        path = tmp_path / "junk_first.txt"
        path.write_text(BAD_LINE + LINE_MDY_1 + LINE_MDY_2, encoding="utf-8")
        with pytest.raises(FileLoadFailedError) as excinfo:
            _load(plugin, str(path))
        assert "not a BatteryInfoView log" in str(excinfo.value)

    def test_auto_probe_first_line_mdy(self, plugin, tmp_path):
        path = tmp_path / "mdy.txt"
        path.write_text(LINE_MDY_1 + LINE_MDY_2 + BAD_COLS, encoding="utf-8")
        summary = _load(plugin, str(path))
        assert summary["record_count_hint"] == 3

    def test_auto_probe_first_line_dmy(self, plugin, tmp_path):
        path = tmp_path / "dmy.txt"
        path.write_text(LINE_DMY_ONLY + LINE_DMY_ONLY.replace("3:31:07", "3:32:07"),
                        encoding="utf-8")
        summary = _load(plugin, str(path))
        # 首行 13/8/2026 在 m.d.y 下失败 → auto 探测回退 d.m.y，2 行全部成功
        assert summary["record_count_hint"] == 2

    def test_empty_file_raises(self, plugin, tmp_path):
        path = tmp_path / "empty.txt"
        path.write_text("", encoding="utf-8")
        with pytest.raises(FileLoadFailedError):
            _load(plugin, str(path))

    def test_head_sample_estimate_not_full_scan(self, plugin, tmp_path):
        # 30 行（>20 行样本窗口）：hint 按「前 20 行样本均长」估算，
        # 尾部追加长坏行使估算值明显偏离真实行数 30，证明非整文件统计
        short = LINE_MDY_1
        long_bad = "zzz," * 90 + "\n"  # 361 字节坏行
        path = tmp_path / "head30.txt"
        path.write_text(short * 25 + long_bad * 5, encoding="utf-8")
        summary = _load(plugin, str(path))
        with open(str(path), encoding="utf-8") as f:
            sample = [f.readline() for _ in range(20)]
        avg = sum(len(l) for l in sample) / len(sample)
        size = os.path.getsize(str(path))
        assert summary["record_count_hint"] == int(size / avg)
        assert summary["record_count_hint"] != 30  # 30 行文件 ≠ 精确行数

    def test_tail_sample_last_ts(self, plugin, tmp_path):
        # 80 行 > 4096 字节尾部窗口：end_ms 必须是末行 ts（来自尾部采样，非首部样本）
        path = tmp_path / "tail80.txt"
        lines = [LINE_MDY_1.replace("3:30:57", "3:%02d:%02d" % (30 + i // 60, i % 60))
                 for i in range(79)]
        last = LINE_MDY_2.replace("3:31:07", "4:00:00")
        path.write_text("".join(lines) + last, encoding="utf-8")
        summary = _load(plugin, str(path))
        assert summary["time_range"]["start_ms"] == parse_timestamp("8/6/2026 3:30:00 PM", "m.d.y")
        assert summary["time_range"]["end_ms"] == parse_timestamp("8/6/2026 4:00:00 PM", "m.d.y")
        # 估算公式（盘上字节 / 样本行均长）；80 行统一长度 → 贴近 80
        with open(str(path), encoding="utf-8") as f:
            sample = [f.readline() for _ in range(20)]
        avg = sum(len(l) for l in sample) / len(sample)
        assert summary["record_count_hint"] == int(os.path.getsize(str(path)) / avg)

    def test_tail_sample_all_bad_omits_time_range(self, plugin, tmp_path):
        # 尾部窗口内末 5 行全坏 → last_ts None → 省略 time_range 键
        path = tmp_path / "junk_tail.txt"
        path.write_text(LINE_MDY_1 * 20 + ("zzz," * 90 + "\n") * 5, encoding="utf-8")
        summary = _load(plugin, str(path))
        assert "time_range" not in summary
        assert summary["record_count_hint"] > 0


class TestSchema:
    def test_schema_exactly_four_metrics(self, plugin):
        metrics = plugin.on_schema()["metrics"]
        assert len(metrics) == 4
        by_id = {m["id"]: m for m in metrics}
        assert by_id["battery_level"] == {"id": "battery_level", "name": "电池电量",
                                          "unit": "%", "aggregation": "avg"}
        assert by_id["full_capacity"] == {"id": "full_capacity", "name": "满充容量",
                                          "unit": "mWh", "aggregation": "avg"}
        assert by_id["current_capacity"] == {"id": "current_capacity", "name": "当前容量",
                                             "unit": "mWh", "aggregation": "avg"}
        assert by_id["design_capacity"] == {"id": "design_capacity", "name": "设计容量",
                                            "unit": "mWh", "aggregation": "avg"}


class TestParse:
    def test_records_total_is_lines_times_four(self, plugin, tmp_log, emitted):
        _load(plugin, tmp_log)
        total = plugin.on_parse("f1", None, emitted[1])
        _finish(emitted)
        records = _records(emitted)
        assert total == 3 * 4
        assert len(records) == 3 * 4

    def test_record_fields(self, plugin, tmp_log, emitted):
        _load(plugin, tmp_log)
        plugin.on_parse("f1", None, emitted[1])
        _finish(emitted)
        records = _records(emitted)
        first = records[0]
        assert first["timestamp"] == parse_timestamp(TS_MDY_1, "m.d.y")
        assert first["metric"] == "battery_level"
        assert first["value"] == 100.0
        by_metric = {r["metric"]: r["value"] for r in records[:4]}
        assert by_metric == {"battery_level": 100.0, "full_capacity": 99072.0,
                             "current_capacity": 0.0, "design_capacity": 17850.0}
        dc_metric = {r["metric"]: r["value"] for r in records[8:12]}
        assert dc_metric["battery_level"] == 63.5
        assert dc_metric["full_capacity"] == 54432.0
        assert dc_metric["current_capacity"] == 31120.0
        assert dc_metric["design_capacity"] == 60000.0

    def test_raw_line_sampling(self, plugin, tmp_log, emitted):
        _load(plugin, tmp_log)
        plugin.on_parse("f1", None, emitted[1])
        _finish(emitted)
        records = _records(emitted)
        # 首组（total==0 → 500 整除）4 条带 raw_line
        assert records[0]["raw_line"] == LINE_MDY_1.rstrip("\n")
        assert records[3]["raw_line"] == LINE_MDY_1.rstrip("\n")
        # 第二组起（total=4 → 500 不整除）不带 raw_line
        assert "raw_line" not in records[4]
        assert "raw_line" not in records[-1]

    def test_raw_line_crlf_stripped(self, plugin, tmp_path, emitted):
        path = tmp_path / "crlf.txt"
        path.write_text(LINE_MDY_1.replace("\n", "\r\n") + LINE_MDY_2, encoding="utf-8")
        _load(plugin, str(path))
        plugin.on_parse("f1", None, emitted[1])
        _finish(emitted)
        records = _records(emitted)
        # 首组带 raw_line，CRLF 行不得残留尾部 \r
        assert records[0]["raw_line"] == LINE_MDY_1.rstrip("\r\n")
        assert "\r" not in records[0]["raw_line"]

    def test_bad_lines_skipped_in_parse(self, plugin, tmp_log, emitted):
        _load(plugin, tmp_log)
        total = plugin.on_parse("f1", None, emitted[1])
        _finish(emitted)
        assert total == 12

    def test_line_level_dmy_fallback(self, plugin, tmp_path, emitted):
        path = tmp_path / "mixed.txt"
        path.write_text(LINE_MDY_1 + LINE_DMY_ONLY + LINE_MDY_2, encoding="utf-8")
        _load(plugin, str(path))
        # 首行 m.d.y → load 期 resolved="m.d.y"；13/8/2026 行 m.d.y 失败 → 行级回退 d.m.y
        assert parse_timestamp(TS_DMY_ONLY, "m.d.y") is None
        ts_dmy = parse_timestamp(TS_DMY_ONLY, "d.m.y")
        assert ts_dmy is not None
        total = plugin.on_parse("f1", None, emitted[1])
        _finish(emitted)
        records = _records(emitted)
        assert total == 3 * 4
        assert records[4]["timestamp"] == ts_dmy
        assert records[4]["metric"] == "battery_level"
        assert records[4]["value"] == 100.0

    def test_progress_events(self, plugin, tmp_log, emitted):
        _load(plugin, tmp_log)
        size = os.path.getsize(tmp_log)
        plugin.on_parse("f1", None, emitted[1])
        _finish(emitted)
        progresses = [params for method, params in emitted[0] if method == "progress"]
        assert progresses, "no progress emitted"
        assert progresses[-1]["percent"] == 100.0
        assert progresses[-1]["bytes_read"] == size

    def test_parse_unknown_file_id_raises_keyerror(self, plugin, emitted):
        with pytest.raises(KeyError):
            plugin.on_parse("nope", None, emitted[1])


class TestE2EFixtures:
    @pytest.fixture
    def fixtures_dir(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(root, "tests", "fixtures")

    def test_sample_records_total(self, plugin, fixtures_dir, emitted):
        path = os.path.join(fixtures_dir, "biv_sample.txt")
        summary = _load(plugin, path)
        # 25 行统一长度 → 估算恰为 25
        assert summary["record_count_hint"] == 25
        total = plugin.on_parse("f1", None, emitted[1])
        _finish(emitted)
        records = _records(emitted)
        assert total == 25 * 4
        assert len(records) == 25 * 4

    def test_malformed_bad_lines(self, plugin, fixtures_dir):
        path = os.path.join(fixtures_dir, "biv_malformed.txt")
        summary = _load(plugin, path)
        # P0：load 不做整文件统计——hint 为估算（8 行文件 == 行数），坏行统计在 parse 期
        assert summary["record_count_hint"] == 8
        assert "estimate" in summary["note"]

    def test_dmy_fixture_fallback(self, plugin, fixtures_dir, emitted):
        path = os.path.join(fixtures_dir, "biv_dmy.txt")
        summary = _load(plugin, path)
        # load 期不做行级回退（裁定 5）：样本判据只看 resolved 格式（m.d.y 可解析行 ≥1）
        assert summary["record_count_hint"] == 6
        assert "estimate" in summary["note"]
        # on_parse 行级回退（§7 注）：d.m.y-only 2 行经 "d.m.y" 重试成功 → 5 行 × 4
        total = plugin.on_parse("f1", None, emitted[1])
        _finish(emitted)
        assert total == 5 * 4
        assert len(_records(emitted)) == 5 * 4


class TestKeyValues:
    def _load_and_parse(self, plugin, path, emitted):
        """load + parse 后 index 才可用（P0：BivIndex 构建推迟到 parse）。"""
        _load(plugin, path)
        plugin.on_parse("f1", None, emitted[1])
        _finish(emitted)

    def test_latest_state_at_tail(self, plugin, tmp_log, emitted):
        self._load_and_parse(plugin, tmp_log, emitted)
        entries = plugin.on_key_values("f1", 10**18)["entries"]
        by_key = {e["key"]: e for e in entries}
        assert by_key == {"power_state": {"key": "power_state", "value": "DC Power"},
                          "log_type": {"key": "log_type", "value": "On Battery"}}

    def test_state_at_midpoint(self, plugin, tmp_log, emitted):
        self._load_and_parse(plugin, tmp_log, emitted)
        t1 = parse_timestamp(TS_MDY_1, "m.d.y")
        t3 = parse_timestamp(TS_DC, "m.d.y")
        mid = (t1 + t3) // 2
        entries = plugin.on_key_values("f1", mid)["entries"]
        by_key = {e["key"]: e for e in entries}
        assert by_key["power_state"]["value"] == "AC Power"
        assert by_key["log_type"]["value"] == "Timer"

    def test_before_first_line_is_empty(self, plugin, tmp_log, emitted):
        self._load_and_parse(plugin, tmp_log, emitted)
        assert plugin.on_key_values("f1", 0) == {"entries": []}

    def test_before_parse_is_empty(self, plugin, tmp_log):
        # P0：file 已 load 但尚未 parse（index 未构建）→ 无状态可查
        _load(plugin, tmp_log)
        assert plugin.on_key_values("f1", 10**18) == {"entries": []}

    def test_unloaded_file_id_is_empty(self, plugin):
        assert plugin.on_key_values("nope", 123) == {"entries": []}

    def test_unload_file_clears_state(self, plugin, tmp_log, emitted):
        self._load_and_parse(plugin, tmp_log, emitted)
        assert plugin.on_key_values("f1", 10**18)["entries"]
        plugin.on_unload_file("f1")
        assert plugin.on_key_values("f1", 10**18) == {"entries": []}


class TestCapabilities:
    def test_annotate_capability_false(self, plugin):
        caps = plugin.on_initialize({"protocol_version": 1})["capabilities"]
        assert caps["annotate"] is False

    def test_plugin_identity(self, plugin):
        assert plugin.id == "batteryinfoview"
        assert plugin.name == "BatteryInfoView 电池日志解析器"
        assert plugin.version == "0.1.0"


class TestManifest:
    def test_plugin_json_matches_section_2(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "plugin.json"), encoding="utf-8") as f:
            manifest = json.load(f)
        assert manifest["id"] == "batteryinfoview"
        assert manifest["display_name"] == "BatteryInfoView 电池日志解析器"
        assert manifest["version"] == "0.1.0"
        assert manifest["entry"] == {"command": "python", "args": ["main.py"]}
        assert manifest["match"] == {
            "extensions": ["txt"],
            "header_fingerprints": ["AC Power", "DC Power"],
        }
        assert manifest["min_protocol_version"] == 1
        assert manifest["author"] == "PegionFish"
        assert manifest["repository"].startswith("https://")
        assert manifest["update_url"] == manifest["repository"]
        assert manifest["tools"] == ["AnalysisBuddy >= 0.1.0"]
        changelog = manifest["changelog"]
        assert changelog[0]["version"] == manifest["version"]
        assert changelog[0]["date"] == "2026-08-15"
        assert isinstance(changelog[0]["notes"], list) and changelog[0]["notes"]

    def test_presets_structure_and_ids(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "plugin.json"), encoding="utf-8") as f:
            manifest = json.load(f)
        presets = manifest["presets"]
        assert 1 <= len(presets) <= 32
        for preset in presets:
            assert preset["id"] == preset["id"].lower()
            assert "name" in preset and "zh" in preset["name"] and "en" in preset["name"]
            assert "description" in preset
            assert all(isinstance(k, str) and k for k in preset.get("keywords", []))
            for group in preset.get("groups", []):
                assert group["id"] and group["name"]["zh"] and group["name"]["en"]
                for entry in group["entries"]:
                    assert entry["names"]
            for entry in preset.get("entries", []):
                assert entry["names"]
        # 4 个静态指标双通道（metric_id + 中文名）穷举在预设中全覆盖
        names = set()
        for preset in presets:
            for group in preset.get("groups", []):
                for entry in group["entries"]:
                    names.update(entry["names"])
            for entry in preset.get("entries", []):
                names.update(entry["names"])
        for mid, cn in (("battery_level", "电池电量"), ("full_capacity", "满充容量"),
                        ("current_capacity", "当前容量"), ("design_capacity", "设计容量")):
            assert mid in names and cn in names
