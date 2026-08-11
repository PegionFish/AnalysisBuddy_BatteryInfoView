# batteryinfoview — BatteryInfoView 电池日志 TXT 解析插件（analysisbuddy-sdk）。
#
# 仅经 analysisbuddy-sdk 公共 API（AnalysisBuddyPlugin 子类 + serve()）与
# parser.py 公共接口（deep-dive §3）工作，不触碰 SDK 内部模块与 parser 内部逻辑。
# 仓库根即插件目录（plugin.json §4.5），clone 即用：
#   pip install analysisbuddy-sdk   # 开发机一次安装
#   python main.py                  # 宿主以 plugin.json entry 拉起
#
# 指标（spec 3.5）：battery_level / full_capacity / current_capacity /
# design_capacity 四个 avg 指标，每条日志行产出 4 条 Record，
# raw_line 每 500 条抽样一次。
# key_values（spec 3.5）：BivIndex.state_at 二分查询 ≤T 最新 power_state/log_type。
# annotate：不实现（capabilities.annotate=false）。
# 时间戳直读 UTC 毫秒不换算；time_format auto 在 load 期用首行探测一次并记住
# time_format_resolved；行级回退：tfmt=="m.d.y" 且 parse_line 失败时再试 "d.m.y"
# 一次（deep-dive §7 注，回退在 main.py 实现，parser 不做回退）。

import json
import os
import re
from typing import Dict, Optional

from analysisbuddy import AnalysisBuddyPlugin, FileLoadFailedError

from parser import BivIndex, parse_line

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
_DEFAULT_CONFIG = {"time_format": "auto", "encoding": "auto"}


def _decode_name(cfg: dict) -> str:
    """按 cfg.encoding 决定 open() 编码名（同 HWiNFO §5.3 语义）：
    "gbk"/"utf-8" 直用；"auto" 读首 3 字节判 BOM（utf-8-sig/utf-16），
    无 BOM 先按 utf-8 replace 读前 5 行样本，替换符 ≥10% 则回退 gbk。"""
    encoding = cfg.get("encoding", "auto")
    if encoding in ("utf-8", "gbk"):
        return encoding
    path = cfg.get("path")
    if path and os.path.exists(path):
        with open(path, "rb") as f:
            head = f.read(3)
        if head.startswith(b"\xef\xbb\xbf"):
            return "utf-8-sig"
        if head.startswith((b"\xff\xfe", b"\xfe\xff")):
            return "utf-16"
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                sample = [f.readline() for _ in range(5)]
        except OSError:
            sample = []
        if sample:
            total = sum(len(line) for line in sample)
            bad = sum(line.count("\ufffd") for line in sample)
            if total > 0 and bad / total >= 0.10:
                return "gbk"
    return "utf-8"


class BatteryInfoViewPlugin(AnalysisBuddyPlugin):
    id = "batteryinfoview"
    name = "BatteryInfoView 电池日志解析器"
    version = "0.1.0"

    def __init__(self) -> None:
        super().__init__()
        # file_id -> {"path","index","config","line_count","bad_lines","first_ts","last_ts"}
        self._files: Dict[str, Dict] = {}

    # ---- 生命周期 ------------------------------------------------------

    def _load_config(self) -> dict:
        """读取插件私有配置（§4）；文件缺失 → 全默认；解析失败 → 全默认 + WARN；
        未知键忽略。"""
        config = dict(_DEFAULT_CONFIG)
        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            return config
        except (OSError, ValueError) as exc:
            self.log("WARN", f"{_CONFIG_PATH} unreadable, using defaults: {exc}")
            return config
        for key in ("time_format", "encoding"):
            if key in raw:
                config[key] = raw[key]
        return config

    def _probe_time_format(self, config: dict, first_line: str) -> Optional[str]:
        """time_format auto → 用首行探测一次（先 m.d.y 再 d.m.y，§5.3）；
        显式取值直接返回；探测全失败返回 None（由调用方判定格式不符）。"""
        fmt = config.get("time_format", "auto")
        if fmt in ("m.d.y", "d.m.y"):
            return fmt
        if parse_line(first_line, "m.d.y") is not None:
            return "m.d.y"
        if parse_line(first_line, "d.m.y") is not None:
            return "d.m.y"
        return None

    def on_can_handle(self, p: dict) -> dict:
        ext = p.get("ext", "")
        head = p.get("head_sample") or ""
        has_time = bool(re.search(r"\d{1,2}/\d{1,2}/\d{4} \d{1,2}:\d{2}:\d{2} (AM|PM)", head, re.I))
        has_power = ("ac power" in head.lower()) or ("dc power" in head.lower())
        if ext != "txt" or not has_time:
            return {"can_handle": False, "confidence": 0.0, "reason": None}
        confidence = 0.9 if has_power else 0.5
        return {"can_handle": True, "confidence": confidence,
                "reason": "BatteryInfoView log format detected" if has_power else None}

    def on_load_file(self, p: dict) -> dict:
        path = p["path"]
        if not os.path.exists(path):
            raise FileLoadFailedError(f"file not found: {path}", data={"path": path})

        config = self._load_config()
        config["path"] = path  # 供 _decode_name 的 auto 探测
        encoding = _decode_name(config)

        with open(path, "r", encoding=encoding, errors="replace") as f:
            first_line = f.readline()

        resolved = self._probe_time_format(config, first_line)
        if resolved is None:
            raise FileLoadFailedError("not a BatteryInfoView log", data={"path": path})
        config["time_format_resolved"] = resolved

        index = BivIndex()
        line_count = 0
        bad_lines = 0
        first_ts: Optional[int] = None
        last_ts: Optional[int] = None
        with open(path, "r", encoding=encoding, errors="replace") as f:
            for raw in f:
                parsed = parse_line(raw, resolved)
                if parsed is None:
                    bad_lines += 1
                    continue
                index.add(parsed)
                line_count += 1
                if first_ts is None:
                    first_ts = parsed.timestamp
                last_ts = parsed.timestamp

        if line_count < 2 or parse_line(first_line, resolved) is None:
            raise FileLoadFailedError("not a BatteryInfoView log", data={"path": path})

        self._files[p["file_id"]] = {
            "path": path,
            "index": index,
            "config": config,
            "line_count": line_count,
            "bad_lines": bad_lines,
            "first_ts": first_ts,
            "last_ts": last_ts,
        }
        summary = {"record_count_hint": line_count}
        if first_ts is not None:
            summary["time_range"] = {"start_ms": first_ts, "end_ms": last_ts}
        summary["note"] = f"batteryinfoview: {line_count} lines, {bad_lines} bad lines skipped"
        return summary

    def on_schema(self) -> dict:
        return {"metrics": [
            {"id": "battery_level", "name": "电池电量", "unit": "%", "aggregation": "avg"},
            {"id": "full_capacity", "name": "满充容量", "unit": "mWh", "aggregation": "avg"},
            {"id": "current_capacity", "name": "当前容量", "unit": "mWh", "aggregation": "avg"},
            {"id": "design_capacity", "name": "设计容量", "unit": "mWh", "aggregation": "avg"},
        ]}

    def on_parse(self, file_id: str, options: Optional[dict], ctx) -> int:
        data = self._files[file_id]
        path = data["path"]
        tfmt = data["config"]["time_format_resolved"]
        total = 0
        line_no = 0
        file_bytes = os.path.getsize(path)
        with open(path, "r", encoding=_decode_name(data["config"]), errors="replace") as f:
            for raw in f:
                ctx.check_cancelled()
                line_no += 1
                parsed = parse_line(raw, tfmt)
                if parsed is None and tfmt == "m.d.y":
                    parsed = parse_line(raw, "d.m.y")  # 行级回退（§7 注）
                if parsed is None:
                    continue
                records = [
                    {"timestamp": parsed.timestamp, "metric": "battery_level",
                     "value": parsed.battery_level},
                    {"timestamp": parsed.timestamp, "metric": "full_capacity",
                     "value": float(parsed.full_capacity)},
                    {"timestamp": parsed.timestamp, "metric": "current_capacity",
                     "value": float(parsed.current_capacity)},
                    {"timestamp": parsed.timestamp, "metric": "design_capacity",
                     "value": float(parsed.design_capacity)},
                ]
                if total % 500 == 0:
                    for r in records:
                        r["raw_line"] = raw.rstrip("\r\n")
                ctx.emit_records(records)
                total += 4
                if line_no % 20000 == 0:
                    ctx.progress(percent=None, bytes_read=None)
        ctx.progress(percent=100.0, bytes_read=file_bytes)
        return total

    def on_key_values(self, file_id: str, timestamp_ms: int) -> dict:
        data = self._files.get(file_id)
        if data is None:
            return {"entries": []}
        state = data["index"].state_at(timestamp_ms)
        return {"entries": [{"key": k, "value": v} for k, v in state.items()]}

    def on_unload_file(self, file_id: str) -> None:
        self._files.pop(file_id, None)


if __name__ == "__main__":
    BatteryInfoViewPlugin().serve()
