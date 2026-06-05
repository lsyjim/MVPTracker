"""quick_analyzer.py — 從 StockGOGOV2/main.py 抽出的純分析引擎（無 GUI 耦合）。
複製，不改邏輯。提供 QuickAnalyzer.analyze_stock(symbol, market='台股') → result dict。"""
import datetime, threading, time, warnings, gc, sys, os
import yfinance as yf
import mplfinance as mpf
import pandas as pd
import numpy as np
from scipy.stats import percentileofscore
import twstock
import requests
from config import QuantConfig
from data_fetcher import RealtimePriceFetcher, WukongAPI, DataSourceManager, FubonMarketData
from analyzers import (DecisionMatrix, WaveAnalyzer, MeanReversionAnalyzer,
                       MarketRegimeAnalyzer, CorrelationAnalyzer,
                       calculate_sma, calculate_bollinger_bands, calculate_macd,
                       calculate_rsi, calculate_kd, analyze_volume_price_relation)
from backtesting import BacktestEngine
from database import WatchlistDatabase

from yf_rate_limiter import YFinanceRateLimiter


class QuickAnalyzer:
    """快速量化分析器 v4.0"""
    
    # 籌碼緩存資料庫實例（類別層級）
    _db = None

    # A2/B：大盤指數歷史快取（避免每檔重抓，RS 計算共用）
    # 結構：{ index_symbol: (date_str, DataFrame) }，同一天內有效
    _index_hist_cache = {}

    @classmethod
    def get_db(cls):
        if cls._db is None:
            cls._db = WatchlistDatabase()
        return cls._db

    @staticmethod
    def aligned_prev_close(df):
        """
        日期對齊的昨收價（主程式與分析報告共用，確保漲跌幅一致）。

        即時報價的「現價」是今日盤中價；昨收應為「前一交易日的收盤」。
        - 若 df 最後一根 K 棒的日期 >= 今天（df 已含今日盤中/收盤），昨收 = 倒數第二根。
        - 否則（df 最後一根是前一交易日），昨收 = 最後一根。
        這樣不論資料源是否已更新今日 K 棒，都能取到正確的昨收，
        避免用 iloc[-2] 在「df 未含今日」時誤差一天。
        """
        import datetime as _dt
        try:
            if df is None or 'Close' not in df:
                return None
            # NaN-safe：先濾掉空值收盤（避免今日空 K 棒/資料缺漏導致回傳 nan）
            closes = df['Close'].dropna()
            if len(closes) < 1:
                return None
            last_idx = closes.index[-1]
            last_date = last_idx.date() if hasattr(last_idx, 'date') else None
            today = _dt.date.today()
            if last_date is not None and last_date >= today and len(closes) > 1:
                return round(float(closes.iloc[-2]), 2)
            return round(float(closes.iloc[-1]), 2)
        except Exception:
            try:
                closes = df['Close'].dropna()
                return round(float(closes.iloc[-2]), 2) if len(closes) > 1 else round(float(closes.iloc[-1]), 2)
            except Exception:
                return None

    @staticmethod
    def _get_index_history_cached(index_symbol, period=None):
        """
        取得大盤指數歷史（含當日快取）。

        用 yf.Ticker(index_symbol).history()，與 beta 計算相同的可運作路徑。
        index_symbol 應為 yfinance 格式（例如 "^TWII" / "^GSPC"）。
        回傳 DataFrame（含 'Close'）或 None。
        """
        import datetime as _dt
        period = period or f"{QuantConfig.RISK_DATA_YEARS}y"
        today = _dt.date.today().isoformat()
        cache_key = f"{index_symbol}|{period}"
        cached = QuickAnalyzer._index_hist_cache.get(cache_key)
        if cached and cached[0] == today:
            return cached[1]
        try:
            idx = yf.Ticker(index_symbol)
            hist = idx.history(period=period)
            if hist is None or hist.empty:
                return None
            QuickAnalyzer._index_hist_cache[cache_key] = (today, hist)
            return hist
        except Exception as e:
            print(f"[RS] 大盤指數 {index_symbol} 取得失敗: {e}")
            return None

    @staticmethod
    def analyze_stock(symbol, market="台股", analysis_date=None, scan_mode=False, chip=None):
        """
        快速分析股票 - v4.3 增強版（整合即時與歷史分析）

        scan_mode=True（首頁/題材掃描）：只用便宜資料產出 ThreeLayerEngine 真實 A/B/C 評級；
          跳過 yfinance .info 基本面、多年 PE Band、多年風險/beta、4 個策略回測、verbose 建議。
          評級引擎只用 technical/RS/bias/volume/法人，與完整模式一致。
        chip：外部已抓好的法人 chip_flow（避免在此重複抓取）。

        v4.4.7 更新：加入 YFinance 速率限制處理
        
        Args:
            symbol: 股票代碼
            market: 市場（台股/美股）
            analysis_date: 分析日期 (datetime 物件)，None 表示今天
        
        Returns:
            dict: 分析結果
        """
        try:
            # ============================================================
            # v4.4.7 重構：統一數據源管理
            # 優先使用富邦 API，失敗才 fallback 到 yfinance
            # ============================================================
            
            # 檢查 yfinance 熔斷（作為最後防線）
            if not DataSourceManager.is_fubon_available() and YFinanceRateLimiter.is_circuit_breaker_active():
                remaining = YFinanceRateLimiter.get_circuit_breaker_remaining()
                print(f"⛔ [DataSource] 所有數據源不可用，{symbol} 分析跳過（yfinance 熔斷剩餘 {remaining} 秒）")
                return None
            
            # 取得股票名稱（優先使用 twstock）
            stock_name = symbol
            if market == "台股" and symbol.isdigit():
                try:
                    stock_name = f"{symbol} {twstock.codes[symbol].name}"
                except:
                    stock_name = symbol
            
            # 用於後續基本面分析的 yfinance ticker（可選）
            ticker_symbol = None
            stock = None
            if market == "台股":
                ticker_symbol = f"{symbol}.TW"
            else:
                ticker_symbol = symbol
            
            # 只有在需要時才創建 yfinance ticker（延遲初始化）
            def get_yf_ticker():
                nonlocal stock
                if stock is None:
                    stock = YFinanceRateLimiter.get_ticker_safe(ticker_symbol)
                return stock
            
            is_historical = analysis_date is not None
            
            # ============================================================
            # 數據獲取（v4.4.7 重構：優先使用富邦 API）
            # 優先順序：富邦 API → yfinance
            # ============================================================
            if is_historical:
                # 歷史模式：取得截至指定日期的數據
                end_date = analysis_date
                start_date = end_date - datetime.timedelta(days=250)
                
                # 優先使用 DataSourceManager（富邦 API → yfinance）
                hist = DataSourceManager.get_history(
                    symbol, market,
                    start_date=start_date,
                    end_date=end_date + datetime.timedelta(days=1)
                )
                
                if hist is None or hist.empty:
                    print(f"{symbol}: 無法獲取 {analysis_date.strftime('%Y-%m-%d')} 的歷史數據")
                    return None
                
                hist = hist.dropna()
                
                # 截取到分析日期（使用日期比較避免時區問題）
                target_date = analysis_date.date()
                mask = hist.index.date <= target_date
                hist = hist[mask]
                
                if hist.empty or len(hist) < 60:
                    print(f"{symbol}: 歷史數據不足（少於60天）")
                    return None
                
                actual_date = hist.index[-1].strftime('%Y-%m-%d')
                
                # 長期數據（截至分析日期）
                try:
                    long_start = end_date - datetime.timedelta(days=QuantConfig.RISK_DATA_YEARS * 365)
                    hist_long = DataSourceManager.get_history(
                        symbol, market,
                        start_date=long_start,
                        end_date=end_date + datetime.timedelta(days=1)
                    )
                    if hist_long is not None and not hist_long.empty:
                        hist_long = hist_long[hist_long.index.date <= target_date]
                    else:
                        hist_long = hist
                except:
                    hist_long = hist
            else:
                # 即時模式：取得最新數據
                # 優先使用 DataSourceManager（富邦 API → yfinance）
                hist = None
                for attempt, period in enumerate(["6mo", "3mo", "1y"]):
                    try:
                        hist = DataSourceManager.get_history(symbol, market, period=period)
                        if hist is not None and not hist.empty:
                            data_source = DataSourceManager.get_current_source()
                            print(f"[{symbol}] 數據來源：{data_source}，取得 {len(hist)} 筆")
                            break
                    except Exception as e:
                        print(f"{symbol}: 嘗試 {period} 失敗 - {e}")
                        continue
                
                if hist is None or hist.empty:
                    print(f"{symbol}: 無法獲取數據（請檢查網絡連接或稍後再試）")
                    return None
                
                hist = hist.dropna()
                if len(hist) < 60:
                    print(f"{symbol}: 數據不足（少於60天，僅有 {len(hist)} 天）")
                    return None
                
                actual_date = None
                if scan_mode:
                    hist_long = hist  # 掃描模式：不抓多年資料（評級不需要）
                else:
                    try:
                        hist_long = DataSourceManager.get_history(symbol, market, period=f"{QuantConfig.RISK_DATA_YEARS}y")
                    except:
                        hist_long = hist  # 如果長期數據獲取失敗，使用短期數據
            
            # 確保 hist_long 有效
            if hist_long is None or hist_long.empty:
                hist_long = hist
            
            # ============================================================
            # v4.4.7 更新：即時模式優先使用 DataSourceManager 取得即時股價
            # ============================================================
            realtime_price = None
            realtime_change = None
            realtime_change_pct = None
            realtime_prev_close = None
            price_source = 'unknown'

            if not is_historical and market == "台股":
                # 優先使用 DataSourceManager（會嘗試富邦 API）
                realtime_data = DataSourceManager.get_realtime_price(symbol, market)
                if realtime_data and realtime_data.get('price'):
                    realtime_price = realtime_data['price']
                    realtime_change = realtime_data.get('change', 0)
                    realtime_change_pct = realtime_data.get('change_pct', 0)
                    realtime_prev_close = realtime_data.get('prev_close')  # 同源昨收
                    price_source = realtime_data.get('source', 'unknown')
            
            # ============================================================
            # 分析計算（共用邏輯）
            # ============================================================
            
            # 技術指標
            technical = QuickAnalyzer._technical_analysis(hist)
            
            # 基本面分析（掃描模式跳過 yfinance .info / PE Band，用預設值；評級不依賴基本面）
            if scan_mode:
                fundamental = QuickAnalyzer._get_default_fundamental()
            else:
                fundamental = QuickAnalyzer._fundamental_analysis_v4(get_yf_ticker(), ticker_symbol, hist, is_historical)

            # 風險指標（掃描模式跳過多年風險/beta(.info)，用預設值；評級不依賴風險指標）
            if scan_mode:
                risk_metrics = QuickAnalyzer._get_default_risk_metrics()
            else:
                risk_metrics = QuickAnalyzer._calculate_risk_metrics_v4(hist_long, ticker_symbol, market)
            
            # 支撐壓力
            support_resistance = QuickAnalyzer._calculate_support_resistance(hist, technical)
            
            # 籌碼面分析（chip 由外部傳入時直接用，避免重複抓取法人）
            if chip is not None:
                chip_flow = chip
            elif is_historical:
                chip_flow = QuickAnalyzer._analyze_chip_flow_historical(symbol, market, analysis_date)
            else:
                chip_flow = QuickAnalyzer._analyze_chip_flow_cached(symbol, market)
            
            # 成交量分析
            volume_analysis = QuickAnalyzer._analyze_volume_spike(hist)
            
            # v4.4.1 新增：量價分析情境庫
            from analyzers import VolumePriceAnalyzer, RiskManager
            volume_price = VolumePriceAnalyzer.analyze(hist)
            
            # v4.4.1 新增：風險管理分析
            risk_manager = RiskManager.analyze(hist)
            
            # 市場環境（根據模式走不同分支）
            if is_historical:
                market_regime = MarketRegimeAnalyzer.get_market_regime_historical(market, analysis_date)
            else:
                market_regime = MarketRegimeAnalyzer.get_market_regime(market)
            
            # 波段分析（~1ms，純 CPU；保留以確保 scan 評級與完整報告/個股彈窗一致）
            wave_analysis = WaveAnalyzer.analyze_wave(hist)
            
            # 均值回歸分析
            mean_reversion = MeanReversionAnalyzer.analyze(hist)
            
            # ============================================================
            # 組裝結果（使用即時股價如果有的話）
            # ============================================================
            # 先取得昨收價（日期對齊；與主程式 K 線視窗共用同一邏輯確保一致）
            prev_close_hist = QuickAnalyzer.aligned_prev_close(hist)
            if prev_close_hist is None:
                prev_close_hist = round(hist['Close'].iloc[-1], 2)
            
            if realtime_price is not None:
                current_price = realtime_price
                # 修正錯位 bug：優先用即時報價「同源」的昨收（current 與 prev 來自
                # 同一來源同一時刻，數學上不可能超過漲跌停）。原本改用 hist 的
                # iloc[-2]，若 hist 最後一根不是今天就會差兩天，算出 +11.6% 假漲幅。
                if realtime_prev_close and realtime_prev_close > 0:
                    prev_close = realtime_prev_close
                    price_change = round(current_price - prev_close, 2)
                    price_change_pct = round((current_price / prev_close - 1) * 100, 2)
                else:
                    # 爬蟲未提供昨收 → 退回 hist 昨收（可能錯位，由 price_anomaly 防護）
                    prev_close = prev_close_hist
                    price_change = round(current_price - prev_close, 2)
                    price_change_pct = round((current_price / prev_close - 1) * 100, 2) if prev_close > 0 else 0
            else:
                current_price = round(hist['Close'].iloc[-1], 2)
                prev_close = prev_close_hist
                price_change = round(current_price - prev_close, 2)
                price_change_pct = round((current_price / prev_close - 1) * 100, 2) if prev_close > 0 else 0

            # 資料異常防護：台股有 ±10% 漲跌停限制，單日漲跌幅不可能超過約 10%。
            # 若超過，多半是即時報價與 hist 昨收來自不同日期/來源（資料未對齊），
            # 標記為可疑，避免使用者誤信（例如鴻海顯示 +11.6%）。
            price_anomaly = bool(market == "台股" and abs(price_change_pct) > 10.5)
            if price_anomaly:
                print(f"⚠️ [{symbol}] 漲跌幅異常 {price_change_pct:+.1f}%（超過±10%漲跌停），"
                      f"即時價 {current_price} 與昨收 {prev_close} 可能未對齊")

            result = {
                "symbol": symbol,
                "name": stock_name,  # v4.3 新增：股票名稱
                "current_price": current_price,
                "prev_close": prev_close,
                "price_change": price_change,
                "price_change_pct": price_change_pct,
                "price_anomaly": price_anomaly,  # 漲跌幅超過±10%漲跌停 → 資料可疑
                "price_source": price_source,  # v4.3 新增：標註價格來源
                "technical": technical,
                "fundamental": fundamental,
                "risk_metrics": risk_metrics,
                "support_resistance": support_resistance,
                "chip_flow": chip_flow,
                "volume_analysis": volume_analysis,
                "volume_price": volume_price,  # v4.4.1 新增：量價分析
                "risk_manager": risk_manager,  # v4.4.1 新增：風險管理
                "market_regime": market_regime,
                "wave_analysis": wave_analysis,
                "mean_reversion": mean_reversion,
                "recommendation": ""
            }
            
            # v4.4.6 形態分析（~10ms，純 CPU；保留：其頭部覆蓋會改變評級，
            # 略過會使 scan 與完整報告不一致，例如 3231 SELL→C）
            if QuantConfig.ENABLE_PATTERN_ANALYSIS:
                try:
                    from analyzers import PatternAnalyzer
                    pattern_analysis = PatternAnalyzer.analyze(
                        hist,
                        lookback=QuantConfig.PATTERN_LOOKBACK_DAYS
                    )
                    result["pattern_analysis"] = pattern_analysis
                except Exception as e:
                    print(f"形態分析錯誤: {e}")
                    result["pattern_analysis"] = {'available': False, 'message': str(e)}
            else:
                result["pattern_analysis"] = {'available': False, 'message': '形態分析已停用'}
            
            # === v4.5.19 相對強度 (RS) 計算 ===
            # A2 修正：原本用 DataSourceManager.get_history("TWII","美股") 取大盤，
            # 但 yfinance 需要 "^TWII"，去掉 "^" 後變成查無此代碼（404）→ RS 永遠
            # fallback 成 50/0，導致全系統 RS 因子（含 L1 ±12、L2 動能模式）失效。
            # 改用與 beta 計算相同、可正常運作的 yf.Ticker(MARKET_INDEX) 路徑，並加快取。
            try:
                market_symbol = QuantConfig.MARKET_INDEX_TW if market == "台股" else QuantConfig.MARKET_INDEX_US
                market_hist = QuickAnalyzer._get_index_history_cached(market_symbol)

                if market_hist is not None and len(market_hist) > 20:
                    # 計算 5/20/60 日相對表現
                    stock_ret_5d = (hist['Close'].iloc[-1] / hist['Close'].iloc[-5] - 1) * 100 if len(hist) > 5 else 0
                    stock_ret_20d = (hist['Close'].iloc[-1] / hist['Close'].iloc[-20] - 1) * 100 if len(hist) > 20 else 0
                    
                    market_ret_5d = (market_hist['Close'].iloc[-1] / market_hist['Close'].iloc[-5] - 1) * 100 if len(market_hist) > 5 else 0
                    market_ret_20d = (market_hist['Close'].iloc[-1] / market_hist['Close'].iloc[-20] - 1) * 100 if len(market_hist) > 20 else 0
                    
                    rs_5d = stock_ret_5d - market_ret_5d
                    rs_20d = stock_ret_20d - market_ret_20d
                    
                    # 加權 RS 分數 (5日60% + 20日40%)
                    rs_score = rs_5d * 0.6 + rs_20d * 0.4
                    
                    # 標準化到 0-100
                    normalized_rs = max(0, min(100, 50 + rs_score * 5))
                    
                    result["relative_strength"] = {
                        'rs_score': round(normalized_rs, 1),
                        'vs_market': round(rs_score, 2),
                        'rs_5d': round(rs_5d, 2),
                        'rs_20d': round(rs_20d, 2)
                    }
                else:
                    result["relative_strength"] = {'rs_score': 50, 'vs_market': 0}
            except Exception as rs_err:
                print(f"[RS] 相對強度計算錯誤: {rs_err}")
                result["relative_strength"] = {'rs_score': 50, 'vs_market': 0}
            
            # === v4.5.19 新增：trend 結構 (用於評分系統) ===
            result["trend"] = {
                'primary_trend': technical.get('trend', '盤整'),
                'ma20_slope': technical.get('ma20_slope', 0)
            }
            
            # 歷史模式額外欄位
            if is_historical:
                result["is_historical"] = True
                result["analysis_date"] = actual_date
                result["requested_date"] = analysis_date.strftime('%Y-%m-%d')
            
            # 決策矩陣
            decision_matrix = DecisionMatrix.analyze(result)
            result["decision_matrix"] = decision_matrix
            
            # 生成建議 + 策略（掃描模式跳過：完整報告才需要；A/B/C 評級已在 decision_matrix）
            if scan_mode:
                result["recommendation"] = ""
                result["strategies"], result["best_strategy"] = [], None
            else:
                result["recommendation"] = QuickAnalyzer._generate_recommendation_v43(result, decision_matrix)
                result["strategies"], result["best_strategy"] = QuickAnalyzer.analyze_strategies_v4(
                    hist, technical, fundamental, market_regime
                )
            
            result["data_time"] = hist.index[-1].strftime('%Y-%m-%d %H:%M:%S')
            
            # 歷史模式：計算未來驗證數據
            if is_historical:
                result["future_validation"] = QuickAnalyzer._calculate_future_validation(
                    stock, analysis_date, hist['Close'].iloc[-1]
                )
            
            return result
            
        except Exception as e:
            print(f"分析錯誤 {symbol}: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    @staticmethod
    def analyze_stock_historical(symbol, market="台股", analysis_date=None):
        """
        歷史日期分析（向後兼容，實際調用 analyze_stock）
        """
        return QuickAnalyzer.analyze_stock(symbol, market, analysis_date)
    
    @staticmethod
    def _fundamental_analysis_v4(stock, ticker_symbol, hist=None, is_historical=False):
        """
        v4.3 改進：基本面分析（整合即時與歷史模式）
        v4.4.7 更新：使用 YFinanceRateLimiter.get_info_safe 避免速率限制
        
        即時模式：使用 Forward PE + PE Band
        歷史模式：Forward PE 不可用，僅使用 Trailing PE
        """
        try:
            # 如果 stock 為 None，嘗試創建
            if stock is None:
                stock = YFinanceRateLimiter.get_ticker_safe(ticker_symbol)
            
            # 如果還是 None，返回預設值
            if stock is None:
                return QuickAnalyzer._get_default_fundamental()
            
            # 使用安全的 info 取得方法（帶快取和熔斷）
            info = YFinanceRateLimiter.get_info_safe(stock)
            
            # 取得基本數據
            trailing_pe = info.get("trailingPE", None)
            pb = info.get("priceToBook", None)
            sector = info.get("sector", "Unknown")
            industry = info.get("industry", "Unknown")
            
            # 取得 EPS 數據
            trailing_eps = info.get("trailingEps", None)
            forward_eps = info.get("forwardEps", None)
            
            # 取得殖利率
            dividend_yield = info.get("dividendYield", None)
            
            # Forward PE（僅即時模式可用）
            if is_historical:
                forward_pe = None
            else:
                forward_pe = info.get("forwardPE", None)
            
            # PE Band 計算
            pe_percentile = None
            pe_band_signal = "中性"
            
            if trailing_pe is not None:
                try:
                    # 即時模式：使用 5 年歷史數據
                    # 歷史模式：使用傳入的 hist 數據
                    if is_historical and hist is not None and len(hist) > 60:
                        hist_for_pe = hist
                    else:
                        hist_for_pe = YFinanceRateLimiter.get_history(stock, period="5y")
                    
                    if hist_for_pe is not None and len(hist_for_pe) > 252:  # 至少一年數據
                        current_price = hist_for_pe['Close'].iloc[-1]
                        implied_eps = current_price / trailing_pe if trailing_pe > 0 else 1
                        
                        historical_pe = hist_for_pe['Close'] / implied_eps
                        pe_percentile = percentileofscore(historical_pe.dropna(), trailing_pe)
                        
                        if pe_percentile < 20:
                            pe_band_signal = "歷史低檔（偏多）"
                        elif pe_percentile > 80:
                            pe_band_signal = "歷史高檔（偏空）"
                        else:
                            pe_band_signal = f"歷史 {pe_percentile:.0f}% 位置（中性）"
                except Exception as e:
                    print(f"PE Band 計算錯誤: {e}")
            
            # 綜合評級
            signal = "中性"
            signal_reason = []
            
            # Forward PE 判斷（即時模式優先使用）
            if forward_pe is not None and not is_historical:
                if forward_pe < 12:
                    signal = "偏多"
                    signal_reason.append(f"預估PE={forward_pe:.1f}偏低")
                elif forward_pe > 25:
                    signal = "偏空"
                    signal_reason.append(f"預估PE={forward_pe:.1f}偏高")
            elif trailing_pe is not None:
                # 歷史模式或無 Forward PE 時使用 Trailing PE
                if trailing_pe < 12:
                    signal = "偏多"
                    signal_reason.append(f"本益比={trailing_pe:.1f}偏低")
                elif trailing_pe > 25:
                    signal = "偏空"
                    signal_reason.append(f"本益比={trailing_pe:.1f}偏高")
            
            # PE Band 調整
            if pe_percentile is not None:
                if pe_percentile < 20:
                    if signal != "偏多":
                        signal = "偏多"
                    signal_reason.append("PE處於歷史低檔")
                elif pe_percentile > 80:
                    if signal != "偏空":
                        signal = "偏空"
                    signal_reason.append("PE處於歷史高檔")
            
            return {
                "trailing_pe": round(trailing_pe, 2) if trailing_pe else "N/A",
                "forward_pe": "歷史模式不可用" if is_historical else (round(forward_pe, 2) if forward_pe else "N/A"),
                "pb": round(pb, 2) if pb else "N/A",
                "eps": round(trailing_eps, 2) if trailing_eps else "N/A",
                "forward_eps": round(forward_eps, 2) if forward_eps else "N/A",
                "dividend_yield": round(dividend_yield, 4) if dividend_yield else "N/A",
                "sector": sector,
                "industry": industry,
                "pe_percentile": round(pe_percentile, 1) if pe_percentile else "N/A",
                "pe_band_signal": pe_band_signal,
                "signal": signal,
                "signal_reason": "；".join(signal_reason) if signal_reason else "數據有限",
                "is_historical": is_historical
            }
            
        except Exception as e:
            print(f"基本面分析錯誤: {e}")
            return {
                "trailing_pe": "N/A",
                "forward_pe": "歷史模式不可用" if is_historical else "N/A",
                "pb": "N/A",
                "eps": "N/A",
                "forward_eps": "N/A",
                "dividend_yield": "N/A",
                "sector": "Unknown",
                "industry": "Unknown",
                "pe_percentile": "N/A",
                "pe_band_signal": "無法判斷",
                "signal": "中性",
                "signal_reason": "數據有限",
                "is_historical": is_historical
            }
    
    @staticmethod
    def _analyze_chip_flow_historical(symbol, market, analysis_date):
        """嘗試取得歷史籌碼數據"""
        try:
            if market != "台股":
                return {
                    "available": False,
                    "message": "歷史籌碼僅支援台股"
                }
            
            # 嘗試查詢證交所歷史數據
            date_str = analysis_date.strftime('%Y%m%d')
            url = "https://www.twse.com.tw/fund/T86"
            params = {
                'response': 'json',
                'date': date_str,
                'selectType': 'ALL'
            }
            
            r = requests.get(url, params=params, timeout=10)
            data = r.json()
            
            if 'data' not in data or not data['data']:
                return {
                    "available": False,
                    "message": f"{analysis_date.strftime('%Y-%m-%d')} 無籌碼資料（可能為非交易日）"
                }
            
            for row in data['data']:
                if row[0] == symbol:
                    foreign_investor = int(row[4].replace(',', ''))
                    investment_trust = int(row[10].replace(',', ''))
                    
                    # 判斷籌碼狀態
                    if foreign_investor > 0 and investment_trust > 0:
                        signal = "籌碼偏多"
                    elif foreign_investor < 0 and investment_trust < 0:
                        signal = "籌碼偏空"
                    else:
                        signal = "籌碼中性"
                    
                    return {
                        "available": True,
                        "foreign": f"{'買超' if foreign_investor > 0 else '賣超'} {abs(foreign_investor):,} 張",
                        "trust": f"{'買超' if investment_trust > 0 else '賣超'} {abs(investment_trust):,} 張",
                        "dealer": "歷史模式",
                        "foreign_continuous": "歷史單日",
                        "trust_continuous": "歷史單日",
                        "signal": signal,
                        "signal_color": "positive" if signal == "籌碼偏多" else "negative" if signal == "籌碼偏空" else "neutral",
                        "message": f"📅 歷史籌碼 ({analysis_date.strftime('%Y-%m-%d')})",
                        "is_historical": True
                    }
            
            return {
                "available": False,
                "message": f"找不到 {symbol} 在 {analysis_date.strftime('%Y-%m-%d')} 的籌碼資料"
            }
            
        except Exception as e:
            return {
                "available": False,
                "message": f"歷史籌碼查詢失敗: {str(e)}"
            }
    
    @staticmethod
    def _calculate_future_validation(stock, analysis_date, analysis_price):
        """
        計算分析日期之後的實際走勢（用於驗證策略準確度）
        v4.4.7 更新：使用 YFinanceRateLimiter
        """
        try:
            # 取得分析日期之後的數據
            future_start = analysis_date + datetime.timedelta(days=1)
            future_end = datetime.datetime.now()
            
            if future_start >= future_end:
                return {
                    "available": False,
                    "message": "分析日期之後尚無數據"
                }
            
            future_hist = YFinanceRateLimiter.get_history(
                stock,
                start=future_start.strftime('%Y-%m-%d'),
                end=future_end.strftime('%Y-%m-%d')
            )
            
            if future_hist is None or future_hist.empty or len(future_hist) < 1:
                return {
                    "available": False,
                    "message": "無法取得後續數據"
                }
            
            # 計算各時間段的漲跌幅
            validation = {
                "available": True,
                "analysis_price": round(analysis_price, 2)
            }
            
            # 5天後
            if len(future_hist) >= 5:
                price_5d = future_hist['Close'].iloc[4]
                change_5d = (price_5d / analysis_price - 1) * 100
                validation["5d_price"] = round(price_5d, 2)
                validation["5d_change"] = round(change_5d, 2)
            
            # 10天後
            if len(future_hist) >= 10:
                price_10d = future_hist['Close'].iloc[9]
                change_10d = (price_10d / analysis_price - 1) * 100
                validation["10d_price"] = round(price_10d, 2)
                validation["10d_change"] = round(change_10d, 2)
            
            # 20天後
            if len(future_hist) >= 20:
                price_20d = future_hist['Close'].iloc[19]
                change_20d = (price_20d / analysis_price - 1) * 100
                validation["20d_price"] = round(price_20d, 2)
                validation["20d_change"] = round(change_20d, 2)
            
            # 最高價和最低價
            validation["max_price"] = round(future_hist['High'].max(), 2)
            validation["max_change"] = round((future_hist['High'].max() / analysis_price - 1) * 100, 2)
            validation["min_price"] = round(future_hist['Low'].min(), 2)
            validation["min_change"] = round((future_hist['Low'].min() / analysis_price - 1) * 100, 2)
            
            # 當前價格
            validation["current_price"] = round(future_hist['Close'].iloc[-1], 2)
            validation["current_change"] = round((future_hist['Close'].iloc[-1] / analysis_price - 1) * 100, 2)
            validation["days_elapsed"] = len(future_hist)
            
            return validation
            
        except Exception as e:
            print(f"未來驗證計算錯誤: {e}")
            return {
                "available": False,
                "message": f"計算錯誤: {str(e)}"
            }
        """v4.0 改進：基本面分析（PE Band + Forward PE）"""
        try:
            info = stock.info
            
            # 取得當前 PE 和預估 PE
            trailing_pe = info.get("trailingPE", None)
            forward_pe = info.get("forwardPE", None)
            pb = info.get("priceToBook", None)
            sector = info.get("sector", "Unknown")
            industry = info.get("industry", "Unknown")
            
            # v4.0 新增：計算 PE Band（歷史百分位）
            pe_percentile = None
            pe_band_signal = "中性"
            
            if trailing_pe is not None:
                try:
                    # 嘗試獲取歷史 PE 數據（透過歷史價格和 EPS 估算）
                    hist_5y = stock.history(period="5y")
                    if len(hist_5y) > 252:  # 至少一年數據
                        # 簡化計算：假設近期 EPS 穩定，用價格變動估算 PE 分布
                        # 實際應用中應使用真實的歷史 EPS 數據
                        current_price = hist_5y['Close'].iloc[-1]
                        implied_eps = current_price / trailing_pe if trailing_pe > 0 else 1
                        
                        # 計算歷史 PE 分布
                        historical_pe = hist_5y['Close'] / implied_eps
                        pe_percentile = percentileofscore(historical_pe.dropna(), trailing_pe)
                        
                        if pe_percentile < 20:
                            pe_band_signal = "歷史低檔（偏多）"
                        elif pe_percentile > 80:
                            pe_band_signal = "歷史高檔（偏空）"
                        else:
                            pe_band_signal = f"歷史 {pe_percentile:.0f}% 位置（中性）"
                except Exception as e:
                    print(f"PE Band 計算錯誤: {e}")
            
            # 綜合評級（v4.0改進：考慮 Forward PE 和 PE Band）
            signal = "中性"
            signal_reason = []
            
            # v4.4.2 修正：檢查 PE 是否為負值（公司虧損）
            pe_is_negative = False
            if forward_pe is not None and forward_pe < 0:
                pe_is_negative = True
                signal_reason.append(f"公司虧損(預估PE={forward_pe:.1f})")
            elif trailing_pe is not None and trailing_pe < 0:
                pe_is_negative = True
                signal_reason.append(f"公司虧損(當前PE={trailing_pe:.1f})")
            
            # PE 為負值時，改用 PB 判斷
            if pe_is_negative:
                if pb is not None and pb > 0:
                    if pb < 1.0:
                        signal = "中性"
                        signal_reason.append(f"PB={pb:.2f}<1（低於淨值）")
                    elif pb > 3.0:
                        signal = "偏空"
                        signal_reason.append(f"PB={pb:.2f}偏高")
                    else:
                        signal = "中性"
                        signal_reason.append(f"PB={pb:.2f}正常")
                else:
                    signal = "中性"
                    signal_reason.append("PE無效，需觀察獲利改善")
            else:
                # Forward PE 判斷（市場交易的是未來）- 必須是正數
                if forward_pe is not None and forward_pe > 0:
                    if forward_pe < 12:
                        signal = "偏多"
                        signal_reason.append(f"預估PE={forward_pe:.1f}偏低")
                    elif forward_pe > 25:
                        signal = "偏空"
                        signal_reason.append(f"預估PE={forward_pe:.1f}偏高")
                
                # PE Band 調整
                if pe_percentile is not None:
                    if pe_percentile < 20:
                        if signal != "偏多":
                            signal = "偏多"
                        signal_reason.append("PE處於歷史低檔")
                    elif pe_percentile > 80:
                        if signal != "偏空":
                            signal = "偏空"
                        signal_reason.append("PE處於歷史高檔")
                
                # 如果沒有 Forward PE，使用 Trailing PE（但降低權重）- 必須是正數
                if forward_pe is None and trailing_pe is not None and trailing_pe > 0:
                    if trailing_pe < 15:
                        signal = "偏多" if signal == "中性" else signal
                        signal_reason.append(f"當前PE={trailing_pe:.1f}偏低(參考)")
                    elif trailing_pe > 30:
                        signal = "偏空" if signal == "中性" else signal
                        signal_reason.append(f"當前PE={trailing_pe:.1f}偏高(參考)")
            
            return {
                "signal": signal,
                "signal_reason": "，".join(signal_reason) if signal_reason else "無特別訊號",
                "trailing_pe": trailing_pe if trailing_pe else "N/A",
                "forward_pe": forward_pe if forward_pe else "N/A",
                "pe_percentile": round(pe_percentile, 1) if pe_percentile else "N/A",
                "pe_band_signal": pe_band_signal,
                "pb": pb if pb else "N/A",
                "sector": sector,
                "industry": industry
            }
        except Exception as e:
            print(f"基本面分析錯誤: {e}")
            return {
                "signal": "中性", 
                "signal_reason": "資料不足",
                "trailing_pe": "N/A", 
                "forward_pe": "N/A",
                "pe_percentile": "N/A",
                "pe_band_signal": "N/A",
                "pb": "N/A",
                "sector": "Unknown",
                "industry": "Unknown"
            }
    
    @staticmethod
    def _calculate_risk_metrics_v4(hist_long, ticker_symbol, market="台股"):
        """v4.0 改進：使用長期數據計算風險指標 + Beta 係數"""
        try:
            if hist_long.empty or len(hist_long) < 60:
                return QuickAnalyzer._get_default_risk_metrics()
            
            daily_returns = hist_long['Close'].pct_change(fill_method=None).dropna()
            
            # 年化波動率
            volatility = daily_returns.std() * np.sqrt(252) * 100
            
            # v4.0 改進：使用長期數據計算 VaR
            var_95 = np.percentile(daily_returns, 5) * 100
            var_99 = np.percentile(daily_returns, 1) * 100  # 新增 99% VaR
            
            # 最大回撤
            cumulative = (1 + daily_returns).cumprod()
            running_max = cumulative.expanding().max()
            drawdown = (cumulative - running_max) / running_max
            max_drawdown = drawdown.min() * 100
            
            # v4.0 新增：Beta 係數計算
            beta = QuickAnalyzer._calculate_beta(daily_returns, market)
            
            # 波動率分級
            if volatility < 20:
                vol_level = "低波動"
            elif volatility < 40:
                vol_level = "中波動"
            else:
                vol_level = "高波動"
            
            # v4.0 新增：Beta 分類
            if beta is not None:
                if beta < 0.8:
                    beta_type = "防禦型（低Beta）"
                elif beta > 1.2:
                    beta_type = "攻擊型（高Beta）"
                else:
                    beta_type = "中性型"
            else:
                beta_type = "N/A"
            
            return {
                "volatility": round(volatility, 2),
                "vol_level": vol_level,
                "var_95": round(var_95, 2),
                "var_99": round(var_99, 2),  # v4.0 新增
                "max_drawdown": round(max_drawdown, 2),
                "beta": round(beta, 2) if beta else "N/A",  # v4.0 新增
                "beta_type": beta_type,  # v4.0 新增
                "data_period": f"{len(hist_long)}天 ({QuantConfig.RISK_DATA_YEARS}年)"  # v4.0 新增
            }
        except Exception as e:
            print(f"風險指標計算錯誤: {e}")
            return QuickAnalyzer._get_default_risk_metrics()
    
    @staticmethod
    def _get_default_risk_metrics():
        """返回預設風險指標"""
        return {
            "volatility": 0,
            "vol_level": "未知",
            "var_95": 0,
            "var_99": 0,
            "max_drawdown": 0,
            "beta": "N/A",
            "beta_type": "N/A",
            "data_period": "N/A"
        }
    
    @staticmethod
    def _get_default_fundamental():
        """返回預設基本面數據（當無法取得時使用）"""
        return {
            "signal": "中性",
            "signal_reason": "資料不足",
            "trailing_pe": "N/A",
            "forward_pe": "N/A",
            "pe_percentile": "N/A",
            "pe_band_signal": "N/A",
            "pb": "N/A",
            "eps": "N/A",
            "forward_eps": "N/A",
            "dividend_yield": "N/A",
            "sector": "Unknown",
            "industry": "Unknown"
        }
    
    @staticmethod
    def _calculate_beta(stock_returns, market="台股"):
        """v4.0 新增：計算 Beta 係數"""
        try:
            # 取得大盤數據
            if market == "台股":
                index_symbol = QuantConfig.MARKET_INDEX_TW
            else:
                index_symbol = QuantConfig.MARKET_INDEX_US
            
            # B2 #4：改用當日快取的大盤指數（原本每檔都重抓 2y，純浪費網路）
            index_hist = QuickAnalyzer._get_index_history_cached(
                index_symbol, period=f"{QuantConfig.RISK_DATA_YEARS}y"
            )

            if index_hist is None or index_hist.empty:
                return None
            
            index_returns = index_hist['Close'].pct_change(fill_method=None).dropna()
            
            # 對齊日期
            common_dates = stock_returns.index.intersection(index_returns.index)
            if len(common_dates) < 60:
                return None
            
            stock_aligned = stock_returns.loc[common_dates]
            index_aligned = index_returns.loc[common_dates]
            
            # 計算協方差和變異數
            covariance = stock_aligned.cov(index_aligned)
            market_variance = index_aligned.var()
            
            if market_variance > 0:
                beta = covariance / market_variance
                return beta
            return None
            
        except Exception as e:
            print(f"Beta 計算錯誤: {e}")
            return None
    
    @staticmethod
    def _analyze_volume_spike(hist):
        """v4.0 新增：成交量異常偵測"""
        try:
            if len(hist) < QuantConfig.VOLUME_MA_PERIOD + 1:
                return {"spike_detected": False, "message": "資料不足"}
            
            # 計算成交量移動平均
            volume_ma = hist['Volume'].rolling(window=QuantConfig.VOLUME_MA_PERIOD).mean()
            current_volume = hist['Volume'].iloc[-1]
            avg_volume = volume_ma.iloc[-1]
            
            volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1
            
            # 判斷是否爆量
            spike_detected = volume_ratio >= QuantConfig.VOLUME_SPIKE_THRESHOLD
            
            # 分析爆量的意義
            price_change = (hist['Close'].iloc[-1] - hist['Close'].iloc[-2]) / hist['Close'].iloc[-2] * 100
            
            if spike_detected:
                if price_change > 1:
                    spike_signal = "爆量上漲（可能是突破訊號）"
                    spike_action = "偏多"
                elif price_change < -1:
                    spike_signal = "爆量下跌（可能是恐慌賣壓）"
                    spike_action = "偏空"
                else:
                    spike_signal = "爆量震盪（可能是換手）"
                    spike_action = "中性"
            else:
                spike_signal = "成交量正常"
                spike_action = "中性"
            
            # 近5日成交量趨勢
            recent_volumes = hist['Volume'].tail(5)
            volume_trend = "放大" if recent_volumes.iloc[-1] > recent_volumes.iloc[0] else "縮小"
            
            return {
                "spike_detected": spike_detected,
                "volume_ratio": round(volume_ratio, 2),
                "current_volume": int(current_volume),
                "avg_volume": int(avg_volume),
                "spike_signal": spike_signal,
                "spike_action": spike_action,
                "volume_trend": volume_trend,
                "price_change": round(price_change, 2)
            }
        except Exception as e:
            print(f"成交量分析錯誤: {e}")
            return {"spike_detected": False, "message": f"分析錯誤: {e}"}
    
    @staticmethod
    def _analyze_chip_flow_cached(symbol, market="台股"):
        """v4.4.1 改進：籌碼面分析（優先使用悟空 API）"""
        if market != "台股":
            return {
                "available": False,
                "message": "籌碼面分析僅適用於台股"
            }
        
        try:
            # v4.4.1：優先嘗試悟空 API
            wukong_result = QuickAnalyzer._analyze_chip_flow_wukong(symbol)
            if wukong_result and wukong_result.get('available'):
                return wukong_result
            
            # 悟空 API 失敗，嘗試原有的 TWSE 方法
            db = QuickAnalyzer.get_db()
            today = datetime.datetime.now()
            
            # 嘗試從緩存讀取
            records = []
            for i in range(10):  # 嘗試過去10天
                check_date = today - datetime.timedelta(days=i)
                date_str = check_date.strftime('%Y%m%d')
                
                # 先檢查緩存
                cached = db.get_cached_chip_data(symbol, date_str)
                if cached:
                    records.append({
                        'date': date_str,
                        'foreign_investor': cached['foreign_investor'],
                        'investment_trust': cached['investment_trust']
                    })
                else:
                    # 緩存沒有，嘗試抓取
                    rec = QuickAnalyzer._crawl_invest(check_date, symbol)
                    if rec:
                        # 存入緩存
                        db.save_chip_cache(
                            symbol, date_str,
                            rec['foreign_investor'],
                            rec['investment_trust']
                        )
                        records.append(rec)
                
                if len(records) >= 3:
                    break
                
                # 避免請求過快
                if not cached:
                    time.sleep(0.3)
            
            if len(records) < 2:
                # 最後嘗試悟空 API 的備用方案
                return QuickAnalyzer._analyze_chip_flow_wukong(symbol) or {
                    "available": False,
                    "message": "無法取得籌碼資料"
                }
            
            # 分析籌碼數據
            df = pd.DataFrame(records)
            df['date_dt'] = pd.to_datetime(df['date'], format="%Y%m%d")
            df.sort_values('date_dt', inplace=True)
            
            last_two = df.tail(2)
            fi_vals = last_two['foreign_investor'].values
            it_vals = last_two['investment_trust'].values
            
            # 外資判斷（v4.4.2 修正：計算連續天數）
            foreign_consecutive_days = 0
            if all(fi > 0 for fi in fi_vals):
                foreign_continuous = "連續買超"
                foreign_signal = "偏多"
                foreign_consecutive_days = 2  # 至少2天
            elif all(fi < 0 for fi in fi_vals):
                foreign_continuous = "連續賣超"
                foreign_signal = "偏空"
                foreign_consecutive_days = -2  # 負值表示賣超
            elif fi_vals[-1] > 0:
                foreign_continuous = "買超"
                foreign_signal = "中性偏多"
                foreign_consecutive_days = 1
            elif fi_vals[-1] < 0:
                foreign_continuous = "賣超"
                foreign_signal = "中性偏空"
                foreign_consecutive_days = -1
            else:
                foreign_continuous = "觀望"
                foreign_signal = "中性"
                foreign_consecutive_days = 0
            
            # 投信判斷（v4.4.2 修正：計算連續天數）
            trust_consecutive_days = 0
            if all(it > 0 for it in it_vals):
                trust_continuous = "連續買超"
                trust_signal = "偏多"
                trust_consecutive_days = 2
            elif all(it < 0 for it in it_vals):
                trust_continuous = "連續賣超"
                trust_signal = "偏空"
                trust_consecutive_days = -2
            elif it_vals[-1] > 0:
                trust_continuous = "買超"
                trust_signal = "中性偏多"
                trust_consecutive_days = 1
            elif it_vals[-1] < 0:
                trust_continuous = "賣超"
                trust_signal = "中性偏空"
                trust_consecutive_days = -1
            else:
                trust_continuous = "觀望"
                trust_signal = "中性"
                trust_consecutive_days = 0
            
            # 綜合訊號
            if foreign_signal == "偏多" and trust_signal == "偏多":
                overall_signal = "籌碼集中"
                signal_color = "positive"
            elif foreign_signal == "偏多" or trust_signal == "偏多":
                overall_signal = "籌碼偏多"
                signal_color = "positive"
            elif foreign_signal == "偏空" and trust_signal == "偏空":
                overall_signal = "籌碼分散"
                signal_color = "warning"
            elif foreign_signal == "偏空" or trust_signal == "偏空":
                overall_signal = "籌碼偏空"
                signal_color = "warning"
            else:
                overall_signal = "籌碼中性"
                signal_color = "neutral"
            
            # v4.4.2 新增：數值欄位
            foreign_net = fi_vals[-1]
            trust_net = it_vals[-1]
            foreign_amount = foreign_net / 100000000
            trust_amount = trust_net / 100000000
            
            return {
                "available": True,
                "data_source": "TWSE",
                "foreign": f"{foreign_continuous} ({foreign_amount:.2f}億)",
                "trust": f"{trust_continuous} ({trust_amount:.2f}億)",
                "dealer": "暫無數據",
                "foreign_continuous": foreign_continuous,
                "trust_continuous": trust_continuous,
                # v4.4.2 新增：數值驅動欄位
                "foreign_net": foreign_net,
                "trust_net": trust_net,
                "dealer_net": 0,
                "foreign_consecutive_days": foreign_consecutive_days,
                "trust_consecutive_days": trust_consecutive_days,
                "signal": overall_signal,
                "signal_color": signal_color,
                "message": f"最新資料日期：{last_two['date'].iloc[-1]}（已緩存）"
            }
            
        except Exception as e:
            print(f"籌碼分析錯誤: {e}")
            # 最後嘗試悟空 API
            return QuickAnalyzer._analyze_chip_flow_wukong(symbol) or {
                "available": False,
                "message": f"籌碼分析失敗: {str(e)}"
            }
    
    @staticmethod
    def _analyze_chip_flow_wukong(symbol):
        """
        v4.4.2 修正：使用悟空 API 取得個股三大法人籌碼資料
        API: https://api.wukong.com.tw/stock/{stockId}/iibs
        
        實際回傳格式：
        {
          "iibs": [
            {
              "inputDate": "2026-01-16",
              "foreignInvestorsBuySell": 10105,    // 外資買賣超（張數）
              "investmentTrustBuySell": 208,        // 投信買賣超（張數）
              "dealerBuySell": -1134,               // 自營商買賣超（張數）
              "total": 9179
            },
            ...
          ]
        }
        """
        try:
            url = f"https://api.wukong.com.tw/stock/{symbol}/iibs"
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'application/json',
                'Referer': 'https://wukong.com.tw/'
            }
            
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code != 200:
                print(f"[悟空API] {symbol} 請求失敗: {response.status_code}")
                return None
            
            data = response.json()
            
            # 正確解析格式：{"iibs": [...]}
            iibs_list = data.get('iibs', [])
            if not iibs_list:
                print(f"[悟空API] {symbol} 無 iibs 數據")
                return None
            
            # 依日期排序取最新筆
            try:
                iibs_list_sorted = sorted(iibs_list, key=lambda x: x.get('inputDate', ''), reverse=True)
                latest = iibs_list_sorted[0]
                print(f"[悟空API] {symbol} 取得最新資料日期：{latest.get('inputDate', 'N/A')}")
            except (ValueError, TypeError, IndexError):
                print(f"[悟空API] {symbol} 排序失敗，使用第一筆")
                latest = iibs_list[0]
            
            # 解析正確的欄位名稱（數值為張數）
            foreign_net = latest.get('foreignInvestorsBuySell', 0) or 0  # 張數
            trust_net = latest.get('investmentTrustBuySell', 0) or 0      # 張數
            dealer_net = latest.get('dealerBuySell', 0) or 0              # 張數
            total_net = latest.get('total', 0) or 0
            
            print(f"[悟空API] {symbol} 解析結果：外資={foreign_net}張, 投信={trust_net}張, 自營商={dealer_net}張")
            
            # 計算連續天數（分析歷史數據）
            foreign_consecutive_days = 0
            trust_consecutive_days = 0
            
            # 往前找連續同方向的天數
            if len(iibs_list_sorted) >= 2:
                # 外資連續天數
                if foreign_net > 0:
                    for i, item in enumerate(iibs_list_sorted):
                        if item.get('foreignInvestorsBuySell', 0) > 0:
                            foreign_consecutive_days = i + 1
                        else:
                            break
                elif foreign_net < 0:
                    for i, item in enumerate(iibs_list_sorted):
                        if item.get('foreignInvestorsBuySell', 0) < 0:
                            foreign_consecutive_days = -(i + 1)  # 負值表示賣超
                        else:
                            break
                
                # 投信連續天數
                if trust_net > 0:
                    for i, item in enumerate(iibs_list_sorted):
                        if item.get('investmentTrustBuySell', 0) > 0:
                            trust_consecutive_days = i + 1
                        else:
                            break
                elif trust_net < 0:
                    for i, item in enumerate(iibs_list_sorted):
                        if item.get('investmentTrustBuySell', 0) < 0:
                            trust_consecutive_days = -(i + 1)
                        else:
                            break
            else:
                # 只有一筆資料
                foreign_consecutive_days = 1 if foreign_net > 0 else (-1 if foreign_net < 0 else 0)
                trust_consecutive_days = 1 if trust_net > 0 else (-1 if trust_net < 0 else 0)
            
            print(f"[悟空API] {symbol} 連續天數：外資={foreign_consecutive_days}天, 投信={trust_consecutive_days}天")
            
            # 判斷外資訊號
            if foreign_net > 0:
                if abs(foreign_consecutive_days) >= 2:
                    foreign_text = f"連{abs(foreign_consecutive_days)}日買超"
                    foreign_signal = "偏多"
                else:
                    foreign_text = "買超"
                    foreign_signal = "中性偏多"
            elif foreign_net < 0:
                if abs(foreign_consecutive_days) >= 2:
                    foreign_text = f"連{abs(foreign_consecutive_days)}日賣超"
                    foreign_signal = "偏空"
                else:
                    foreign_text = "賣超"
                    foreign_signal = "中性偏空"
            else:
                foreign_text = "觀望"
                foreign_signal = "中性"
            
            # 判斷投信訊號
            if trust_net > 0:
                if abs(trust_consecutive_days) >= 2:
                    trust_text = f"連{abs(trust_consecutive_days)}日買超"
                    trust_signal = "偏多"
                else:
                    trust_text = "買超"
                    trust_signal = "中性偏多"
            elif trust_net < 0:
                if abs(trust_consecutive_days) >= 2:
                    trust_text = f"連{abs(trust_consecutive_days)}日賣超"
                    trust_signal = "偏空"
                else:
                    trust_text = "賣超"
                    trust_signal = "中性偏空"
            else:
                trust_text = "觀望"
                trust_signal = "中性"
            
            # 判斷自營商訊號
            if dealer_net > 0:
                dealer_text = "買超"
            elif dealer_net < 0:
                dealer_text = "賣超"
            else:
                dealer_text = "觀望"
            
            # 綜合訊號
            if foreign_signal == "偏多" and trust_signal == "偏多":
                overall_signal = "籌碼集中"
                signal_color = "positive"
            elif foreign_signal == "偏多" or trust_signal == "偏多":
                overall_signal = "籌碼偏多"
                signal_color = "positive"
            elif foreign_signal == "偏空" and trust_signal == "偏空":
                overall_signal = "籌碼分散"
                signal_color = "warning"
            elif foreign_signal == "偏空" or trust_signal == "偏空":
                overall_signal = "籌碼偏空"
                signal_color = "warning"
            else:
                overall_signal = "籌碼中性"
                signal_color = "neutral"
            
            # 格式化金額（張數轉換顯示）
            def format_volume(val):
                """格式化張數顯示"""
                abs_val = abs(val)
                if abs_val >= 10000:
                    return f"{val / 10000:.2f}萬張"
                else:
                    return f"{val:,}張"
            
            date_str = latest.get('inputDate', datetime.datetime.now().strftime('%Y-%m-%d'))
            
            return {
                "available": True,
                "data_source": "悟空API",
                "foreign": f"{foreign_text} ({format_volume(foreign_net)})",
                "trust": f"{trust_text} ({format_volume(trust_net)})",
                "dealer": f"{dealer_text} ({format_volume(dealer_net)})",
                "foreign_continuous": foreign_text,
                "trust_continuous": trust_text,
                "foreign_net": foreign_net,   # 單位：張（不再 ×1000，全系統統一用張）
                "trust_net": trust_net,
                "dealer_net": dealer_net,
                "foreign_consecutive_days": foreign_consecutive_days,
                "trust_consecutive_days": trust_consecutive_days,
                "signal": overall_signal,
                "signal_color": signal_color,
                "message": f"最新資料日期：{date_str}（悟空API）"
            }
            
        except requests.exceptions.Timeout:
            print(f"[悟空API] {symbol} 請求超時")
            return None
        except requests.exceptions.RequestException as e:
            print(f"[悟空API] {symbol} 請求錯誤: {e}")
            return None
        except Exception as e:
            print(f"[悟空API] {symbol} 解析錯誤: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    @staticmethod
    def _crawl_invest(date, stock_code):
        """抓取外資投信買賣超資料"""
        date_str = date.strftime('%Y%m%d')
        
        url = "https://www.twse.com.tw/fund/T86"
        params = {
            'response': 'json',
            'date': date_str,
            'selectType': 'ALL'
        }
        
        try:
            r = requests.get(url, params=params, timeout=10)
            data = r.json()
            if 'data' not in data or not data['data']:
                return None
                
            for row in data['data']:
                if row[0] == stock_code:
                    foreign_investor = int(row[4].replace(',', ''))
                    investment_trust = int(row[10].replace(',', ''))
                    
                    return {
                        'date': date_str,
                        'stock_code': stock_code,
                        'foreign_investor': foreign_investor,
                        'investment_trust': investment_trust
                    }
            return None
        except Exception as e:
            return None
    
    @staticmethod
    def _analyze_chip_flow_simulated(symbol, market="台股"):
        """模擬籌碼面數據"""
        last_digit = int(symbol[-1]) if symbol[-1].isdigit() else 0
        
        if last_digit >= 7:
            return {
                "available": True,
                "foreign": "連續買超 (模擬)",
                "trust": "買超 (模擬)",
                "dealer": "模擬數據",
                "foreign_continuous": "連續買超",
                "trust_continuous": "買超",
                "signal": "籌碼集中",
                "signal_color": "positive",
                "message": "⚠️ 使用模擬數據"
            }
        elif last_digit >= 4:
            return {
                "available": True,
                "foreign": "買超 (模擬)",
                "trust": "觀望 (模擬)",
                "dealer": "模擬數據",
                "foreign_continuous": "買超",
                "trust_continuous": "觀望",
                "signal": "籌碼穩定",
                "signal_color": "neutral",
                "message": "⚠️ 使用模擬數據"
            }
        else:
            return {
                "available": True,
                "foreign": "賣超 (模擬)",
                "trust": "賣超 (模擬)",
                "dealer": "模擬數據",
                "foreign_continuous": "賣超",
                "trust_continuous": "賣超",
                "signal": "籌碼分散",
                "signal_color": "warning",
                "message": "⚠️ 使用模擬數據"
            }
    
    @staticmethod
    def _technical_analysis(hist):
        """
        技術面分析 - v4.5.19 高盛級升級
        
        新增：
        - KD 值 (k, d)
        - MACD 完整數據 (macd, macd_signal, macd_histogram)
        - MACD 背離偵測 (macd_divergence)
        - MA20 斜率 (ma20_slope)
        """
        from analyzers import calculate_kd, calculate_macd
        
        ma5 = hist['Close'].rolling(window=5).mean()
        ma20 = hist['Close'].rolling(window=20).mean()
        ma60 = hist['Close'].rolling(window=60).mean()
        
        # RSI 計算
        delta = hist['Close'].diff()
        gain = delta.clip(lower=0).rolling(window=14).mean()
        loss = (-delta).clip(lower=0).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        
        current_price = hist['Close'].iloc[-1]
        current_rsi = rsi.iloc[-1] if not pd.isna(rsi.iloc[-1]) else 50
        
        # 計算 ADX
        adx, plus_di, minus_di = MarketRegimeAnalyzer.calculate_adx(hist)
        current_adx = adx.iloc[-1] if not pd.isna(adx.iloc[-1]) else 20
        
        # === v4.5.19 新增：KD 計算 ===
        try:
            k_series, d_series = calculate_kd(hist)
            k_value = k_series.iloc[-1] if not pd.isna(k_series.iloc[-1]) else 50
            d_value = d_series.iloc[-1] if not pd.isna(d_series.iloc[-1]) else 50
        except:
            k_value = 50
            d_value = 50
        
        # === v4.5.19 新增：MACD 計算 ===
        try:
            macd_line, signal_line, macd_hist_series = calculate_macd(hist['Close'])
            macd = macd_line.iloc[-1] if not pd.isna(macd_line.iloc[-1]) else 0
            macd_signal = signal_line.iloc[-1] if not pd.isna(signal_line.iloc[-1]) else 0
            macd_histogram = macd_hist_series.iloc[-1] if not pd.isna(macd_hist_series.iloc[-1]) else 0
        except:
            macd = 0
            macd_signal = 0
            macd_histogram = 0
            macd_hist_series = pd.Series([0])
        
        # === v4.5.19 新增：MACD 背離偵測 ===
        macd_divergence = {'bullish_divergence': False, 'bearish_divergence': False}
        try:
            if len(hist) >= 20 and len(macd_hist_series) >= 20:
                prices = hist['Close'].values[-20:]
                macd_vals = macd_hist_series.values[-20:]
                
                # 簡化版背離偵測
                # 底背離：價格新低但 MACD 底部墊高
                price_min_idx = np.argmin(prices[-10:])
                price_prev_min_idx = np.argmin(prices[:10])
                
                if prices[-10:][price_min_idx] < prices[:10][price_prev_min_idx]:
                    # 價格創新低
                    if macd_vals[-10:][price_min_idx] > macd_vals[:10][price_prev_min_idx]:
                        # MACD 底部墊高
                        macd_divergence['bullish_divergence'] = True
                
                # 頂背離：價格新高但 MACD 頂部降低
                price_max_idx = np.argmax(prices[-10:])
                price_prev_max_idx = np.argmax(prices[:10])
                
                if prices[-10:][price_max_idx] > prices[:10][price_prev_max_idx]:
                    # 價格創新高
                    if macd_vals[-10:][price_max_idx] < macd_vals[:10][price_prev_max_idx]:
                        # MACD 頂部降低
                        macd_divergence['bearish_divergence'] = True
        except:
            pass
        
        # === v4.5.19 新增：MA20 斜率 (用於 RSI 區間判斷) ===
        try:
            ma20_slope = (ma20.iloc[-1] - ma20.iloc[-5]) / ma20.iloc[-5] if not pd.isna(ma20.iloc[-5]) else 0
        except:
            ma20_slope = 0
        
        # 趨勢判斷
        if current_price > ma20.iloc[-1] > ma60.iloc[-1]:
            trend = "上升趨勢"
            signal = "偏多"
        elif current_price < ma20.iloc[-1] < ma60.iloc[-1]:
            trend = "下降趨勢"
            signal = "偏空"
        else:
            trend = "盤整格局"
            signal = "中性"
        
        return {
            "trend": trend,
            "signal": signal,
            "rsi": round(current_rsi, 2),
            "adx": round(current_adx, 2),
            "ma5": round(ma5.iloc[-1], 2) if not pd.isna(ma5.iloc[-1]) else "N/A",
            "ma20": round(ma20.iloc[-1], 2) if not pd.isna(ma20.iloc[-1]) else "N/A",
            "ma60": round(ma60.iloc[-1], 2) if not pd.isna(ma60.iloc[-1]) else "N/A",
            # === v4.5.19 新增欄位 ===
            "k": round(k_value, 2),
            "d": round(d_value, 2),
            "macd": round(macd, 4),
            "macd_signal": round(macd_signal, 4),
            "macd_histogram": round(macd_histogram, 4),
            "macd_divergence": macd_divergence,
            "ma20_slope": round(ma20_slope, 4)
        }
    
    @staticmethod
    def _calculate_support_resistance(hist, technical):
        """計算支撐壓力位與停損停利建議"""
        try:
            current_price = hist['Close'].iloc[-1]
            
            ma20 = technical['ma20']
            if isinstance(ma20, str):
                ma20 = current_price * 0.95
            
            recent_low = hist['Low'].tail(20).min()
            support1 = max(ma20, recent_low)
            support2 = hist['Low'].tail(60).min()
            
            recent_high = hist['High'].tail(20).max()
            sma = hist['Close'].rolling(window=20).mean().iloc[-1]
            std = hist['Close'].rolling(window=20).std().iloc[-1]
            upper_band = sma + (2 * std)
            resistance1 = min(recent_high, upper_band)
            resistance2 = hist['High'].tail(60).max()
            
            stop_loss = support1 * 0.98
            take_profit = resistance1 * 0.98
            
            if current_price > stop_loss:
                risk_reward = (take_profit - current_price) / (current_price - stop_loss)
            else:
                risk_reward = 0
            
            return {
                "support1": round(support1, 2),
                "support2": round(support2, 2),
                "resistance1": round(resistance1, 2),
                "resistance2": round(resistance2, 2),
                "stop_loss": round(stop_loss, 2),
                "take_profit": round(take_profit, 2),
                "risk_reward": round(risk_reward, 2)
            }
        except:
            return {
                "support1": 0, "support2": 0,
                "resistance1": 0, "resistance2": 0,
                "stop_loss": 0, "take_profit": 0,
                "risk_reward": 0
            }
    
    @staticmethod
    def analyze_strategies_v4(hist, technical, fundamental, market_regime):
        """v4.0 改進：策略分析（考慮市場環境 + 穩定性評分）"""
        strategies = {}
        
        current_price = hist['Close'].iloc[-1]
        ma5 = hist['Close'].rolling(window=5).mean()
        ma20 = hist['Close'].rolling(window=20).mean()
        ma60 = hist['Close'].rolling(window=60).mean()
        
        # 1. 趨勢策略分析
        trend_strength = abs((ma5.iloc[-1] - ma20.iloc[-1]) / ma20.iloc[-1] * 100) if not pd.isna(ma5.iloc[-1]) and not pd.isna(ma20.iloc[-1]) else 0
        
        if current_price > ma5.iloc[-1]:
            short_term = "建議買進"
            short_reason = "價格站上短期均線"
        elif current_price < ma5.iloc[-1]:
            short_term = "建議賣出"
            short_reason = "價格跌破短期均線"
        else:
            short_term = "建議觀望"
            short_reason = "價格在均線附近"
        
        if ma5.iloc[-1] > ma20.iloc[-1]:
            mid_term = "建議買進"
            mid_reason = "黃金交叉，多頭排列"
        elif ma5.iloc[-1] < ma20.iloc[-1]:
            mid_term = "建議賣出"
            mid_reason = "死亡交叉，空頭排列"
        else:
            mid_term = "建議觀望"
            mid_reason = "均線糾結"
        
        if not pd.isna(ma60.iloc[-1]):
            if ma20.iloc[-1] > ma60.iloc[-1]:
                long_term = "建議買進"
                long_reason = "長期趨勢向上"
            elif ma20.iloc[-1] < ma60.iloc[-1]:
                long_term = "建議賣出"
                long_reason = "長期趨勢向下"
            else:
                long_term = "建議觀望"
                long_reason = "長期趨勢不明"
        else:
            long_term = "資料不足"
            long_reason = "需更多歷史資料"
        
        signal = "適合" if trend_strength > 2 else "不適合"
        
        strategies['趨勢策略'] = {
            'signal': signal,
            'strength': trend_strength,
            'reason': f"當前{'多頭' if ma5.iloc[-1] > ma20.iloc[-1] else '空頭'}排列",
            'execution': f"建議使用MA5/MA20交叉策略",
            'risk': "注意盤整時期的假突破" if trend_strength < 1 else "注意趨勢反轉訊號",
            'short_term': short_term, 'short_reason': short_reason,
            'mid_term': mid_term, 'mid_reason': mid_reason,
            'long_term': long_term, 'long_reason': long_reason
        }
        
        # 2. 動能策略分析
        rsi = technical['rsi']
        
        if rsi < 30:
            short_term = "建議買進"
            short_reason = "RSI超賣，可能反彈"
            momentum_signal = "適合"
        elif rsi > 70:
            short_term = "建議賣出"
            short_reason = "RSI超買，可能回檔"
            momentum_signal = "適合"
        else:
            short_term = "建議觀望"
            short_reason = f"RSI={rsi:.1f}，中性區域"
            momentum_signal = "不適合"
        
        if 40 < rsi < 60:
            mid_term = "建議觀望"
            mid_reason = "動能不足，等待極值"
        elif rsi < 40:
            mid_term = "建議逢低買進"
            mid_reason = "動能偏弱，可分批進場"
        else:
            mid_term = "建議逢高賣出"
            mid_reason = "動能偏強，可分批出場"
        
        strategies['動能策略'] = {
            'signal': momentum_signal,
            'strength': abs(rsi - 50),
            'reason': f"RSI={rsi:.1f}",
            'execution': f"建議在RSI極值時操作",
            'risk': "強勢股RSI可能長期超買",
            'short_term': short_term, 'short_reason': short_reason,
            'mid_term': mid_term, 'mid_reason': mid_reason,
            'long_term': "不適用", 'long_reason': "動能策略適合短中線操作"
        }
        
        # 3. 通道策略分析
        sma = hist['Close'].rolling(window=20).mean()
        std = hist['Close'].rolling(window=20).std()
        upper = sma + (2 * std)
        lower = sma - (2 * std)
        
        position_in_channel = (current_price - lower.iloc[-1]) / (upper.iloc[-1] - lower.iloc[-1]) if (upper.iloc[-1] - lower.iloc[-1]) > 0 else 0.5
        
        if position_in_channel > 0.8:
            short_term = "建議賣出"
            short_reason = "接近上軌，可能回檔"
            channel_signal = "適合"
        elif position_in_channel < 0.2:
            short_term = "建議買進"
            short_reason = "接近下軌，可能反彈"
            channel_signal = "適合"
        else:
            short_term = "建議觀望"
            short_reason = f"在通道{position_in_channel*100:.0f}%位置"
            channel_signal = "不適合"
        
        strategies['通道策略'] = {
            'signal': channel_signal,
            'strength': abs(position_in_channel - 0.5) * 100,
            'reason': f"價格在通道{position_in_channel*100:.0f}%位置",
            'execution': "上軌賣出，下軌買進",
            'risk': "突破通道後可能形成新趨勢",
            'short_term': short_term, 'short_reason': short_reason,
            'mid_term': "依短線訊號", 'mid_reason': "通道正常，依位置操作",
            'long_term': "不適用", 'long_reason': "通道策略適合短中線操作"
        }
        
        # 4. 均值回歸策略分析
        z_score = (current_price - sma.iloc[-1]) / std.iloc[-1] if std.iloc[-1] > 0 else 0
        
        if z_score > 2:
            short_term = "建議賣出"
            short_reason = f"Z={z_score:.2f}，遠高於均值"
            reversion_signal = "適合"
        elif z_score < -2:
            short_term = "建議買進"
            short_reason = f"Z={z_score:.2f}，遠低於均值"
            reversion_signal = "適合"
        else:
            short_term = "建議觀望"
            short_reason = f"Z={z_score:.2f}，接近均值"
            reversion_signal = "不適合"
        
        strategies['均值回歸策略'] = {
            'signal': reversion_signal,
            'strength': abs(z_score) * 50,
            'reason': f"Z-Score={z_score:.2f}",
            'execution': "偏離均值過大時反向操作",
            'risk': "趨勢市場中均值會不斷改變",
            'short_term': short_term, 'short_reason': short_reason,
            'mid_term': "建議等待回歸" if abs(z_score) > 1 else "建議觀望",
            'mid_reason': "偏離均值" if abs(z_score) > 1 else "接近均值",
            'long_term': "不適用", 'long_reason': "均值回歸適合短中線操作"
        }
        
        # v4.0 改進：執行回測並整合穩定性評分
        backtest_results = {}
        
        try:
            backtest_results['趨勢策略'] = BacktestEngine.backtest_trend_strategy(hist)
        except:
            backtest_results['趨勢策略'] = None
        
        try:
            backtest_results['動能策略'] = BacktestEngine.backtest_momentum_strategy(hist)
        except:
            backtest_results['動能策略'] = None
        
        try:
            backtest_results['通道策略'] = BacktestEngine.backtest_channel_strategy(hist)
        except:
            backtest_results['通道策略'] = None
        
        try:
            backtest_results['均值回歸策略'] = BacktestEngine.backtest_mean_reversion_strategy(hist)
        except:
            backtest_results['均值回歸策略'] = None
        
        # v4.0 改進：綜合評分（適用性 + 績效 + 穩定性 + 市場環境調整）
        strategy_total_scores = {}
        
        # 取得市場環境調整權重
        regime_adjustments = market_regime.get('strategy_adjustment', {}) if market_regime.get('available') else {}
        
        for strategy_name, strategy_info in strategies.items():
            # 適用性評分
            if strategy_info['signal'] == '適合':
                applicability_score = min(100, strategy_info['strength'])
            else:
                applicability_score = 0
            
            # 績效評分
            bt_result = backtest_results.get(strategy_name)
            if bt_result:
                backtest_return = bt_result['total_return']
                performance_score = min(100, max(0, (backtest_return + 50)))
                
                # v4.0 新增：穩定性評分（使用 Sharpe Ratio）
                sharpe = bt_result['sharpe_ratio']
                stability_score = min(100, max(0, sharpe * 30 + 50))  # Sharpe 1.5 = 95分
            else:
                performance_score = 50
                stability_score = 50
                backtest_return = 0
                sharpe = 0
            
            # v4.0 改進：綜合評分 = 適用性×0.3 + 績效×0.35 + 穩定性×0.35
            base_score = (
                applicability_score * QuantConfig.WEIGHT_APPLICABILITY +
                performance_score * QuantConfig.WEIGHT_PERFORMANCE +
                stability_score * QuantConfig.WEIGHT_STABILITY
            )
            
            # v4.0 新增：市場環境調整
            regime_weight = regime_adjustments.get(strategy_name, {}).get('weight', 1.0)
            adjusted_score = base_score * regime_weight
            
            strategy_total_scores[strategy_name] = {
                'total_score': adjusted_score,
                'base_score': base_score,
                'applicability_score': applicability_score,
                'performance_score': performance_score,
                'stability_score': stability_score,
                'backtest_return': backtest_return,
                'sharpe_ratio': sharpe,
                'regime_weight': regime_weight
            }
            
            # 更新策略資訊
            strategies[strategy_name]['backtest_return'] = f"{backtest_return:.2f}%"
            strategies[strategy_name]['sharpe_ratio'] = f"{sharpe:.2f}"
            strategies[strategy_name]['total_score'] = f"{adjusted_score:.1f}"
            strategies[strategy_name]['regime_adjustment'] = regime_adjustments.get(strategy_name, {}).get('recommendation', '')
        
        # 選擇最佳策略
        if strategy_total_scores:
            best_strategy = max(strategy_total_scores.keys(),
                              key=lambda x: strategy_total_scores[x]['total_score'])
            
            best_score_info = strategy_total_scores[best_strategy]
            
            if best_score_info['total_score'] < 30:
                best_strategy = "暫無特別適合的策略，建議觀望"
            else:
                # 附加說明
                best_strategy_detail = (
                    f"{best_strategy} "
                    f"(評分:{best_score_info['total_score']:.0f}, "
                    f"Sharpe:{best_score_info['sharpe_ratio']:.2f})"
                )
                best_strategy = best_strategy_detail
        else:
            best_strategy = "暫無特別適合的策略，建議觀望"
        
        return strategies, best_strategy
    
    @staticmethod
    def _generate_recommendation_v43(result, decision_matrix):
        """
        v4.3 新版本：基於多因子決策矩陣生成綜合建議
        v4.4.6 更新：整合形態分析評分 + 否決權 + 矛盾仲裁
        
        此函數整合決策矩陣結果、形態分析與傳統評分系統，產出一致性的投資建議。
        
        評分權重（v4.4.6 加權制）：
        - 形態學：40%（最高優先）
        - 波段策略：30%
        - 量價分析：20%
        - 輔助指標：10%
        
        新增機制：
        1. 絕對否決權：RSI > 85 或 乖離 > 20% 時強制限制評分上限
        2. 形態否決權：頭部形態確立時禁止做多
        3. 矛盾仲裁：形態與波段衝突時，以成交量裁決
        """
        # 傳統評分（用於輔助判斷）
        tech_signal = result["technical"]["signal"]
        fund_signal = result["fundamental"]["signal"]
        rsi = result["technical"]["rsi"]
        
        chip_signal = "中性"
        if "chip_flow" in result and result["chip_flow"]["available"]:
            chip_signal = result["chip_flow"]["signal"]
        
        # ============================================================
        # v4.4.6：新版加權評分系統
        # ============================================================
        
        # 1. 形態學分數（權重 40%）
        pattern_score = 50  # 基準分數
        pattern_info = None
        pattern_is_bearish = False  # 追蹤形態是否看空
        pattern_is_bullish = False  # 追蹤形態是否看多
        
        if result.get('pattern_analysis', {}).get('available'):
            pa = result['pattern_analysis']
            pattern_info = pa
            if pa.get('detected'):
                pattern_score += pa.get('score_impact', 0)
                pattern_score = max(0, min(100, pattern_score))
                
                # 標記形態方向
                pattern_status = pa.get('status', '')
                pattern_signal = pa.get('signal', 'neutral')
                if 'CONFIRMED' in pattern_status:
                    if pattern_signal == 'sell':
                        pattern_is_bearish = True
                    elif pattern_signal == 'buy':
                        pattern_is_bullish = True
        
        # 2. 波段策略分數（權重 30%）
        wave_score = 50
        wave_is_bullish = False
        wave_is_bearish = False
        
        wave = result.get('wave_analysis', {})
        if wave.get('available'):
            if wave.get('breakout_signal', {}).get('detected'):
                if wave.get('breakout_signal', {}).get('volume_confirmed'):
                    wave_score = 85
                else:
                    wave_score = 70
                wave_is_bullish = True
            elif wave.get('breakdown_signal', {}).get('detected'):
                wave_score = 20
                wave_is_bearish = True
            elif wave.get('is_bullish_env'):
                wave_score = 65
                wave_is_bullish = True
            elif wave.get('is_bearish_env'):
                wave_score = 35
                wave_is_bearish = True
        
        # 3. 量價分析分數（權重 20%）
        volume_score = 50
        volume_ratio = 1.0  # 用於矛盾仲裁
        
        vp = result.get('volume_price', {})
        if vp.get('available'):
            vp_score_raw = vp.get('vp_score', 0)
            volume_score = 50 + vp_score_raw / 2
            volume_score = max(0, min(100, volume_score))
        
        # 取得成交量比率（用於仲裁）
        vol_analysis = result.get('volume_analysis', {})
        if vol_analysis:
            volume_ratio = vol_analysis.get('volume_ratio', 1.0)
        
        # 4. 輔助指標分數（權重 10%）
        indicator_score = 50
        if tech_signal == "偏多":
            indicator_score = 70
        elif tech_signal == "偏空":
            indicator_score = 30
        if rsi > 70:
            indicator_score -= 15
        elif rsi < 30:
            indicator_score += 15
        
        # ============================================================
        # v4.4.6 新增：矛盾仲裁機制
        # ============================================================
        conflict_resolved = False
        conflict_message = ""
        
        # 情境 1：形態看空 (M頭/頭肩頂) 但波段看多
        if pattern_is_bearish and wave_is_bullish:
            if volume_ratio < 1.0:
                # 量縮：可能是假跌破，減輕形態扣分
                pattern_score = min(pattern_score + 20, 50)  # 把扣掉的分補回一些
                conflict_resolved = True
                conflict_message = "⚠️ 形態看空但量縮，判定可能為假跌破"
            else:
                # 帶量跌破：聽形態的，壓制波段分數
                wave_score = min(wave_score, 50)
                conflict_resolved = True
                conflict_message = "⚠️ 形態帶量跌破，以形態判斷為主"
        
        # 情境 2：形態看多 (W底) 但波段看空
        if pattern_is_bullish and wave_is_bearish:
            if volume_ratio >= 1.2:
                # 帶量突破：聽形態的，這是真突破
                wave_score = max(wave_score, 50)
                conflict_resolved = True
                conflict_message = "✓ 形態帶量突破，以形態判斷為主"
            else:
                # 量不足：突破可能失敗
                pattern_score = min(pattern_score, 60)
                conflict_resolved = True
                conflict_message = "⚠️ 形態突破但量能不足，突破可能失敗"
        
        # ============================================================
        # 計算加權總分
        # ============================================================
        weighted_score = (
            pattern_score * QuantConfig.WEIGHT_PATTERN +
            wave_score * QuantConfig.WEIGHT_WAVE +
            volume_score * QuantConfig.WEIGHT_VOLUME +
            indicator_score * QuantConfig.WEIGHT_INDICATOR
        )
        
        # ============================================================
        # v4.4.6 新增：絕對否決權 (Veto Rules)
        # ============================================================
        veto_applied = False
        veto_reason = ""
        score_cap = 100  # 評分上限（預設無限制）
        risk_notes = []  # A2 改動4：強勢股的過熱/形態風險改為提示，不壓分

        # 取得乖離率
        mr = result.get('mean_reversion', {})
        bias_20 = mr.get('bias_analysis', {}).get('bias_20', 0) if mr.get('available') else 0

        # A2 改動4：動能模式（RS 領先+多頭排列）由三層引擎判定，覆蓋層共用同一旗標。
        # 強勢領漲股的「過熱/頭部」一律降為風險提示，不把 overall 蓋成觀望/暫緩。
        _is_mom = bool(
            ((decision_matrix.get('three_layer', {}) or {}).get('position') or {}).get('is_momentum', False)
        ) if decision_matrix.get('available') else False

        # 否決權 1：RSI 極度過熱 (> 85)
        if rsi > 85:
            if _is_mom:
                risk_notes.append(f"RSI過熱（{rsi:.0f}），強勢股續抱、留意追高")
            else:
                score_cap = min(score_cap, 55)  # 鎖定評分上限，不會出現強力買進
                veto_applied = True
                veto_reason = f"RSI極度過熱（{rsi:.0f}），禁止追價"

        # 否決權 2：乖離率過大 (> 20%)
        if bias_20 > 20:
            if _is_mom:
                risk_notes.append(f"乖離率偏大（{bias_20:.1f}%），強勢延伸、留意追高")
            else:
                score_cap = min(score_cap, 50)
                veto_applied = True
                veto_reason = f"乖離率過大（{bias_20:.1f}%），禁止追價"

        # 否決權 3：形態頭部確立
        # 注意：頭部形態對強勢飆股易誤判（拉回常被當做頭），故動能模式下僅提示。
        if pattern_is_bearish:
            if _is_mom:
                risk_notes.append(
                    f"形態疑似頭部（{pattern_info.get('pattern_name', '')}），"
                    f"但 RS 領先+趨勢成立，僅供留意"
                )
            else:
                score_cap = min(score_cap, 45)  # 頭部確立時，最高只能觀望
                veto_applied = True
                if not veto_reason:
                    veto_reason = f"頭部形態確立（{pattern_info.get('pattern_name', '')}），禁止做多"
        
        # 應用評分上限
        weighted_score = min(weighted_score, score_cap)
        
        # 傳統分數（向後兼容）
        score = 0
        if tech_signal == "偏多":
            score += 30
        elif tech_signal == "中性":
            score += 15
        if fund_signal == "偏多":
            score += 30
        elif fund_signal == "中性":
            score += 15
        if chip_signal in ["籌碼集中", "籌碼偏多"]:
            score += 30
        elif chip_signal in ["籌碼中性", "中性", "籌碼穩定"]:
            score += 20
        
        # A2 改動4(b)：UI 分數與訊號同源。
        # 改用三層引擎綜合分（direction×0.35 + position×0.65，由 ThreeLayerEngine
        # 輸出於 decision_matrix['score']），解決「分數來源(形態40%)與最終文字
        # (三層引擎)是兩套、會不一致」的問題。形態退為資訊註記，不再當 score 主權重。
        # 加權分(weighted_score)/傳統分(score) 僅在三層引擎無輸出時作為 fallback。
        if decision_matrix.get('available') and isinstance(decision_matrix.get('score'), (int, float)):
            final_score = int(decision_matrix['score'])
        elif result.get('pattern_analysis', {}).get('available'):
            final_score = int(weighted_score)
        else:
            final_score = score

        # v4.4.3 新增：限制總分在 0-100 之間 (Clamp score)
        final_score = max(0, min(100, final_score))
        
        # 從決策矩陣獲取核心建議
        if decision_matrix.get('available'):
            dm = decision_matrix
            dv = dm.get('decision_vars', {})
            
            overall = dm.get('recommendation', '建議觀望')
            action_timing = dm.get('action_timing', '等待明確訊號')
            scenario = dm.get('scenario', 'X')
            scenario_name = dm.get('scenario_name', '待觀察')
            warning_message = dm.get('warning_message', '')
            confidence = dm.get('confidence', 'Medium')
            downgraded = dm.get('downgraded', False)
            filters_applied = dm.get('filters_applied', [])
            rr_ratio = dv.get('rr_ratio', 0)
            bias_20 = dv.get('bias_20', 0)
            
            # v5.0：形態分析覆蓋建議（加入三道防線，避免矛盾訊號）
            if pattern_info and pattern_info.get('detected'):
                pattern_status = pattern_info.get('status', '')
                pattern_signal = pattern_info.get('signal', 'neutral')
                pattern_name   = pattern_info.get('pattern_name', '')
                p_target       = pattern_info.get('target_price', 0) or 0
                p_stop         = pattern_info.get('stop_loss', 0) or 0
                current_px     = result.get('current_price', 0) or 0

                # ── 買進形態覆蓋：必須通過三道防線 ──────────────────
                if 'CONFIRMED' in pattern_status and pattern_signal == 'buy':

                    # 防線 1：目標價必須高於現價（目標已達則形態失效）
                    _target_valid = (p_target <= 0) or (p_target > current_px * 1.02)

                    # 防線 2：三層引擎的場景不能是 SKIP / WAIT（方向或位置否決）
                    _engine_ok = dm.get('scenario', '') not in ('SKIP', 'WAIT')

                    # 防線 3：乖離率不能過熱（> 15%）。
                    # A2 改動4：動能模式（RS 領先+趨勢成立）下，正乖離是強度不是過熱，
                    # 不作為阻擋條件（飆股拉回常被此防線誤殺成「暫緩」）。
                    _bias_ok = True if _is_mom else (abs(bias_20) <= 15)

                    if _target_valid and _engine_ok and _bias_ok:
                        # 三道防線全過 → 允許形態覆蓋，輸出買進建議
                        overall = f'強烈建議買進（{pattern_name}確立）'
                        action_timing = '形態突破，可進場'
                        warning_message = (
                            pattern_info.get('description', '')
                            + (f' 目標價${p_target:.2f}，停損${p_stop:.2f}' if p_target > 0 else '')
                        )
                        confidence = 'High'
                    elif _is_mom:
                        # A2 改動4：強勢領漲股不蓋成「暫緩」。
                        # 保留三層引擎的 overall 裁決，形態問題只當風險註記附上。
                        _pat_notes = []
                        if not _target_valid:
                            _pat_notes.append(
                                f'形態目標 ${p_target:.2f} 已被現價 ${current_px:.2f} 超越（測幅達成，僅供參考）'
                            )
                        if not _engine_ok:
                            _pat_notes.append(
                                f'三層引擎場景：{dm.get("scenario_name", dm.get("scenario", ""))}'
                            )
                        if _pat_notes:
                            warning_message = (warning_message + ' ｜ 形態註記：'
                                               + '；'.join(_pat_notes)).strip(' ｜')
                    else:
                        # 非強勢股：任一防線失守 → 降為觀察，附上原因
                        _block_reasons = []
                        if not _target_valid:
                            _block_reasons.append(
                                f'目標價 ${p_target:.2f} 已低於現價 ${current_px:.2f}（形態目標已達成，追高風險大）'
                            )
                        if not _engine_ok:
                            _block_reasons.append(
                                f'三層引擎否決（場景：{dm.get("scenario_name", dm.get("scenario", ""))}）'
                            )
                        if not _bias_ok:
                            _block_reasons.append(
                                f'乖離率過大（{bias_20:+.1f}%），追高風險高'
                            )
                        overall = f'形態確立但暫緩買進（{pattern_name}）'
                        action_timing = '等待拉回或乖離收斂後再進場'
                        warning_message = (
                            pattern_info.get('description', '')
                            + ' ⚠️ 覆蓋條件未達：' + '；'.join(_block_reasons)
                        )
                        confidence = 'Medium'

                # ── 賣出形態覆蓋（無需防線，頭部形態確立直接賣）──────
                elif 'CONFIRMED' in pattern_status and pattern_signal == 'sell':
                    overall = f'建議賣出（{pattern_name}確立）'
                    action_timing = '形態跌破，應出場'
                    warning_message = (
                        pattern_info.get('description', '')
                        + (f' 目標價${p_target:.2f}' if p_target > 0 else '')
                    )
                    confidence = 'High'

                # ── TARGET_REACHED 狀態：形態已完成，不再建議買進 ─────
                elif pattern_status == 'TARGET_REACHED':
                    # 不覆蓋 overall，保留三層引擎的裁決
                    # 只補充說明
                    warning_message = pattern_info.get('description', warning_message)
            
            # 生成分段操作建議
            # 修正：改傳 action_code（與上方三層引擎裁決同源），避免 scenario
            # 代碼撞號（三層引擎 'A'=A級主攻，舊字典 'A'=多頭過熱，意思相反）。
            _action_code = dm.get('action_code', '')
            short_term = QuickAnalyzer._get_short_term_from_scenario(scenario, dv, result, _action_code)
            mid_term = QuickAnalyzer._get_mid_term_from_scenario(scenario, dv, result, _action_code)
            long_term = QuickAnalyzer._get_long_term_recommendation(result, final_score)
            
            # 構建基本建議結果
            recommendation_result = {
                "overall": overall,
                "score": final_score,
                "action_timing": action_timing,
                "scenario": scenario,
                "scenario_name": scenario_name,
                "warning_message": warning_message,
                "confidence": confidence,
                "downgraded": downgraded,
                "filters_applied": filters_applied,
                "original_recommendation": decision_matrix.get('original_recommendation', overall),
                "rr_ratio": rr_ratio,
                "bias_20": bias_20,
                "short_term": short_term,
                "mid_term": mid_term,
                "long_term": long_term,
                # v4.4.6 新增：分項分數
                "score_breakdown": {
                    "pattern_score": pattern_score,
                    "wave_score": wave_score,
                    "volume_score": volume_score,
                    "indicator_score": indicator_score,
                    "weighted_score": round(weighted_score, 1),
                    "score_cap": score_cap  # 評分上限
                },
                # v4.4.6 新增：否決權與矛盾仲裁資訊
                "veto_info": {
                    "veto_applied": veto_applied,
                    "veto_reason": veto_reason,
                    "conflict_resolved": conflict_resolved,
                    "conflict_message": conflict_message
                }
            }
            
            # 如果有否決權觸發，在警告訊息中加入
            if veto_applied and veto_reason:
                if warning_message:
                    recommendation_result["warning_message"] = f"🛑 {veto_reason} | {warning_message}"
                else:
                    recommendation_result["warning_message"] = f"🛑 {veto_reason}"
            
            # 資料異常：漲跌幅超過漲跌停 → 最高優先警示（整份分析可能不可信）
            if result.get('price_anomaly'):
                _pa = (f"🛑 資料異常：漲跌幅 {result.get('price_change_pct', 0):+.1f}% 超過±10%漲跌停，"
                       f"即時價與昨收可能未對齊，本檔分析請勿採信")
                _ew = recommendation_result.get("warning_message", "")
                recommendation_result["warning_message"] = f"{_pa} | {_ew}" if _ew else _pa

            # A2 改動4：強勢股的過熱/形態風險提示（不壓分，純資訊）併入警示
            if risk_notes:
                existing_warning = recommendation_result.get("warning_message", "")
                _rn = '⚠️ 風險提示：' + '；'.join(risk_notes)
                recommendation_result["warning_message"] = (
                    f"{existing_warning} | {_rn}" if existing_warning else _rn
                )

            # 如果有矛盾被仲裁，也加入
            if conflict_resolved and conflict_message:
                existing_warning = recommendation_result.get("warning_message", "")
                if existing_warning:
                    recommendation_result["warning_message"] = f"{existing_warning} | {conflict_message}"
                else:
                    recommendation_result["warning_message"] = conflict_message
            
            # v4.4.6 新增：形態資訊
            if pattern_info and pattern_info.get('detected'):
                recommendation_result['pattern_info'] = {
                    'pattern_name': pattern_info.get('pattern_name'),
                    'status': pattern_info.get('status'),
                    'neckline_price': pattern_info.get('neckline_price'),
                    'target_price': pattern_info.get('target_price'),
                    'stop_loss': pattern_info.get('stop_loss'),
                    'signal': pattern_info.get('signal'),
                    'volume_confirmed': pattern_info.get('volume_confirmed', False)
                }
            
            # v4.4.7 新增：解釋原因和目標價
            explanation = dm.get('explanation', '')
            if explanation:
                recommendation_result['explanation'] = explanation
            
            price_targets = dm.get('price_targets', {})
            if price_targets and price_targets.get('available'):
                recommendation_result['price_targets'] = price_targets
            
            # 修正：場景 E 或 F（區間操作），加入 range_info
            range_info = dm.get('range_info', {})
            if range_info and scenario in ['E', 'F']:
                recommendation_result['range_info'] = range_info
            
            return recommendation_result
        else:
            # 決策矩陣不可用時，使用傳統邏輯
            return QuickAnalyzer._generate_recommendation(result)
    
    @staticmethod
    def _get_short_term_from_scenario(scenario, decision_vars, result, action_code=''):
        """根據三層引擎的 action_code 生成短線建議（與上方裁決同源）。

        修正：原本依 scenario 查舊字典，但三層引擎的 scenario 代碼（'A'=A級主攻）
        與舊字典（'A'=多頭過熱）撞號，導致「A級主攻」卻顯示「暫停加碼」的自相矛盾。
        改以 action_code 為主。
        """
        bias_20 = decision_vars.get('bias_20', 0)
        rsi = decision_vars.get('rsi', 50)
        rr_ratio = decision_vars.get('rr_ratio', 0)

        # 與三層引擎裁決同源（action_code 明確、不撞號）
        _ac_map = {
            'STRONG_BUY': {'action': '積極進場（A級主攻）',
                           'reason': '方向+位置+時機三者到位，順勢操作'},
            'BUY':        {'action': '可進場、分批佈局（B級追蹤）',
                           'reason': '訊號成形，等量能/拉回確認可加碼'},
            'HOLD':       {'action': '持股續抱、暫不加碼',
                           'reason': '已有部位者續抱，空手者等更好位置'},
            'WAIT':       {'action': '等待拉回再進場',
                           'reason': '方向偏多但位置偏高，等乖離收斂'},
            'SKIP':       {'action': '不參與',
                           'reason': '趨勢/方向不利，避開'},
            'SELL':       {'action': '賣出 / 出場',
                           'reason': '觸發賣出訊號'},
            'TAKE_PROFIT': {'action': '分批停利',
                            'reason': '高檔過熱，獲利了結'},
        }
        if action_code in _ac_map:
            return _ac_map[action_code]

        # 場景 E 或 F 特殊處理：加入區間詳細資訊
        if scenario in ['E', 'F']:
            # 嘗試從支撐壓力位取得箱頂箱底
            sr = result.get('support_resistance', {})
            current_price = result.get('current_price', 0)
            
            box_top = sr.get('resistance1', 0)
            box_bottom = sr.get('support1', 0)
            
            # 如果有有效的箱頂箱底，計算位置並給出具體建議
            if box_top > 0 and box_bottom > 0 and box_top > box_bottom:
                range_width = box_top - box_bottom
                position_pct = ((current_price - box_bottom) / range_width) * 100 if range_width > 0 else 50
                
                if position_pct <= 30:
                    action = '區間操作：接近箱底，適合買進'
                    reason = f'箱底${box_bottom:.1f}↔箱頂${box_top:.1f}，目前靠近箱底'
                elif position_pct >= 70:
                    action = '區間操作：接近箱頂，適合賣出'
                    reason = f'箱底${box_bottom:.1f}↔箱頂${box_top:.1f}，目前靠近箱頂'
                else:
                    action = '區間操作：觀望為主'
                    reason = f'箱底${box_bottom:.1f}↔箱頂${box_top:.1f}，區間中段'
                
                return {'action': action, 'reason': reason}
            else:
                return {'action': '區間操作', 'reason': '箱底買、箱頂賣'}
        
        scenario_short_term = {
            'A': {  # 多頭過熱
                'action': '暫停加碼，持股續抱',
                'reason': f'乖離{bias_20:+.1f}%過熱，等拉回再加碼'
            },
            'B': {  # 黃金買點
                'action': '強烈建議買進',
                'reason': f'拉回甜蜜點，盈虧比{rr_ratio:.1f}'
            },
            'B2': {  # 多頭正常
                'action': '可買進',
                'reason': '趨勢向上，順勢操作'
            },
            'C': {  # 空頭超賣
                'action': '勿殺低，可搶反彈',
                'reason': f'乖離{bias_20:+.1f}%超跌，逆勢高風險'
            },
            'D': {  # 空頭確認
                'action': '建議賣出',
                'reason': '空頭趨勢，反彈即出場'
            },
            'X': {  # 待觀察
                'action': '觀望',
                'reason': '等待明確訊號'
            }
        }
        
        return scenario_short_term.get(scenario, {'action': '觀望', 'reason': '無明確訊號'})
    
    @staticmethod
    def _get_mid_term_from_scenario(scenario, decision_vars, result, action_code=''):
        """根據三層引擎 action_code 生成中線建議（與上方裁決同源）。"""
        trend = decision_vars.get('trend_status', 'Range')

        _ac_map = {
            'STRONG_BUY': {'action': '偏多持有', 'reason': '多頭趨勢成立，持股續抱'},
            'BUY':        {'action': '偏多持有', 'reason': '趨勢向上，順勢操作'},
            'HOLD':       {'action': '中線持有 / 觀望', 'reason': '維持部位，留意趨勢變化'},
            'WAIT':       {'action': '中線偏多、等位置', 'reason': '趨勢偏多但等更好進場點'},
            'SKIP':       {'action': '避開', 'reason': '趨勢偏弱，不宜中線佈局'},
            'SELL':       {'action': '減碼 / 出場', 'reason': '趨勢轉弱或觸發賣訊'},
            'TAKE_PROFIT': {'action': '逢高減碼', 'reason': '高檔過熱，分批了結'},
        }
        if action_code in _ac_map:
            return _ac_map[action_code]

        # 舊場景碼相容（三層引擎不產生 E/F，保留以防其他呼叫路徑）
        if scenario == 'E':
            return {'action': '區間操作', 'reason': '盤整格局，高拋低吸'}

        return {'action': '中線觀望', 'reason': '等待趨勢明確'}
    
    @staticmethod
    def _generate_recommendation(result):
        """生成綜合推薦 - v4.1 整合波段分析，消除建議矛盾"""
        tech_signal = result["technical"]["signal"]
        fund_signal = result["fundamental"]["signal"]
        rsi = result["technical"]["rsi"]
        
        chip_signal = "中性"
        if "chip_flow" in result and result["chip_flow"]["available"]:
            chip_signal = result["chip_flow"]["signal"]
        
        # v4.1 新增：取得波段分析結果
        wave = result.get("wave_analysis", {})
        wave_status = wave.get("wave_status", "") if wave.get("available") else ""
        wave_action = wave.get("action_advice", "") if wave.get("available") else ""
        breakout_detected = wave.get("breakout_signal", {}).get("detected", False)
        breakdown_detected = wave.get("breakdown_signal", {}).get("detected", False)
        
        # v4.2 新增：取得均值回歸分析結果
        mr = result.get("mean_reversion", {})
        left_buy_triggered = mr.get("left_buy_signal", {}).get("triggered", False) if mr.get("available") else False
        left_sell_triggered = mr.get("left_sell_signal", {}).get("triggered", False) if mr.get("available") else False
        bias_20 = mr.get("bias_analysis", {}).get("bias_20", 0) if mr.get("available") else 0
        is_overbought = mr.get("bias_analysis", {}).get("is_overbought", False) if mr.get("available") else False
        is_oversold = mr.get("bias_analysis", {}).get("is_oversold", False) if mr.get("available") else False
        
        # 計算綜合評分
        score = 0
        
        # 技術面評分（30%）
        if tech_signal == "偏多":
            score += 30
        elif tech_signal == "中性":
            score += 15
        
        # 基本面評分（30%）
        if fund_signal == "偏多":
            score += 30
        elif fund_signal == "中性":
            score += 15
        
        # 籌碼面評分（40%）
        if chip_signal == "籌碼集中":
            score += 40
        elif chip_signal == "籌碼偏多":
            score += 30
        elif chip_signal in ["籌碼中性", "中性", "籌碼穩定"]:
            score += 20
        elif chip_signal == "籌碼偏空":
            score += 10
        
        # v4.0 新增：成交量異常調整
        volume_analysis = result.get("volume_analysis", {})
        if volume_analysis.get("spike_detected"):
            if volume_analysis.get("spike_action") == "偏多":
                score += 5
            elif volume_analysis.get("spike_action") == "偏空":
                score -= 5
        
        # v4.0 新增：市場環境調整
        market_regime = result.get("market_regime", {})
        if market_regime.get("available"):
            if market_regime.get("trend_direction") == "空頭":
                score -= 10
            elif market_regime.get("trend_direction") == "多頭":
                score += 5
        
        # RSI 調整
        if rsi > 80:
            score -= 10
        elif rsi < 20:
            score += 10
        
        # v4.1 新增：波段分析調整評分
        if breakdown_detected:
            score -= 15  # 三盤跌破，大幅扣分
        
        # v4.2 新增：均值回歸調整評分
        if left_sell_triggered and is_overbought:
            score -= 10  # 嚴重過熱，扣分
        if left_buy_triggered and is_oversold:
            score += 5  # 超跌可能反彈，小幅加分（但風險仍高）
        
        # v4.4.3 新增：限制總分在 0-100 之間 (Clamp score)
        score = max(0, min(100, score))
        
        # ============================================================
        # v4.2 修正：生成一致性的總體建議（整合波段分析 + 均值回歸）
        # ============================================================
        
        # 判斷當前是否適合立即進場
        immediate_entry_ok = True
        wait_reason = ""
        
        # 條件1：RSI 超買需要等待
        if rsi > 70:
            immediate_entry_ok = False
            wait_reason = f"RSI={rsi:.0f}超買"
        
        # 條件2：波段分析建議等拉回
        if "等" in wave_action and ("拉回" in wave_action or "縮量" in wave_action):
            immediate_entry_ok = False
            if wait_reason:
                wait_reason += "，且波段建議等拉回"
            else:
                wait_reason = "波段建議等拉回確認"
        
        # 條件3：三盤跌破
        if breakdown_detected:
            immediate_entry_ok = False
            wait_reason = "三盤跌破，波段結束"
        
        # 條件4 (v4.2新增)：嚴重正乖離
        if is_overbought:
            immediate_entry_ok = False
            if wait_reason:
                wait_reason += f"，乖離率{bias_20:.1f}%過熱"
            else:
                wait_reason = f"乖離率{bias_20:.1f}%嚴重過熱"
        
        # 生成總體建議（考慮是否適合立即進場 + 均值回歸訊號）
        if breakdown_detected:
            # 三盤跌破優先
            overall = "建議出場觀望"
            action_timing = "立即"
        elif left_sell_triggered and is_overbought:
            # v4.2：嚴重過熱，觸發左側賣訊
            overall = "建議積極停利"
            action_timing = "短線過熱，鎖住獲利"
        elif left_buy_triggered and is_oversold:
            # v4.2：嚴重超跌，觸發左側買訊（逆勢操作，高風險）
            if score >= 40:
                overall = "可嘗試搶反彈（高風險）"
                action_timing = "超跌反彈機會，但屬逆勢操作"
            else:
                overall = "超跌但趨勢向下，觀望"
                action_timing = "等止跌訊號確認"
        elif score >= 70:
            if immediate_entry_ok:
                overall = "強烈建議買進"
                action_timing = "可立即進場"
            else:
                overall = "看好，但等拉回再買"
                action_timing = f"等待（{wait_reason}）"
        elif score >= 50:
            if immediate_entry_ok:
                overall = "建議買進"
                action_timing = "可考慮進場"
            else:
                overall = "偏多，等回檔佈局"
                action_timing = f"等待（{wait_reason}）"
        elif score >= 35:
            overall = "建議觀望"
            action_timing = "暫不操作"
        elif score >= 20:
            overall = "建議減碼"
            action_timing = "逢高減碼"
        else:
            overall = "建議賣出"
            action_timing = "儘速離場"
        
        # v4.2 修正：短線建議與總體建議保持一致（加入均值回歸）
        short_term = QuickAnalyzer._get_short_term_recommendation_v42(result, score, wave, mr, immediate_entry_ok, wait_reason)
        mid_term = QuickAnalyzer._get_mid_term_recommendation(result, score)
        long_term = QuickAnalyzer._get_long_term_recommendation(result, score)
        
        return {
            "overall": overall,
            "score": score,
            "action_timing": action_timing,  # v4.1 新增：進場時機說明
            "short_term": short_term,
            "mid_term": mid_term,
            "long_term": long_term
        }
    
    @staticmethod
    def _get_short_term_recommendation_v42(result, score, wave, mr, immediate_entry_ok, wait_reason):
        """v4.2 修正：短線建議整合波段分析 + 均值回歸"""
        rsi = result["technical"]["rsi"]
        chip = result.get("chip_flow", {})
        volume = result.get("volume_analysis", {})
        
        breakdown_detected = wave.get("breakdown_signal", {}).get("detected", False) if wave.get("available") else False
        breakout_detected = wave.get("breakout_signal", {}).get("detected", False) if wave.get("available") else False
        
        # v4.2：取得均值回歸訊號
        left_buy_triggered = mr.get("left_buy_signal", {}).get("triggered", False) if mr.get("available") else False
        left_sell_triggered = mr.get("left_sell_signal", {}).get("triggered", False) if mr.get("available") else False
        is_overbought = mr.get("bias_analysis", {}).get("is_overbought", False) if mr.get("available") else False
        is_oversold = mr.get("bias_analysis", {}).get("is_oversold", False) if mr.get("available") else False
        bias_20 = mr.get("bias_analysis", {}).get("bias_20", 0) if mr.get("available") else 0
        
        # 優先級1：三盤跌破 - 必須出場
        if breakdown_detected:
            return {"action": "建議出場", "reason": "三盤跌破，波段結束"}
        
        # 優先級2 (v4.2新增)：左側賣出訊號 - 積極停利
        if left_sell_triggered and is_overbought:
            return {"action": "建議積極停利", "reason": f"乖離{bias_20:.1f}%過熱，觸發左側賣訊"}
        
        # 優先級3 (v4.2新增)：左側買進訊號 - 超跌反彈
        if left_buy_triggered and is_oversold:
            return {"action": "可嘗試搶反彈（高風險）", "reason": f"乖離{bias_20:.1f}%超跌，屬逆勢操作"}
        
        # 優先級4：三盤突破但需等拉回
        if breakout_detected and not immediate_entry_ok:
            return {"action": "等拉回再進場", "reason": wait_reason}
        
        # 優先級5：三盤突破且可立即進場
        if breakout_detected and immediate_entry_ok:
            strength = wave.get("breakout_signal", {}).get("strength", "")
            if strength == "strong":
                return {"action": "可進場", "reason": "三盤突破（強勢）"}
            else:
                return {"action": "可小量試單", "reason": "三盤突破，等拉回加碼"}
        
        # 優先級6：乖離偏高但未觸發左側賣訊
        if bias_20 > 10:
            return {"action": "短線過熱，不宜追高", "reason": f"乖離{bias_20:.1f}%偏高"}
        
        # 優先級7：爆量判斷
        if volume.get("spike_detected"):
            if volume.get("spike_action") == "偏多":
                return {"action": "爆量買進訊號", "reason": volume.get("spike_signal", "")}
            elif volume.get("spike_action") == "偏空":
                return {"action": "爆量賣出訊號", "reason": volume.get("spike_signal", "")}
        
        # 優先級8：籌碼面（v4.4.1 修正：改用數值驅動，不用中文句子比對）
        if chip.get("available"):
            # 取得數值欄位
            foreign_net = chip.get("foreign_net", 0)
            trust_net = chip.get("trust_net", 0)
            foreign_days = chip.get("foreign_consecutive_days", 0)
            trust_days = chip.get("trust_consecutive_days", 0)
            
            # 同步買超信號：外資投信都買超且連續天數>=2
            is_sync_buy = (foreign_net > 0 and trust_net > 0 and 
                          foreign_days >= 2 and trust_days >= 2)
            # 同步賣超信號：外資投信都賣超且連續天數>=2
            is_sync_sell = (foreign_net < 0 and trust_net < 0 and 
                           abs(foreign_days) >= 2 and abs(trust_days) >= 2)
            
            if is_sync_buy:
                if immediate_entry_ok:
                    return {"action": "建議買進", "reason": f"外資投信同步連續買超（外資連{foreign_days}日，投信連{trust_days}日）"}
                else:
                    return {"action": "等拉回買進", "reason": f"籌碼面佳但{wait_reason}"}
            elif is_sync_sell:
                return {"action": "建議賣出", "reason": f"外資投信同步連續賣超（外資連{abs(foreign_days)}日，投信連{abs(trust_days)}日）"}
        
        # 優先級9：RSI 判斷
        if rsi < 30:
            return {"action": "可考慮買進", "reason": f"RSI={rsi:.0f}超賣區"}
        elif rsi > 70:
            return {"action": "短線過熱，等拉回", "reason": f"RSI={rsi:.0f}超買區"}
        
        # 優先級10：綜合評分
        if score >= 60:
            if immediate_entry_ok:
                return {"action": "短線偏多", "reason": "技術面籌碼面配合良好"}
            else:
                return {"action": "看好但等拉回", "reason": wait_reason}
        elif score <= 30:
            return {"action": "短線偏空", "reason": "技術面籌碼面偏弱"}
        else:
            return {"action": "短線觀望", "reason": "方向不明確"}
    
    @staticmethod
    def _get_mid_term_recommendation(result, score):
        """中線建議"""
        tech = result["technical"]
        chip = result.get("chip_flow", {})
        
        if tech.get("trend") == "上升趨勢":
            if chip.get("signal") in ["籌碼集中", "籌碼偏多"]:
                return {"action": "建議持有", "reason": "趨勢向上且籌碼面支撐"}
            else:
                return {"action": "持有觀察", "reason": "趨勢向上但籌碼面未配合"}
        elif tech.get("trend") == "下降趨勢":
            if chip.get("signal") in ["籌碼分散", "籌碼偏空"]:
                return {"action": "建議減碼", "reason": "趨勢向下且籌碼流出"}
            else:
                return {"action": "觀察反彈", "reason": "趨勢向下但可能有反彈"}
        
        return {"action": "中線觀望", "reason": "等待明確訊號"}
    
    @staticmethod
    def _get_long_term_recommendation(result, score):
        """
        長線建議
        v4.4.2 修正：當 PE 為負值時，改用其他指標判斷
        """
        fund = result["fundamental"]
        
        # v4.0 改進：使用 PE Band 和 Forward PE
        pe_percentile = fund.get("pe_percentile", "N/A")
        forward_pe = fund.get("forward_pe", "N/A")
        trailing_pe = fund.get("trailing_pe", "N/A")
        pb = fund.get("pb", "N/A")
        
        # v4.4.2 修正：檢查 PE 是否為負值（公司虧損）
        pe_is_negative = False
        if forward_pe not in ["N/A", "歷史模式不可用", None] and isinstance(forward_pe, (int, float)):
            if forward_pe < 0:
                pe_is_negative = True
        
        # 如果沒有 Forward PE，檢查 Trailing PE
        if not pe_is_negative and trailing_pe not in ["N/A", None] and isinstance(trailing_pe, (int, float)):
            if trailing_pe < 0:
                pe_is_negative = True
        
        # ============================================================
        # PE 為負值時的處理邏輯（公司虧損）
        # ============================================================
        if pe_is_negative:
            # 嘗試使用 PB（股價淨值比）判斷
            if pb not in ["N/A", None] and isinstance(pb, (int, float)) and pb > 0:
                if pb < 1.0:
                    return {
                        "action": "長線觀察", 
                        "reason": f"公司虧損(PE<0)，但PB={pb:.2f}<1（股價低於淨值），可關注轉機"
                    }
                elif pb < 2.0:
                    return {
                        "action": "長線謹慎", 
                        "reason": f"公司虧損(PE<0)，PB={pb:.2f}，需觀察獲利改善"
                    }
                else:
                    return {
                        "action": "長線避開", 
                        "reason": f"公司虧損(PE<0)且PB={pb:.2f}偏高，估值風險大"
                    }
            
            # 沒有 PB 資料，使用技術面和籌碼面判斷
            chip_signal = "中性"
            if "chip_flow" in result and result["chip_flow"].get("available"):
                chip_signal = result["chip_flow"].get("signal", "中性")
            
            tech_signal = result.get("technical", {}).get("signal", "中性")
            
            # 技術面和籌碼面都偏多，可能有轉機題材
            if tech_signal == "偏多" and chip_signal in ["籌碼集中", "籌碼偏多"]:
                return {
                    "action": "長線觀察", 
                    "reason": "公司虧損(PE<0)，但技術面+籌碼面偏多，可能有轉機題材"
                }
            elif tech_signal == "偏空" or chip_signal in ["籌碼分散", "籌碼偏空"]:
                return {
                    "action": "長線避開", 
                    "reason": "公司虧損(PE<0)，技術面或籌碼面偏空，風險較高"
                }
            else:
                return {
                    "action": "長線謹慎", 
                    "reason": "公司虧損(PE<0)，長線價值需觀察獲利改善"
                }
        
        # ============================================================
        # 正常 PE 判斷邏輯
        # ============================================================
        # 確保 forward_pe 是正數才進行比較
        if forward_pe not in ["N/A", "歷史模式不可用", None] and isinstance(forward_pe, (int, float)):
            if forward_pe > 0:  # v4.4.2 修正：必須是正數
                if forward_pe < 12:
                    return {"action": "長線看好", "reason": f"預估PE={forward_pe:.1f}偏低，具投資價值"}
                elif forward_pe > 25:
                    return {"action": "長線謹慎", "reason": f"預估PE={forward_pe:.1f}偏高，留意風險"}
        
        # 確保 pe_percentile 是數字才進行比較
        if pe_percentile not in ["N/A", None] and isinstance(pe_percentile, (int, float)):
            if pe_percentile < 20:
                return {"action": "長線看好", "reason": f"PE處於歷史{pe_percentile:.0f}%低檔"}
            elif pe_percentile > 80:
                return {"action": "長線謹慎", "reason": f"PE處於歷史{pe_percentile:.0f}%高檔"}
        
        if score >= 60:
            return {"action": "長線持有", "reason": "整體面向正面"}
        elif score <= 30:
            return {"action": "長線觀望", "reason": "整體面向偏弱"}
        else:
            return {"action": "長線中性", "reason": "維持現有部位"}

