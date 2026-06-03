import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scanner.theme_scanner import ThemeMetrics, mock_overview


def test_mock_overview_shape():
    metrics = mock_overview()
    assert len(metrics) >= 5
    m = metrics[0]
    assert isinstance(m, ThemeMetrics)
    assert hasattr(m, "momentum_5d") and hasattr(m, "inst_net") and hasattr(m, "count")
    assert isinstance(m.diverge, bool)


def test_aggregate_theme():
    from scanner.theme_scanner import aggregate_theme
    rows = [
        {"d5_pct": 6.0, "today_pct": 1.0, "inst": 100, "signal": "可進場 A"},
        {"d5_pct": -2.0, "today_pct": -1.0, "inst": -50, "signal": "觀察"},
        {"d5_pct": 4.0, "today_pct": 0.5, "inst": 200, "signal": "建議買進 追蹤"},
    ]
    agg = aggregate_theme(rows)
    assert round(agg["momentum_5d"], 2) == round((6 - 2 + 4) / 3, 2)
    assert agg["up_count"] == 2 and agg["down_count"] == 1
    assert round(agg["inst_ratio"], 2) == round(2 / 3, 2)   # 2 檔淨買
    assert 0 <= agg["strong_ratio"] <= 1
