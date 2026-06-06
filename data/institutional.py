# data/institutional.py — 個股 /iibs 三大法人 → chip_flow
import requests
import time
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
    """單位：張（API 已是張數，與 UI 一致，免換算）。total = 三大法人合計淨買超。
    依交易日去重（保留每日一筆）+ 由新到舊排序；_streak 跑在乾淨資料上。
    註：wukong /iibs 偶有缺漏交易日（來源資料不完整），連買天數為可得資料的保守下限。"""
    seen, items = set(), []
    for it in sorted(data.get("iibs", []), key=lambda x: x.get("inputDate", ""), reverse=True):
        d = it.get("inputDate")
        if d and d not in seen:
            seen.add(d)
            items.append(it)
    if not items:
        return {"available": False}
    latest = items[0]
    five = items[:5]
    total_5d = sum((it.get("total", 0) or 0) for it in five)             # 近 5 日合計（熱圖/聚合用，不動）
    foreign_5d = sum((it.get("foreignInvestorsBuySell", 0) or 0) for it in five)
    trust_5d = sum((it.get("investmentTrustBuySell", 0) or 0) for it in five)
    dealer_5d = sum((it.get("dealerBuySell", 0) or 0) for it in five)
    # 保留每日明細（供個股彈窗）：外資/投信/自營 各別買賣超（張）
    days = [{"date": it.get("inputDate", ""),
             "foreign": it.get("foreignInvestorsBuySell", 0) or 0,
             "trust": it.get("investmentTrustBuySell", 0) or 0,
             "dealer": it.get("dealerBuySell", 0) or 0,
             "total": it.get("total", 0) or 0} for it in items[:10]]
    return {
        "available": True,
        "foreign_net": latest.get("foreignInvestorsBuySell", 0) or 0,
        "trust_net": latest.get("investmentTrustBuySell", 0) or 0,
        "dealer_net": latest.get("dealerBuySell", 0) or 0,
        "total": latest.get("total", 0) or 0,           # 最新單日合計
        "total_5d": total_5d,                            # 近 5 日累計合計
        "foreign_5d": foreign_5d,                        # 近 5 日外資累計
        "trust_5d": trust_5d,                            # 近 5 日投信累計
        "dealer_5d": dealer_5d,                          # 近 5 日自營累計
        "foreign_consecutive_days": _streak(items, "foreignInvestorsBuySell"),
        "trust_consecutive_days": _streak(items, "investmentTrustBuySell"),
        "items": days,                                   # 每日明細（date/foreign/trust/dealer/total）
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


# ============================================================================
# 完整三大法人（TWSE T86，逐日、無缺漏）— 個股彈窗 on-demand 補爬
# wukong /iibs 會漏交易日；TWSE T86 是官方來源、逐日完整。單位：股 → ÷1000 為張。
# ============================================================================
T86_URL = "https://www.twse.com.tw/fund/T86"


def _streak_series(items, key):
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


def _t86_for_date(con, date_str):
    """TWSE T86 某交易日 → {code: [外, 投, 自, 合計]}（張）。歷史不變，永久快取、跨股共用。
    ★ 驗證回應 date/stat == 請求日（TWSE 在高頻/並發下會回錯日或殘缺資料）；不符就重試，仍不符不快取。"""
    if con is not None:
        payload, _ = cache.get(con, date_str, "t86")
        if payload is not None:
            return payload
    for attempt in range(3):
        time.sleep(0.3 * (attempt + 1))   # 輕節流：TWSE 對高頻請求會回錯日資料
        try:
            r = requests.get(T86_URL, params={"response": "json", "date": date_str, "selectType": "ALL"},
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
            d = r.json() or {}
            if d.get("stat") == "OK" and d.get("date") == date_str and d.get("data"):
                out = {}
                for row in d["data"]:
                    try:
                        out[row[0].strip()] = [
                            int(round(int(row[4].replace(",", "")) / 1000)),    # 外陸資買賣超
                            int(round(int(row[10].replace(",", "")) / 1000)),   # 投信買賣超
                            int(round(int(row[11].replace(",", "")) / 1000)),   # 自營商買賣超(合計)
                            int(round(int(row[18].replace(",", "")) / 1000))]   # 三大法人合計
                    except Exception:
                        continue
                if out:
                    if con is not None:
                        cache.put(con, date_str, "t86", out)
                    return out
        except Exception as e:
            print(f"[institutional] TWSE T86 {date_str} 試{attempt + 1}失敗: {e}")
        time.sleep(0.6 * (attempt + 1))
    return {}


def full_chip_flow(code, con=None, days=15):
    """個股完整三大法人（TWSE 逐日、無缺漏）：連續交易日 → 正確連買天數與每日明細。
    回傳格式同 summarize_iibs（多 source='TWSE'）。每股每日快取。"""
    if con is not None:
        payload, ts = cache.get(con, code, "iibs_full")
        if payload and cache.is_today(ts):
            return payload
    from data import fetcher
    rec = fetcher.recent_daily(code, days)               # 交易日曆（完整，含 wukong 缺的日）
    dates = [it["date"] for it in rec][::-1]             # 由新到舊
    # ★ TWSE T86 在『任何並發』下都會回錯資料（錯日資料卻標對日期）→ 只能逐日序列抓。
    # 每日結果跨股共用快取，所以只有當天第一個人慢；之後（任何股）瞬開。
    series = []
    for d in dates:
        v = _t86_for_date(con, d.replace("-", "")).get(code)
        if v:
            series.append({"date": d, "foreign": v[0], "trust": v[1], "dealer": v[2], "total": v[3]})
    # ★ 完整性把關：TWSE 在某些環境/日期會缺日或回錯日。series 必須覆蓋完整交易日曆才採用；
    #   不完整 → 回 unavailable（彈窗退回 wukong），並快取此判定避免反覆重爬。
    #   生產環境（真實日期）TWSE 完整 → 採用 → 連買/日期正確。
    have = {it["date"] for it in series}
    missing = [d for d in dates if d not in have]
    if missing or len(series) < 5:
        result = {"available": False, "incomplete": True, "missing": missing[:6]}
        if con is not None:
            cache.put(con, code, "iibs_full", result)
        return result
    five = series[:5]
    summary = {
        "available": True, "source": "TWSE",
        "foreign_net": series[0]["foreign"], "trust_net": series[0]["trust"], "dealer_net": series[0]["dealer"],
        "total": series[0]["total"],
        "total_5d": sum(it["total"] for it in five),
        "foreign_5d": sum(it["foreign"] for it in five),
        "trust_5d": sum(it["trust"] for it in five),
        "dealer_5d": sum(it["dealer"] for it in five),
        "foreign_consecutive_days": _streak_series(series, "foreign"),
        "trust_consecutive_days": _streak_series(series, "trust"),
        "items": series[:10],
        "date": series[0]["date"],
    }
    if con is not None:
        cache.put(con, code, "iibs_full", summary)
    return summary
