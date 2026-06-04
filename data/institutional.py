# data/institutional.py — 個股 /iibs 三大法人 → chip_flow
import requests
from data import cache

IIBS_URL = "https://api.wukong.com.tw/stock/{code}/iibs"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json", "Referer": "https://wukong.com.tw/"}


def _streak(items, key):
    """連續同向天數：買超正、賣超負。items 已依日期新→舊排序。"""
    if not items:
        return 0
    first = items[0].get(key, 0) or 0
    if first == 0:
        return 0
    sign = 1 if first > 0 else -1
    n = 0
    for it in items:
        v = it.get(key, 0) or 0
        if (v > 0 and sign > 0) or (v < 0 and sign < 0):
            n += 1
        else:
            break
    return n * sign


def summarize_iibs(data: dict) -> dict:
    """單位：張（API 已是張數，與 UI 一致，免換算）。total = 三大法人合計淨買超。"""
    items = sorted(data.get("iibs", []), key=lambda x: x.get("inputDate", ""), reverse=True)
    if not items:
        return {"available": False}
    latest = items[0]
    total_5d = sum((it.get("total", 0) or 0) for it in items[:5])  # 近 5 個交易日累計（與 5 日動能對齊）
    return {
        "available": True,
        "foreign_net": latest.get("foreignInvestorsBuySell", 0) or 0,
        "trust_net": latest.get("investmentTrustBuySell", 0) or 0,
        "dealer_net": latest.get("dealerBuySell", 0) or 0,
        "total": latest.get("total", 0) or 0,           # 最新單日合計
        "total_5d": total_5d,                            # 近 5 日累計合計
        "foreign_consecutive_days": _streak(items, "foreignInvestorsBuySell"),
        "trust_consecutive_days": _streak(items, "investmentTrustBuySell"),
        "date": latest.get("inputDate", ""),             # 法人資料實際日期（盤後落後）
    }


def chip_flow(code, con=None):
    """逐檔取得個股三大法人 chip_flow（每日快取）。"""
    if con is not None:
        payload, ts = cache.get(con, code, "iibs")
        if payload and cache.is_today(ts):
            return payload
    try:
        resp = requests.get(IIBS_URL.format(code=code), headers=HEADERS, timeout=10)
        data = resp.json() if resp.status_code == 200 else {"iibs": []}
    except Exception as e:
        print(f"[institutional] {code} 取得失敗: {e}")
        data = {"iibs": []}
    summary = summarize_iibs(data)
    if con is not None and summary.get("available"):
        cache.put(con, code, "iibs", summary)
    return summary


def theme_inst_ratio(code_to_net: dict) -> float:
    """買超家數占比；傳入的 dict 應只含『有法人資料』的個股（暫缺者請先排除）。"""
    if not code_to_net:
        return 0.0
    return sum(1 for v in code_to_net.values() if v > 0) / len(code_to_net)


def latest_date(code="2330"):
    """取一檔參考股的法人資料實際日期（'YYYY-MM-DD'），給 header 顯示『截至 X』。"""
    try:
        cf = chip_flow(code)
        return cf.get("date") if cf.get("available") else None
    except Exception:
        return None


def intraday_force(code):
    """v2 預留：盤中主力力道（富邦 tick）。MVP 回 None。"""
    return None
