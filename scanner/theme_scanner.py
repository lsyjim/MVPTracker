# scanner/theme_scanner.py
import sys, os, statistics, threading
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "analysis"))

from concept import store
from data import institutional, cache
from ui import theme as uitheme

MAX_WORKERS = 10


@dataclass
class ThemeMetrics:
    theme_id: int
    key: str
    name: str
    momentum_5d: float      # 5日動能 %
    inst_net: float         # 法人買超強度（-100..100，正=買超）
    count: int              # 成分股數
    up_count: int = 0
    down_count: int = 0
    strong_ratio: float = 0.0
    signal: str = ""
    diverge: bool = False
    today_pct: float = 0.0  # 今日漲幅（成分股平均）
    pending: bool = False   # 進度顯示用：尚未掃完（畫成骨架/微光）


def is_diverge(m: float, inst: float) -> bool:
    return (m > 0 and inst < -20) or (m < 0 and inst > 20)


def mock_overview():
    """假資料（取自 mockup themes 陣列）。保留供 MVP_WEB demo 與離線測試。"""
    raw = [("先進封裝", 8.4, 70, 8), ("AI/伺服器", 6.7, 55, 16), ("機器人", 5.2, 25, 20), ("散熱", 4.1, 60, 6),
           ("半導體設備", 3.3, 20, 7), ("軟體", 2.9, -15, 30), ("光通訊", 2.6, 40, 10), ("IC設計", 1.6, 10, 6),
           ("記憶體", 1.1, -45, 9), ("連接器", 0.8, 5, 4), ("晶圓代工", 0.4, -20, 3), ("電源管理", -0.4, -10, 5),
           ("PCB", -1.2, -55, 16), ("低軌衛星", -1.8, 35, 9), ("被動元件", -3.1, -70, 10)]
    out = []
    for i, (n, m, inst, c) in enumerate(raw):
        out.append(ThemeMetrics(theme_id=i + 1, key=f"mock_{i}", name=n, momentum_5d=m,
                                inst_net=inst, count=c, diverge=is_diverge(m, inst),
                                today_pct=round(m * 0.4, 1)))
    return out


# ============================================================================
# 真資料：QuickAnalyzer 逐檔 + 題材聚合（每日快取）
# ============================================================================

def _signal_text(res):
    """從 QuickAnalyzer result 取得乾淨的訊號文字（供 grade_tag 分級與明細列顯示）。"""
    dm = res.get("decision_matrix", {}) or {}
    # SELL 情境 grade_tag 不一定抓得到「賣訊」字樣，直接給賣出語意確保綠色 badge
    if dm.get("scenario") == "SELL":
        return "賣出 " + (dm.get("recommendation") or "")
    rec = dm.get("recommendation")
    if rec:
        return rec
    overall = (res.get("recommendation") or {})
    if isinstance(overall, dict) and overall.get("overall"):
        return overall["overall"]
    return "觀察"


def analyze_stock_row(code, con=None, force=False):
    """單檔 → 明細列 dict。用 QuickAnalyzer.analyze_stock（grade/RS/recommendation）+ /iibs 法人。
    成功結果每日快取於 scan_cache(kind='row')；force=True 強制重算。
    失敗（取不到資料）回暫缺列但**不快取**，避免整天卡 0。"""
    if con is not None and not force:
        cached, ts = cache.get(con, code, "row")
        if cached and cache.is_today(ts):
            return cached
    from quick_analyzer import QuickAnalyzer
    chip = institutional.chip_flow(code, con)               # 法人只抓一次
    res = QuickAnalyzer.analyze_stock(code, "台股", scan_mode=True, chip=chip)  # 便宜資料 + 真實評級
    inst_ok = bool(chip.get("available"))
    inst_val = chip.get("total_5d") if inst_ok else None   # 5 日累計（與 5 日動能對齊）；暫缺為 None
    if not res:
        # 取不到資料：回暫缺列、不快取（下次會重試）
        return {"price": 0, "today_pct": 0, "d5_pct": 0, "rs": 50,
                "inst": inst_val, "inst_ok": inst_ok, "signal": "資料暫缺",
                "grade": "grade_C", "vol_ratio": 0, "bias": 0, "cons_buy": 0, "diverge": False,
                "_ok": False}
    rs = res.get("relative_strength", {}).get("rs_score", 50)
    d5 = res.get("relative_strength", {}).get("rs_5d", 0)
    vp = res.get("volume_price", {}) or {}
    ba = (res.get("mean_reversion", {}) or {}).get("bias_analysis", {}) or {}
    fc = chip.get("foreign_consecutive_days", 0) or 0
    tc = chip.get("trust_consecutive_days", 0) or 0
    sig = _signal_text(res)
    row = {
        "price": res.get("current_price", 0),
        "today_pct": res.get("price_change_pct", 0),
        "d5_pct": d5,
        "rs": round(rs),
        "inst": inst_val,
        "inst_ok": inst_ok,
        "signal": sig,
        # 今日精選股因子
        "grade": uitheme.grade_tag(sig) or "grade_C",
        "vol_ratio": round(vp.get("vol_ratio", 0) or 0, 2),     # 今日量 / 20日均量
        "bias": round(ba.get("bias_20", 0) or 0, 1),            # 20日乖離 %
        "cons_buy": max(fc if fc > 0 else 0, tc if tc > 0 else 0),  # 外資/投信連續買超天數
        "diverge": bool(inst_ok and inst_val is not None and ((d5 > 0 and inst_val < 0) or (d5 < 0 and inst_val > 0))),
        "_ok": True,
    }
    if con is not None:
        cache.put(con, code, "row", row)
    return row


def refresh_prices(rows):
    """盤中輕量刷新：只更新 price / today_pct（不重算 grade/RS）。
    收盤後報價的 change_pct 常為 0，會蓋掉當日收盤漲幅，故 cp 為 0 時不覆寫 today_pct。"""
    from data import fetcher
    for code, row in rows.items():
        try:
            q = fetcher.get_quote(code)
            if q and q.get("price"):
                row["price"] = q["price"]
                cp = q.get("change_pct")
                if cp:   # 僅在有非零漲跌時更新（避免盤後 0 蓋掉收盤值）
                    row["today_pct"] = cp
        except Exception:
            pass
    return rows


def aggregate_theme(rows):
    rows = [r for r in rows if r.get("_ok", True)]  # 只聚合取得到資料的成分股
    if not rows:
        return {"momentum_5d": 0, "today_pct": 0, "up_count": 0, "down_count": 0, "inst_ratio": 0,
                "inst_buy_count": 0, "inst_avail_count": 0, "strong_ratio": 0, "inst_net": 0}
    moms = [r["d5_pct"] for r in rows]
    # 法人維度只計入「有法人資料」的個股（暫缺者排除，避免被當 0 拉低占比）
    nets = {str(i): r["inst"] for i, r in enumerate(rows) if r.get("inst_ok", True) and r.get("inst") is not None}
    inst_ratio = institutional.theme_inst_ratio(nets)
    inst_net = round((inst_ratio * 2 - 1) * 100) if nets else 0   # 無法人資料 → 中性 0，不畫成賣超
    strong = sum(1 for r in rows if uitheme.grade_tag(r["signal"]) in ("grade_A", "grade_B"))
    return {
        "momentum_5d": statistics.mean(moms),
        "today_pct": statistics.mean([r["today_pct"] for r in rows]),
        "up_count": sum(1 for r in rows if r["today_pct"] > 0),
        "down_count": sum(1 for r in rows if r["today_pct"] < 0),
        "inst_ratio": inst_ratio,
        "inst_buy_count": sum(1 for v in nets.values() if v > 0),
        "inst_avail_count": len(nets),
        "strong_ratio": strong / len(rows),
        "inst_net": inst_net,  # -100..100 給熱圖
    }


def _all_codes(con, theme_id):
    cons = store.list_constituents(con, theme_id, None)
    for s in store.list_sub_themes(con, theme_id):
        cons += store.list_constituents(con, theme_id, s["id"])
    return cons


def _theme_metrics(t, count, rows_list):
    agg = aggregate_theme(rows_list)
    return ThemeMetrics(theme_id=t["id"], key=t["key"], name=t["name"],
                        momentum_5d=round(agg["momentum_5d"], 1), inst_net=agg["inst_net"],
                        count=count, up_count=agg["up_count"], down_count=agg["down_count"],
                        strong_ratio=agg["strong_ratio"],
                        diverge=is_diverge(agg["momentum_5d"], agg["inst_net"]),
                        today_pct=round(agg["today_pct"], 1))


def _scan_codes_parallel(con, codes, force=False, progress=None, max_workers=MAX_WORKERS):
    """平行分析一組代號（去重）→ {code: row}。progress(done,total) thread-safe。"""
    codes = list(dict.fromkeys(codes))   # 去重保序
    results, done, lock, total = {}, [0], threading.Lock(), len(codes)

    def work(code):
        row = analyze_stock_row(code, con, force=force)
        with lock:
            results[code] = row
            done[0] += 1
            if progress:
                progress(done[0], total)

    if codes:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            for f in [ex.submit(work, c) for c in codes]:
                try:
                    f.result()
                except Exception:
                    pass
    return results


def scan_theme(con, theme_id, force=False):
    """題材所有成分股平行分析 → {code: row} + 聚合。force=True 略過快取重算。"""
    cons = _all_codes(con, theme_id)
    res = _scan_codes_parallel(con, [c["code"] for c in cons], force=force)
    rows = {c["code"]: res[c["code"]] for c in cons if c["code"] in res}
    agg = aggregate_theme(list(rows.values()))
    return rows, agg


def all_constituent_codes(con):
    """全部成分股代號（去重），供進度/排行使用。"""
    seen, out = set(), []
    for t in store.list_themes(con):
        for c in _all_codes(con, t["id"]):
            if c["code"] not in seen:
                seen.add(c["code"]); out.append(c["code"])
    return out


def real_overview(con, force=False, progress=None):
    """平行掃描全部成分股 → 各題材 ThemeMetrics。progress(done,total)。"""
    themes = store.list_themes(con)
    theme_cons = {t["id"]: _all_codes(con, t["id"]) for t in themes}
    all_codes = [c["code"] for t in themes for c in theme_cons[t["id"]]]
    res = _scan_codes_parallel(con, all_codes, force=force, progress=progress)
    out = []
    for t in themes:
        cons = theme_cons[t["id"]]
        rows = [res[c["code"]] for c in cons if c["code"] in res]
        out.append(_theme_metrics(t, len(cons), rows))
    return out


def start_overview_scan(con, force=False, max_workers=MAX_WORKERS):
    """背景漸進掃描 session：呼叫端用 io_bound 跑 sess['run']；UI 輪詢共享狀態。
    sess['metrics'][theme_id] 於該題材全部成分股完成時填入 ThemeMetrics。"""
    themes = store.list_themes(con)
    theme_cons = {t["id"]: _all_codes(con, t["id"]) for t in themes}
    uniq = list(dict.fromkeys(c["code"] for t in themes for c in theme_cons[t["id"]]))
    sess = {"lock": threading.Lock(), "done": 0, "total": len(uniq),
            "metrics": {}, "finished": False, "themes": themes, "theme_cons": theme_cons}

    def run():
        results = {}
        lock = sess["lock"]

        def work(code):
            row = analyze_stock_row(code, con, force=force)
            with lock:
                results[code] = row
                sess["done"] += 1
                for t in themes:                         # 檢查哪些題材剛好全部完成
                    if t["id"] in sess["metrics"]:
                        continue
                    codes = [c["code"] for c in theme_cons[t["id"]]]
                    if all(cc in results for cc in codes):
                        rows = [results[cc] for cc in codes if results.get(cc)]
                        sess["metrics"][t["id"]] = _theme_metrics(t, len(codes), rows)

        if uniq:
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                for f in [ex.submit(work, c) for c in uniq]:
                    try:
                        f.result()
                    except Exception:
                        pass
        with lock:
            sess["finished"] = True

    sess["run"] = run
    return sess


def skeleton_metrics(con):
    """畫骨架用：每題材一個 pending ThemeMetrics（灰、分析中）。"""
    out = []
    for t in store.list_themes(con):
        out.append(ThemeMetrics(theme_id=t["id"], key=t["key"], name=t["name"],
                                 momentum_5d=0, inst_net=0, count=len(_all_codes(con, t["id"])),
                                 pending=True))
    return out
