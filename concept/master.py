# concept/master.py — 代號→名稱/產業 驗證與補名
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "analysis"))

_cache = {}


def lookup_name(code: str):
    if not code:
        return None
    code = code.strip()
    if code in _cache:
        return _cache[code]
    name = None
    # 1) twstock 母清單
    try:
        import twstock
        info = twstock.codes.get(code)
        if info and getattr(info, "name", None):
            name = info.name
    except Exception:
        pass
    # 2) WukongAPI 個股資訊 fallback
    if not name:
        try:
            from data_fetcher import WukongAPI
            r = WukongAPI.get_stock_info(code)
            if r and r.get("name"):
                name = r["name"]
        except Exception:
            pass
    _cache[code] = name
    return name


def in_master(code: str) -> bool:
    return lookup_name(code) is not None
