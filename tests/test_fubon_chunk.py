import sys, os, datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "analysis"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pandas as pd
import data_fetcher as dfm


def test_chunks_two_year_range(monkeypatch):
    calls = []

    def fake_candles(symbol, frm, to, tf):
        calls.append((frm, to))
        idx = pd.to_datetime([frm, to])
        return pd.DataFrame({"Open": [1, 1], "High": [1, 1], "Low": [1, 1],
                             "Close": [1, 1], "Volume": [0, 0]}, index=idx)

    monkeypatch.setattr(dfm.FubonMarketData, "get_historical_candles", staticmethod(fake_candles))
    out = dfm.DataSourceManager._fubon_candles_chunked("2382", "2024-06-04", "2026-06-04")
    # 730 天 → 應切成多段（每段 <= 364 天）
    assert len(calls) >= 2
    for frm, to in calls:
        s = datetime.datetime.strptime(frm, "%Y-%m-%d")
        e = datetime.datetime.strptime(to, "%Y-%m-%d")
        assert (e - s).days <= 364
    assert out is not None and len(out) >= 2
    assert out.index.is_monotonic_increasing


def test_single_call_when_under_year(monkeypatch):
    calls = []

    def fake_candles(symbol, frm, to, tf):
        calls.append((frm, to))
        return pd.DataFrame({"Open": [1], "High": [1], "Low": [1], "Close": [1], "Volume": [0]},
                            index=pd.to_datetime([frm]))

    monkeypatch.setattr(dfm.FubonMarketData, "get_historical_candles", staticmethod(fake_candles))
    dfm.DataSourceManager._fubon_candles_chunked("2382", "2026-01-01", "2026-06-04")
    assert len(calls) == 1   # < 1 年 → 單次
