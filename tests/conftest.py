# pytest 夹具：把插件仓库根加入 sys.path（仓库根即插件目录，§4.6）；
# SDK 未安装时注入开发期替身（analysisbuddy_stub）；仓库根尚无真实 parser.py
# （BIV-01 未提交）时注入 parser_stub 为模块 parser。两者都在 conftest 导入期
# 完成，保证测试模块顶层的 `from parser import ...` / `from analysisbuddy import ...`
# 可用；真实 SDK / parser 就绪后本 conftest 自动让位。

import importlib.util
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HAS_REAL_SDK = importlib.util.find_spec("analysisbuddy") is not None
HAS_REAL_PARSER = importlib.util.find_spec("parser") is not None

if not HAS_REAL_SDK:
    import analysisbuddy_stub  # noqa: F401  注册 sys.modules["analysisbuddy"]
if not HAS_REAL_PARSER:
    import parser_stub  # noqa: F401  注册 sys.modules["parser"]


@pytest.fixture
def plugin():
    from main import BatteryInfoViewPlugin

    return BatteryInfoViewPlugin()
