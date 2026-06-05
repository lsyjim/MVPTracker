import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.institutional import summarize_iibs, theme_inst_ratio

SAMPLE = {"iibs": [
    {"inputDate": "2026-05-30", "foreignInvestorsBuySell": 1000, "investmentTrustBuySell": 200, "dealerBuySell": -100, "total": 1100},
    {"inputDate": "2026-05-29", "foreignInvestorsBuySell": 500, "investmentTrustBuySell": 50, "dealerBuySell": 0, "total": 550},
    {"inputDate": "2026-05-28", "foreignInvestorsBuySell": -30, "investmentTrustBuySell": 10, "dealerBuySell": 0, "total": -20},
]}


def test_summarize_latest_and_streak():
    s = summarize_iibs(SAMPLE)
    assert s["available"] is True
    assert s["total"] == 1100 and s["foreign_net"] == 1000 and s["trust_net"] == 200
    assert s["foreign_consecutive_days"] == 2   # 連 2 日外資買超
    assert s["total"] > 0
    # 5 日累計 = 全部 3 筆 total 相加（樣本不足 5 筆時取現有）
    assert s["total_5d"] == 1100 + 550 + (-20)
    assert s["date"] == "2026-05-30"
    # 各別 5 日累計
    assert s["foreign_5d"] == 1000 + 500 + (-30)
    assert s["trust_5d"] == 200 + 50 + 10
    assert s["dealer_5d"] == -100 + 0 + 0
    # 每日明細保留
    assert len(s["items"]) == 3
    assert s["items"][0] == {"date": "2026-05-30", "foreign": 1000, "trust": 200, "dealer": -100, "total": 1100}


def test_summarize_empty():
    assert summarize_iibs({"iibs": []})["available"] is False


def test_theme_ratio():
    nets = {"2330": 1100, "2317": -50, "2454": 300}
    assert theme_inst_ratio(nets) == 2 / 3   # 3 檔中 2 檔淨買
