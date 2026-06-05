# data/fetcher.py — 包裝 vendored DataSourceManager
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "analysis"))
from data_fetcher import DataSourceManager


def get_history(code, market="台股", period="6mo"):
    return DataSourceManager.get_history(code, market, period=period)


def get_quote(code, market="台股"):
    return DataSourceManager.get_realtime_price(code, market)


def fubon_available() -> bool:
    return DataSourceManager.is_fubon_available()


def init_fubon(sdk=None) -> bool:
    try:
        return DataSourceManager.initialize(sdk)
    except Exception as e:
        print(f"[fetcher] 富邦初始化失敗: {e}")
        return False


def recent_daily(code, n=6, market="台股"):
    """近 n 個交易日：[{date, close, pct}]（oldest→newest）。
    pct 用前一交易日收盤比較（日線本身已跳過假日）→ 永遠是『最近交易日 vs 前一交易日』。"""
    df = get_history(code, market, period="1mo")
    if df is None or len(df) < 2:
        return []
    closes = [float(x) for x in df["Close"]]
    dates = list(df.index)
    out = []
    for i in range(1, len(closes)):
        prev, cur = closes[i - 1], closes[i]
        out.append({"date": dates[i].strftime("%Y-%m-%d"), "close": round(cur, 2),
                    "pct": round((cur / prev - 1) * 100, 2) if prev else 0.0})
    return out[-n:]


def today_change(code, market="台股"):
    """今日漲跌幅（最近交易日收盤 vs 前一交易日收盤）；取不到回 None。"""
    r = recent_daily(code, 2, market)
    return r[-1]["pct"] if r else None


_index_cache = {"ts": 0, "data": None}


def get_index(symbol="^TWII", ttl=120):
    """加權指數（TAIEX）現值與漲跌%。富邦(IX0001)優先（即時、不落後），yfinance ^TWII fallback。"""
    import time as _t
    if _index_cache["data"] and _t.time() - _index_cache["ts"] < ttl:
        return _index_cache["data"]
    # 富邦優先（台股指數即時，最新交易日）
    try:
        from data_fetcher import FubonMarketData
        if FubonMarketData.is_available():
            q = FubonMarketData._rest_client.intraday.quote(symbol="IX0001")
            if isinstance(q, dict):
                val = q.get("lastPrice") or q.get("closePrice")
                cp = q.get("changePercent")
                if val:
                    data = {"value": round(float(val), 2), "change_pct": round(float(cp or 0), 2)}
                    _index_cache.update(ts=_t.time(), data=data)
                    return data
    except Exception as e:
        print(f"[fetcher] 富邦指數取得失敗: {e}")
    # fallback：yfinance ^TWII（日線最後兩個交易日）
    try:
        import yfinance as yf
        d = yf.Ticker(symbol).history(period="5d")
        if len(d) >= 2:
            c = float(d["Close"].iloc[-1]); p = float(d["Close"].iloc[-2])
            data = {"value": round(c, 2), "change_pct": round((c / p - 1) * 100, 2)}
            _index_cache.update(ts=_t.time(), data=data)
            return data
    except Exception as e:
        print(f"[fetcher] 加權指數取得失敗: {e}")
    return None


def ohlc_for_echart(code, market="台股", period="3mo"):
    """回 ECharts candlestick 用 [[open,close,low,high],...] 與 x 軸日期。"""
    df = get_history(code, market, period)
    if df is None or len(df) == 0:
        return None, None
    x = [d.strftime("%m/%d") for d in df.index]
    data = [[round(float(r.Open), 2), round(float(r.Close), 2), round(float(r.Low), 2), round(float(r.High), 2)]
            for r in df.itertuples()]
    return x, data
