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
