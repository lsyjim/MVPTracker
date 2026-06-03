import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from concept import master


def test_lookup_known():
    # 2330 台積電 應可由 twstock 取得名稱
    name = master.lookup_name("2330")
    assert name and ("積" in name or name != "2330")


def test_lookup_unknown_returns_none():
    assert master.lookup_name("00000") is None
