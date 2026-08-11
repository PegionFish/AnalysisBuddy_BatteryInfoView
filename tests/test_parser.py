# batteryinfoview parser 单测（BIV-01 DoD）：
# parse_timestamp（m.d.y/d.m.y × AM/PM × 形状不符）、parse_line（正常含引号千分位、
# 列数≠7、数值非法、时间非法）、BivIndex.state_at（≤T 最新、无记录省略 key、后覆盖前）。
# 期望毫秒为契约表达式（本地墙上时间直读 UTC 毫秒，不做时区换算）的定值。

import datetime

import pytest

from parser import BivIndex, BivLine, parse_line, parse_timestamp

# ref 事实：m.d.y 下 8/6/2026 3:30:57 PM == 2026-08-06 15:30:57 UTC
TS_MDY = "8/6/2026 3:30:57 PM"
TS_MDY_2 = "8/6/2026 3:31:07 PM"
TS_MDY_AM = "8/6/2026 3:30:57 AM"
TS_DMY = "13/8/2026 3:31:07 PM"
TS_DMY_6_8 = "6/8/2026 3:30:57 PM"  # d.m.y 下与 TS_MDY 同一时刻（8 月 6 日）

EXPECTED_MDY = 1786030257000
EXPECTED_DMY_6_8 = 1786030257000
EXPECTED_DMY_13 = 1786635067000

LINE_OK = '8/6/2026 3:30:57 PM,AC Power,100.0%,"99,072",0,"17,850",Timer'
LINE_DMY_OK = '13/8/2026 3:31:07 PM,AC Power,100.0%,"99,072",0,"17,850",Timer'


class TestParseTimestamp:
    def test_mdy_expected_utc_ms(self):
        assert parse_timestamp(TS_MDY, "m.d.y") == EXPECTED_MDY

    def test_mdy_second_line(self):
        assert parse_timestamp(TS_MDY_2, "m.d.y") == EXPECTED_MDY + 10000

    def test_mdy_am_earlier_than_pm(self):
        am = parse_timestamp(TS_MDY_AM, "m.d.y")
        assert am == EXPECTED_MDY - 12 * 3600 * 1000

    def test_dmy_same_instant_as_mdy(self):
        assert parse_timestamp(TS_DMY_6_8, "d.m.y") == EXPECTED_DMY_6_8

    def test_dmy_day_over_twelve(self):
        assert parse_timestamp(TS_DMY, "d.m.y") == EXPECTED_DMY_13

    def test_dmy_day_over_twelve_fails_mdy(self):
        assert parse_timestamp(TS_DMY, "m.d.y") is None

    def test_shape_mismatch_no_am_pm(self):
        assert parse_timestamp("8/6/2026 3:30:57", "m.d.y") is None

    def test_shape_mismatch_dotted_date(self):
        assert parse_timestamp("8.6.2026 3:30:57 PM", "m.d.y") is None

    def test_shape_mismatch_iso(self):
        assert parse_timestamp("2026-08-06 15:30:57", "m.d.y") is None

    def test_shape_ok_but_time_invalid(self):
        assert parse_timestamp("8/6/2026 25:30:57 PM", "m.d.y") is None

    def test_unknown_time_format(self):
        assert parse_timestamp(TS_MDY, "y.d.m") is None

    def test_empty_text(self):
        assert parse_timestamp("", "m.d.y") is None


class TestParseLine:
    def test_normal_line_fields(self):
        line = parse_line(LINE_OK, "m.d.y")
        assert isinstance(line, BivLine)
        assert line.timestamp == EXPECTED_MDY
        assert line.power_state == "AC Power"
        assert line.battery_level == 100.0
        assert line.full_capacity == 99072
        assert line.current_capacity == 0
        assert line.design_capacity == 17850
        assert line.log_type == "Timer"

    def test_normal_line_dmy(self):
        line = parse_line(LINE_DMY_OK, "d.m.y")
        assert line is not None
        assert line.timestamp == EXPECTED_DMY_13

    def test_dmy_line_fails_mdy(self):
        assert parse_line(LINE_DMY_OK, "m.d.y") is None

    def test_too_few_columns(self):
        assert parse_line('8/6/2026 3:30:57 PM,AC Power,100.0%,0,"17,850"', "m.d.y") is None

    def test_too_many_columns(self):
        line = LINE_OK + ',Extra'
        assert parse_line(line, "m.d.y") is None

    def test_invalid_number(self):
        assert parse_line(
            '8/6/2026 3:30:57 PM,AC Power,100.0%,"99,0A2",0,"17,850",Timer',
            "m.d.y") is None

    def test_invalid_time(self):
        assert parse_line(
            '8/6/2026 25:30:57 PM,AC Power,100.0%,"99,072",0,"17,850",Timer',
            "m.d.y") is None

    def test_invalid_battery_level(self):
        assert parse_line(
            '8/6/2026 3:30:57 PM,AC Power,1oo.0%,"99,072",0,"17,850",Timer',
            "m.d.y") is None

    def test_trailing_newline(self):
        line = parse_line(LINE_OK + "\n", "m.d.y")
        assert line is not None
        assert line.timestamp == EXPECTED_MDY

    def test_garbage_line(self):
        assert parse_line("this is not a battery log line", "m.d.y") is None


class TestBivIndex:
    def _make_line(self, timestamp: int, power: str, log: str) -> BivLine:
        return BivLine(timestamp, power, 100.0, 99072, 0, 17850, log)

    def test_state_at_latest_not_after_t(self):
        t1, t2, t3 = 1000, 2000, 3000
        idx = BivIndex()
        idx.add(self._make_line(t1, "AC Power", "Timer"))
        idx.add(self._make_line(t2, "AC Power", "On Battery"))
        idx.add(self._make_line(t3, "DC Power", "On Battery"))
        assert idx.state_at(t1) == {"power_state": "AC Power", "log_type": "Timer"}
        assert idx.state_at(t1 + 1) == {"power_state": "AC Power", "log_type": "Timer"}
        assert idx.state_at(t2) == {"power_state": "AC Power", "log_type": "On Battery"}
        assert idx.state_at((t2 + t3) // 2) == {"power_state": "AC Power", "log_type": "On Battery"}
        assert idx.state_at(t3) == {"power_state": "DC Power", "log_type": "On Battery"}
        assert idx.state_at(10**18) == {"power_state": "DC Power", "log_type": "On Battery"}

    def test_empty_index_omits_keys(self):
        assert BivIndex().state_at(10**18) == {}

    def test_before_first_record_omits_keys(self):
        idx = BivIndex()
        idx.add(self._make_line(1000, "AC Power", "Timer"))
        assert idx.state_at(0) == {}
        assert idx.state_at(999) == {}

    def test_later_line_overwrites_at_same_timestamp(self):
        idx = BivIndex()
        idx.add(self._make_line(1000, "AC Power", "Timer"))
        idx.add(self._make_line(1000, "DC Power", "On Battery"))
        assert idx.state_at(1000) == {"power_state": "DC Power", "log_type": "On Battery"}

    def test_both_keys_present_after_any_record(self):
        idx = BivIndex()
        idx.add(self._make_line(1000, "AC Power", "Timer"))
        state = idx.state_at(5000)
        assert set(state) == {"power_state", "log_type"}


class TestFixtures:
    @pytest.fixture
    def fixture_text(self):
        import os

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        base = os.path.join(root, "tests", "fixtures")

        def read(name: str) -> str:
            with open(os.path.join(base, name), encoding="utf-8") as f:
                return f.read()

        return read

    def test_sample_line_counts(self, fixture_text):
        text = fixture_text("biv_sample.txt")
        lines = [l for l in text.splitlines() if l.strip()]
        assert len(lines) == 25
        good = [l for l in lines if parse_line(l, "m.d.y") is not None]
        assert len(good) == 25

    def test_malformed_bad_lines(self, fixture_text):
        text = fixture_text("biv_malformed.txt")
        lines = [l for l in text.splitlines() if l.strip()]
        good = [l for l in lines if parse_line(l, "m.d.y") is not None]
        assert len(lines) == 8
        assert len(good) == 5

    def test_dmy_fixture_fallback(self, fixture_text):
        text = fixture_text("biv_dmy.txt")
        lines = [l for l in text.splitlines() if l.strip()]
        assert len(lines) == 6
        mdy_ok = [l for l in lines if parse_line(l, "m.d.y") is not None]
        assert len(mdy_ok) == 3
        fallback = [l for l in lines if parse_line(l, "m.d.y") is None
                    and parse_line(l, "d.m.y") is not None]
        assert len(fallback) == 2
        both_fail = [l for l in lines if parse_line(l, "m.d.y") is None
                     and parse_line(l, "d.m.y") is None]
        assert len(both_fail) == 1
