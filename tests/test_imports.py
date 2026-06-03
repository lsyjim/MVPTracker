import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "analysis"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_backend_imports():
    import analyzers, advanced_analyzers, decision_engine, data_fetcher, backtesting, database
    from quick_analyzer import QuickAnalyzer, YFinanceRateLimiter
    assert hasattr(QuickAnalyzer, "analyze_stock")
    from config import QuantConfig
    assert QuantConfig is not None
