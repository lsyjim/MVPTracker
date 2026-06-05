# scanner/picks.py — 今日精選股（多因子交集 + 差一項候選）。對 scan 結果做篩選/排序，不另運算。
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "analysis"))
from config import PICKS
from concept import store
from data import cache

# 五因子（key, 達標說明）
MISS_LABEL = {
    "grade": "評級未達 A/B",
    "rs": "RS 未達 80",
    "inst": "法人買超不足",
    "vol": "量能未增",
    "bias": "乖離過大或背離",
}


def _checks(s, cfg):
    inst = s.get("inst")
    return {
        "grade": s.get("grade") in cfg["grades"],
        "rs": s.get("rs", 0) >= cfg["rs_min"],
        "inst": (inst is not None) and inst > cfg["inst_total_5d_min"] and s.get("cons_buy", 0) >= cfg["cons_buy_min"],
        "vol": s.get("vol_ratio", 0) >= cfg["vol_ratio_min"],
        "bias": abs(s.get("bias", 0)) <= cfg["bias_max"] and not s.get("diverge", False),
    }


def _score(s, cfg):
    inst = max(s.get("inst") or 0, 0)
    return (s.get("rs", 0) * cfg["w_rs"]
            + min(inst / 2000.0, 100) * cfg["w_inst"]
            + min(s.get("vol_ratio", 0) * 30, 100) * cfg["w_vol"]
            + max(s.get("d5_pct", 0), 0) * 4 * cfg["w_mom"])


def select(stocks, cfg=PICKS):
    """回 (full, near)：full=五項全中前 N；near=(stock, 缺項說明) 四項中。"""
    full, near = [], []
    for s in stocks:
        if not s.get("_ok"):
            continue
        chk = _checks(s, cfg)
        n = sum(chk.values())
        if n == 5:
            full.append(s)
        elif n == 4:
            miss = next((MISS_LABEL[k] for k, ok in chk.items() if not ok), "")
            near.append((s, miss))
    full.sort(key=lambda s: _score(s, cfg), reverse=True)
    near.sort(key=lambda x: _score(x[0], cfg), reverse=True)
    return full[: cfg["top_n"]], near


def tags(s):
    """因子標籤（顯示用）。"""
    out = [{"grade_A": "A主攻", "grade_B": "B追蹤", "grade_C": "觀察", "grade_sell": "賣出"}.get(s.get("grade"), "觀察"),
           f'RS {s.get("rs", 0)}']
    if s.get("cons_buy", 0) >= 2:
        out.append(f'法人連{s["cons_buy"]}買')
    if s.get("vol_ratio", 0) >= 1.2:
        out.append(f'量增{round((s["vol_ratio"] - 1) * 100)}%')
    out.append(f'乖離{s.get("bias", 0):.0f}%')
    return out


def gather(con):
    """從當日快取彙整個股（含因子）；同一檔多題材只取一次、標所屬題材。
    在 cache 鎖內讀取，避免與背景平行掃描共用連線時的併發衝突。"""
    seen = {}
    with cache._lock:
        for t in store.list_themes(con):
            cons = store.list_constituents(con, t["id"], None)
            for st in store.list_sub_themes(con, t["id"]):
                cons += store.list_constituents(con, t["id"], st["id"])
            for c in cons:
                if c["code"] in seen:
                    continue
                row, ts = cache.get(con, c["code"], "row")
                if row and cache.is_today(ts):
                    seen[c["code"]] = {**row, "code": c["code"], "name": c["name"],
                                       "in_master": c["in_master"], "theme": t["name"]}
    return list(seen.values())
