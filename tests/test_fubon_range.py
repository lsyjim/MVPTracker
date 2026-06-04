import sys, os, datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "analysis"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import data_fetcher as dfm

DSM = dfm.DataSourceManager


def test_range_days_period():
    assert DSM._range_days(None, None, "6mo") == 180
    assert DSM._range_days(None, None, "2y") == 730
    assert DSM._range_days(None, None, "5d") == 5


def test_range_days_dates():
    s = datetime.datetime(2024, 6, 4)
    e = datetime.datetime(2026, 6, 4)
    assert DSM._range_days(s, e, None) == 730  # ~2 年


def test_long_range_skips_fubon(monkeypatch):
    """> 1 年的請求不應呼叫富邦（避免 400/429 與誤觸熔斷），直接走 yfinance。"""
    calls = {"fubon": 0, "yf": 0, "disabled": 0}
    monkeypatch.setattr(DSM, "is_fubon_available", classmethod(lambda cls: True))
    monkeypatch.setattr(DSM, "_serve_from_batch", classmethod(lambda cls, *a, **k: None))
    monkeypatch.setattr(DSM, "_disable_fubon_temporarily", classmethod(lambda cls: calls.__setitem__("disabled", calls["disabled"] + 1)))

    def fake_fubon(cls, *a, **k):
        calls["fubon"] += 1
        return None
    import pandas as pd

    def fake_yf(cls, *a, **k):
        calls["yf"] += 1
        return pd.DataFrame({"Close": [1, 2]}, index=pd.to_datetime(["2024-06-04", "2024-06-05"]))
    monkeypatch.setattr(DSM, "_get_history_fubon", classmethod(fake_fubon))
    monkeypatch.setattr(DSM, "_get_history_yfinance", classmethod(fake_yf))

    DSM.get_history("2330", "台股", period="2y")
    assert calls["fubon"] == 0      # 富邦未被呼叫
    assert calls["disabled"] == 0   # 未誤觸熔斷
    assert calls["yf"] == 1         # 走 yfinance


def test_short_range_uses_fubon(monkeypatch):
    calls = {"fubon": 0, "yf": 0}
    monkeypatch.setattr(DSM, "is_fubon_available", classmethod(lambda cls: True))
    monkeypatch.setattr(DSM, "_serve_from_batch", classmethod(lambda cls, *a, **k: None))
    monkeypatch.setattr(DSM, "_reset_fubon_failure", classmethod(lambda cls: None))
    import pandas as pd

    def fake_fubon(cls, *a, **k):
        calls["fubon"] += 1
        return pd.DataFrame({"Close": [1, 2]}, index=pd.to_datetime(["2026-01-01", "2026-01-02"]))
    monkeypatch.setattr(DSM, "_get_history_fubon", classmethod(fake_fubon))
    monkeypatch.setattr(DSM, "_get_history_yfinance", classmethod(lambda cls, *a, **k: (_ for _ in ()).throw(AssertionError("should not call yf"))))

    DSM.get_history("2330", "台股", period="6mo")
    assert calls["fubon"] == 1
