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


_index_cache = {"ts": 0, "data": None}


def get_index(symbol="^TWII", ttl=300):
    """加權指數（TAIEX）現值與漲跌%。yfinance 取得，5 分鐘記憶體快取。"""
    import time as _t
    if _index_cache["data"] and _t.time() - _index_cache["ts"] < ttl:
        return _index_cache["data"]
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
