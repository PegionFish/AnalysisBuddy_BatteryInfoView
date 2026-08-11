# parser 开发期替身（BIV-02 产出）：与 deep-dive §3 公共接口签名一致的占位实现。
#
# conftest 仅在仓库根尚无真实 parser.py（BIV-01 未提交）时把本模块注册为
# sys.modules["parser"]；BIV-01 落地后自动让位，测试走真实实现。
# 语义按 §3/§3.1 忠实实现（csv 切列、%m/%d 与 %d/%m 两态、BivIndex 二分查询），
# 使插件级单测在替身与真实 parser 下行为一致。

import csv
import datetime
import re
import sys
from bisect import bisect_right
from typing import Dict, List, Optional

_BIV_TIME_RE = re.compile(
    r"^(\d{1,2}/\d{1,2}/\d{4}) (\d{1,2}:\d{2}:\d{2}) (AM|PM)$", re.IGNORECASE)


class BivLine:
    """一行日志的结构化结果。"""
    __slots__ = ("timestamp", "power_state", "battery_level",
                 "full_capacity", "current_capacity", "design_capacity", "log_type")

    def __init__(self, timestamp: int, power_state: str, battery_level: float,
                 full_capacity: int, current_capacity: int,
                 design_capacity: int, log_type: str) -> None:
        # timestamp: UTC 毫秒（本地时间直读，spec D3）
        # battery_level: 剥离 % 后的数值（如 100.0）
        # full_capacity/current_capacity/design_capacity: 剥离千分位与引号的整数（mWh）
        self.timestamp = timestamp
        self.power_state = power_state
        self.battery_level = battery_level
        self.full_capacity = full_capacity
        self.current_capacity = current_capacity
        self.design_capacity = design_capacity
        self.log_type = log_type


def parse_timestamp(text: str, time_format: str) -> Optional[int]:
    """BIV 时间戳 → UTC 毫秒。time_format ∈ {"m.d.y","d.m.y"}：
    "m.d.y" → strptime "%m/%d/%Y %I:%M:%S %p"（ref 事实，spec 3.3）；
    "d.m.y" → "%d/%m/%Y %I:%M:%S %p"。先用 _BIV_TIME_RE 校验形状，
    失败返回 None。"""
    if _BIV_TIME_RE.match(text.strip()) is None:
        return None
    if time_format == "m.d.y":
        pattern = "%m/%d/%Y %I:%M:%S %p"
    elif time_format == "d.m.y":
        pattern = "%d/%m/%Y %I:%M:%S %p"
    else:
        return None
    try:
        dt = datetime.datetime.strptime(text.strip(), pattern)
    except ValueError:
        return None
    return int(dt.timestamp() * 1000)


def parse_line(line: str, time_format: str) -> Optional[BivLine]:
    """一行日志 → BivLine；列数 ≠ 7 / 时间解析失败 / 任一数值解析失败 → None。
    实现：csv.reader([line]) 切列（引号感知自动处理千分位引号字段）；
    数值列 strip 逗号后 int()/float()（battery_level 需 strip 尾部 %）。"""
    cells = next(csv.reader([line]))
    if len(cells) != 7:
        return None
    timestamp = parse_timestamp(cells[0], time_format)
    if timestamp is None:
        return None
    try:
        battery_level = float(cells[2].strip().rstrip("%"))
        full_capacity = int(cells[3].replace(",", ""))
        current_capacity = int(cells[4].replace(",", ""))
        design_capacity = int(cells[5].replace(",", ""))
    except ValueError:
        return None
    return BivLine(timestamp, cells[1], battery_level, full_capacity,
                   current_capacity, design_capacity, cells[6])


class BivIndex:
    """按 timestamp 有序的状态索引（key_values 用，spec 3.5）。
    power_state/log_type 各自维护 (ts, value) 有序列表；查询 bisect O(log n)。"""

    def __init__(self) -> None:
        self._power_state: List[tuple] = []
        self._log_type: List[tuple] = []

    def add(self, line: BivLine) -> None:
        self._power_state.append((line.timestamp, line.power_state))
        self._log_type.append((line.timestamp, line.log_type))

    def state_at(self, timestamp_ms: int) -> Dict[str, str]:
        """返回 ≤T 最新 power_state 与 log_type（key: "power_state"/"log_type"）；
        无则省略对应 key。"""
        state: Dict[str, str] = {}
        for entries, key in ((self._power_state, "power_state"),
                             (self._log_type, "log_type")):
            idx = bisect_right([t for t, _ in entries], timestamp_ms) - 1
            if idx >= 0:
                state[key] = entries[idx][1]
        return state


def _install_stub() -> None:
    if sys.modules.get("parser") is not None:
        return
    sys.modules["parser"] = sys.modules[__name__]


_install_stub()
