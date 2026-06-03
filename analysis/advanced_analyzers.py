"""
advanced_analyzers.py - 進階量化分析功能

================================================================================
版本: v4.5.17
用途: 高盛級進階分析功能，包含 VCP Scanner、相對強度等

================================================================================
功能清單:
================================================================================

1. VCP Scanner (Volatility Contraction Pattern)
   - 偵測波動率收斂形態
   - Mark Minervini 風格的突破前兆識別

2. Relative Strength (RS) Calculator
   - 計算個股相對大盤的強度
   - 識別 Market Leader

3. ATR-Based Stop Loss Calculator
   - 動態停損計算（基於 ATR）
   - 解決固定百分比停損的問題

4. Enhanced Risk Manager
   - 整合 ATR 停損
   - 動態倉位計算

================================================================================
"""

import numpy as np
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from datetime import datetime

# ============================================================================
# 嘗試導入依賴
# ============================================================================
try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False
    print("[AdvancedAnalyzers] 警告：pandas 未安裝")


# ============================================================================
# 數據類別
# ============================================================================

@dataclass
class VCPResult:
    """VCP 偵測結果"""
    detected: bool = False                      # 是否偵測到 VCP
    contraction_count: int = 0                  # 收斂次數
    contractions: List[float] = field(default_factory=list)  # 各次收斂幅度
    current_range_pct: float = 0.0              # 當前振幅
    pivot_price: float = 0.0                    # 突破點位
    status: str = 'NOT_FOUND'                   # 狀態
    description: str = ''                       # 描述
    score_impact: int = 0                       # 對量化評分的影響


@dataclass
class RSResult:
    """相對強度結果"""
    rs_value: float = 0.0                       # RS 數值
    rs_percentile: float = 0.0                  # RS 百分位
    rs_new_high: bool = False                   # RS 是否創新高
    market_new_high: bool = False               # 大盤是否創新高
    is_market_leader: bool = False              # 是否為 Market Leader
    relative_performance_20d: float = 0.0       # 20日相對績效
    relative_performance_60d: float = 0.0       # 60日相對績效
    description: str = ''                       # 描述


@dataclass
class ATRStopResult:
    """ATR 動態停損結果"""
    atr_value: float = 0.0                      # ATR 數值
    atr_percent: float = 0.0                    # ATR 佔股價百分比
    stop_loss_price: float = 0.0                # 停損價格
    stop_loss_percent: float = 0.0              # 停損幅度
    risk_per_share: float = 0.0                 # 每股風險
    suggested_position_size: int = 0            # 建議部位大小
    description: str = ''                       # 描述


# ============================================================================
# VCP Scanner (Volatility Contraction Pattern)
# ============================================================================

class VCPScanner:
    """
    波動率壓縮偵測器 (Volatility Contraction Pattern Scanner)
    
    =====================================================
    理論基礎 (Mark Minervini SEPA 方法):
    =====================================================
    
    VCP 是一種價格整理形態，特徵是：
    1. 股價在一段上漲後進入整理
    2. 整理期間的振幅逐次收斂（如 15% → 8% → 4%）
    3. 成交量同步萎縮
    4. 當股價突破整理區間高點時，往往展開新一波漲勢
    
    =====================================================
    判斷標準:
    =====================================================
    
    1. 至少 2-3 次收斂（T1 → T2 → T3）
    2. 每次收斂幅度約為前一次的 40-60%
    3. 最後一次收斂幅度 < 5%
    4. 股價維持在 MA50 之上（健康整理）
    5. 成交量在整理末期萎縮至 50% 以下
    
    =====================================================
    使用範例:
    =====================================================
    
    ```python
    scanner = VCPScanner()
    result = scanner.detect(df)
    
    if result.detected:
        print(f"VCP 偵測！突破點位: {result.pivot_price}")
        print(f"收斂次數: {result.contraction_count}")
        print(f"收斂幅度: {result.contractions}")
    ```
    """
    
    def __init__(
        self,
        min_contractions: int = 2,
        max_contractions: int = 5,
        contraction_ratio: float = 0.6,
        final_range_threshold: float = 0.05,
        lookback_days: int = 60
    ):
        """
        初始化 VCP Scanner
        
        Args:
            min_contractions: 最少收斂次數
            max_contractions: 最多收斂次數
            contraction_ratio: 收斂比例閾值（每次應縮小到前次的多少）
            final_range_threshold: 最終振幅閾值（<5% 視為準備突破）
            lookback_days: 回看天數
        """
        self.min_contractions = min_contractions
        self.max_contractions = max_contractions
        self.contraction_ratio = contraction_ratio
        self.final_range_threshold = final_range_threshold
        self.lookback_days = lookback_days
    
    def detect(self, df: 'pd.DataFrame') -> VCPResult:
        """
        偵測 VCP 形態
        
        Args:
            df: DataFrame，需包含 High, Low, Close, Volume
        
        Returns:
            VCPResult: 偵測結果
        """
        if not PANDAS_AVAILABLE or df is None or len(df) < self.lookback_days:
            return VCPResult(
                status='DATA_INSUFFICIENT',
                description='數據不足，無法分析 VCP'
            )
        
        try:
            # 取得最近 N 天數據
            df_recent = df.tail(self.lookback_days).copy()
            
            # ========================================
            # Step 1: 找出整理區間的高低點
            # ========================================
            window = 10  # 用 10 天為一個單位找極值
            contractions = []
            
            for i in range(0, len(df_recent) - window, window // 2):
                end_idx = min(i + window, len(df_recent))
                segment = df_recent.iloc[i:end_idx]
                
                high = segment['High'].max()
                low = segment['Low'].min()
                range_pct = (high - low) / low * 100 if low > 0 else 0
                
                contractions.append({
                    'start_idx': i,
                    'end_idx': end_idx,
                    'high': high,
                    'low': low,
                    'range_pct': range_pct
                })
            
            if len(contractions) < self.min_contractions:
                return VCPResult(
                    status='INSUFFICIENT_DATA',
                    description='整理區間數據不足'
                )
            
            # ========================================
            # Step 2: 檢查是否呈現收斂趨勢
            # ========================================
            valid_contractions = []
            prev_range = None
            
            for c in contractions[-self.max_contractions:]:
                if prev_range is not None:
                    # 檢查是否收斂（當前振幅 < 前一次的 contraction_ratio）
                    if c['range_pct'] < prev_range * self.contraction_ratio:
                        valid_contractions.append(c['range_pct'])
                
                prev_range = c['range_pct']
            
            # ========================================
            # Step 3: 判斷是否為有效 VCP
            # ========================================
            current_range = contractions[-1]['range_pct']
            is_vcp = (
                len(valid_contractions) >= self.min_contractions - 1 and
                current_range <= self.final_range_threshold * 100
            )
            
            # 計算突破點位（最近整理區間的高點）
            recent_high = df_recent['High'].iloc[-20:].max()
            
            # ========================================
            # Step 4: 檢查成交量是否萎縮
            # ========================================
            vol_ma20 = df_recent['Volume'].rolling(20).mean()
            recent_vol = df_recent['Volume'].iloc[-5:].mean()
            vol_contraction = recent_vol < vol_ma20.iloc[-1] * 0.7 if len(vol_ma20) > 0 else False
            
            # ========================================
            # Step 5: 檢查是否在 MA50 之上（健康整理）
            # ========================================
            ma50 = df_recent['Close'].rolling(50).mean()
            above_ma50 = df_recent['Close'].iloc[-1] > ma50.iloc[-1] if len(ma50) >= 50 else False
            
            # ========================================
            # 生成結果
            # ========================================
            if is_vcp:
                status = 'VCP_READY' if vol_contraction and above_ma50 else 'VCP_FORMING'
                score_impact = 15 if status == 'VCP_READY' else 8
                description = (
                    f"VCP形態{'確認' if status == 'VCP_READY' else '形成中'}！"
                    f"振幅收斂 {len(valid_contractions)+1} 次，"
                    f"當前振幅 {current_range:.1f}%，"
                    f"突破點位 ${recent_high:.2f}"
                )
                if vol_contraction:
                    description += "，量能萎縮"
                if above_ma50:
                    description += "，站穩50MA"
            else:
                status = 'NOT_FOUND'
                score_impact = 0
                description = f"未偵測到 VCP，當前振幅 {current_range:.1f}%"
            
            return VCPResult(
                detected=is_vcp,
                contraction_count=len(valid_contractions) + 1,
                contractions=[c['range_pct'] for c in contractions[-self.max_contractions:]],
                current_range_pct=current_range,
                pivot_price=recent_high,
                status=status,
                description=description,
                score_impact=score_impact
            )
            
        except Exception as e:
            return VCPResult(
                status='ERROR',
                description=f'VCP 分析錯誤: {str(e)}'
            )
    
    @staticmethod
    def analyze(df: 'pd.DataFrame') -> VCPResult:
        """靜態方法（便捷介面）"""
        scanner = VCPScanner()
        return scanner.detect(df)


# ============================================================================
# Relative Strength (RS) Calculator
# ============================================================================

class RelativeStrengthCalculator:
    """
    相對強度計算器 (Relative Strength Calculator)
    
    =====================================================
    理論基礎 (William O'Neil CANSLIM):
    =====================================================
    
    相對強度 (RS) 衡量個股相對於大盤的表現：
    - RS > 1.0：個股表現優於大盤
    - RS < 1.0：個股表現落後大盤
    - RS 創新高但大盤未創新高：Market Leader 特徵
    
    =====================================================
    計算公式:
    =====================================================
    
    RS = (個股漲幅 / 大盤漲幅) * 100
    
    或使用相對價格線：
    RS Line = 個股價格 / 大盤指數
    
    =====================================================
    使用範例:
    =====================================================
    
    ```python
    calculator = RelativeStrengthCalculator()
    result = calculator.calculate(stock_df, market_df)
    
    if result.is_market_leader:
        print("Market Leader detected!")
    ```
    """
    
    def __init__(self, lookback_days: int = 60):
        """
        初始化
        
        Args:
            lookback_days: 回看天數
        """
        self.lookback_days = lookback_days
    
    def calculate(
        self, 
        stock_df: 'pd.DataFrame', 
        market_df: 'pd.DataFrame' = None,
        market_symbol: str = '0050.TW'
    ) -> RSResult:
        """
        計算相對強度
        
        Args:
            stock_df: 個股 DataFrame
            market_df: 大盤 DataFrame（可選）
            market_symbol: 大盤代碼（若未提供 market_df）
        
        Returns:
            RSResult: 相對強度結果
        """
        if not PANDAS_AVAILABLE or stock_df is None or len(stock_df) < 20:
            return RSResult(description='數據不足')
        
        try:
            # ========================================
            # Step 1: 取得大盤數據
            # ========================================
            if market_df is None:
                market_df = self._fetch_market_data(market_symbol)
            
            if market_df is None or len(market_df) < 20:
                # 無法取得大盤數據，使用簡化計算
                return self._calculate_simple_rs(stock_df)
            
            # ========================================
            # Step 2: 對齊數據
            # ========================================
            # 確保兩個 DataFrame 有相同的日期索引
            common_dates = stock_df.index.intersection(market_df.index)
            
            if len(common_dates) < 20:
                return self._calculate_simple_rs(stock_df)
            
            stock_aligned = stock_df.loc[common_dates]
            market_aligned = market_df.loc[common_dates]
            
            # ========================================
            # Step 3: 計算 RS Line
            # ========================================
            rs_line = stock_aligned['Close'] / market_aligned['Close']
            rs_line = rs_line / rs_line.iloc[0] * 100  # 標準化到 100
            
            # ========================================
            # Step 4: 判斷 RS 是否創新高
            # ========================================
            rs_value = rs_line.iloc[-1]
            rs_20d_high = rs_line.tail(20).max()
            rs_60d_high = rs_line.tail(min(60, len(rs_line))).max()
            
            rs_new_high = rs_value >= rs_60d_high * 0.98  # 接近新高
            
            # ========================================
            # Step 5: 判斷大盤是否創新高
            # ========================================
            market_price = market_aligned['Close'].iloc[-1]
            market_60d_high = market_aligned['Close'].tail(60).max()
            market_new_high = market_price >= market_60d_high * 0.98
            
            # ========================================
            # Step 6: 判斷是否為 Market Leader
            # ========================================
            is_market_leader = rs_new_high and not market_new_high
            
            # ========================================
            # Step 7: 計算相對績效
            # ========================================
            stock_20d_return = (stock_aligned['Close'].iloc[-1] / stock_aligned['Close'].iloc[-20] - 1) * 100
            market_20d_return = (market_aligned['Close'].iloc[-1] / market_aligned['Close'].iloc[-20] - 1) * 100
            relative_perf_20d = stock_20d_return - market_20d_return
            
            stock_60d_return = (stock_aligned['Close'].iloc[-1] / stock_aligned['Close'].iloc[-min(60, len(stock_aligned))] - 1) * 100
            market_60d_return = (market_aligned['Close'].iloc[-1] / market_aligned['Close'].iloc[-min(60, len(market_aligned))] - 1) * 100
            relative_perf_60d = stock_60d_return - market_60d_return
            
            # ========================================
            # Step 8: 計算 RS 百分位
            # ========================================
            rs_percentile = (rs_line.rank(pct=True).iloc[-1]) * 100
            
            # ========================================
            # 生成描述
            # ========================================
            if is_market_leader:
                description = f"🏆 Market Leader！RS創新高但大盤未創新高，相對強度極強"
            elif rs_new_high:
                description = f"RS創新高，表現優於大盤，20日相對績效 {relative_perf_20d:+.1f}%"
            elif relative_perf_20d > 0:
                description = f"跑贏大盤，20日相對績效 {relative_perf_20d:+.1f}%"
            else:
                description = f"落後大盤，20日相對績效 {relative_perf_20d:+.1f}%"
            
            return RSResult(
                rs_value=rs_value,
                rs_percentile=rs_percentile,
                rs_new_high=rs_new_high,
                market_new_high=market_new_high,
                is_market_leader=is_market_leader,
                relative_performance_20d=relative_perf_20d,
                relative_performance_60d=relative_perf_60d,
                description=description
            )
            
        except Exception as e:
            return RSResult(description=f'RS 計算錯誤: {str(e)}')
    
    def _calculate_simple_rs(self, stock_df: 'pd.DataFrame') -> RSResult:
        """
        簡化版 RS 計算（當無法取得大盤數據時）
        
        使用股票自身的表現作為參考
        """
        try:
            close = stock_df['Close']
            
            # 計算動能
            return_20d = (close.iloc[-1] / close.iloc[-20] - 1) * 100 if len(close) >= 20 else 0
            return_60d = (close.iloc[-1] / close.iloc[-min(60, len(close))] - 1) * 100
            
            # 判斷是否創新高
            current = close.iloc[-1]
            high_60d = close.tail(60).max()
            is_new_high = current >= high_60d * 0.98
            
            description = f"20日績效 {return_20d:+.1f}%，60日績效 {return_60d:+.1f}%"
            if is_new_high:
                description += "（創新高）"
            
            return RSResult(
                rs_value=100 + return_60d,
                relative_performance_20d=return_20d,
                relative_performance_60d=return_60d,
                rs_new_high=is_new_high,
                description=description
            )
            
        except Exception as e:
            return RSResult(description=f'簡化 RS 計算錯誤: {str(e)}')
    
    def _fetch_market_data(self, symbol: str) -> Optional['pd.DataFrame']:
        """
        取得大盤數據
        """
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            return ticker.history(period='6mo')
        except Exception:
            return None
    
    @staticmethod
    def analyze(
        stock_df: 'pd.DataFrame', 
        market_df: 'pd.DataFrame' = None
    ) -> RSResult:
        """靜態方法（便捷介面）"""
        calculator = RelativeStrengthCalculator()
        return calculator.calculate(stock_df, market_df)


# ============================================================================
# ATR-Based Stop Loss Calculator
# ============================================================================

class ATRStopLossCalculator:
    """
    ATR 動態停損計算器
    
    =====================================================
    問題背景:
    =====================================================
    
    固定百分比停損的問題：
    - 低波動股票（如電信股）：5% 停損太寬，容易被洗出
    - 高波動股票（如生技股）：5% 停損太窄，正常波動就觸發
    
    =====================================================
    解決方案:
    =====================================================
    
    使用 ATR (Average True Range) 動態調整停損：
    
    Stop Loss = Entry Price - (ATR × Multiplier)
    
    常用設定：
    - 激進：1.5 × ATR
    - 標準：2.0 × ATR
    - 保守：3.0 × ATR
    
    =====================================================
    使用範例:
    =====================================================
    
    ```python
    calculator = ATRStopLossCalculator(multiplier=2.0)
    result = calculator.calculate(df, entry_price=100.0)
    
    print(f"停損價: {result.stop_loss_price}")
    print(f"風險: {result.stop_loss_percent}%")
    ```
    """
    
    def __init__(
        self,
        atr_period: int = 14,
        multiplier: float = 2.0,
        max_risk_pct: float = 0.10  # 最大風險 10%
    ):
        """
        初始化
        
        Args:
            atr_period: ATR 計算週期
            multiplier: ATR 乘數
            max_risk_pct: 最大風險百分比
        """
        self.atr_period = atr_period
        self.multiplier = multiplier
        self.max_risk_pct = max_risk_pct
    
    def calculate(
        self,
        df: 'pd.DataFrame',
        entry_price: float = None,
        position_risk: float = 10000  # 願意承受的金額風險
    ) -> ATRStopResult:
        """
        計算動態停損
        
        Args:
            df: DataFrame，需包含 High, Low, Close
            entry_price: 進場價格（預設使用最新收盤價）
            position_risk: 願意承受的金額風險
        
        Returns:
            ATRStopResult: 停損計算結果
        """
        if not PANDAS_AVAILABLE or df is None or len(df) < self.atr_period + 1:
            return ATRStopResult(description='數據不足')
        
        try:
            # 使用最新收盤價作為進場價
            if entry_price is None:
                entry_price = df['Close'].iloc[-1]
            
            # ========================================
            # Step 1: 計算 ATR
            # ========================================
            high = df['High']
            low = df['Low']
            close = df['Close']
            
            # True Range
            tr1 = high - low
            tr2 = abs(high - close.shift(1))
            tr3 = abs(low - close.shift(1))
            
            true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr = true_range.rolling(self.atr_period).mean().iloc[-1]
            
            # ATR 佔股價的百分比
            atr_percent = atr / entry_price * 100
            
            # ========================================
            # Step 2: 計算停損價格
            # ========================================
            stop_distance = atr * self.multiplier
            stop_loss_price = entry_price - stop_distance
            stop_loss_percent = stop_distance / entry_price * 100
            
            # 確保不超過最大風險
            if stop_loss_percent > self.max_risk_pct * 100:
                stop_loss_percent = self.max_risk_pct * 100
                stop_loss_price = entry_price * (1 - self.max_risk_pct)
            
            # ========================================
            # Step 3: 計算建議部位大小
            # ========================================
            risk_per_share = entry_price - stop_loss_price
            suggested_position = int(position_risk / risk_per_share) if risk_per_share > 0 else 0
            
            # ========================================
            # 生成描述
            # ========================================
            description = (
                f"ATR(14) = ${atr:.2f} ({atr_percent:.1f}%)，"
                f"停損價 ${stop_loss_price:.2f} (跌 {stop_loss_percent:.1f}%)，"
                f"建議部位 {suggested_position} 股"
            )
            
            return ATRStopResult(
                atr_value=atr,
                atr_percent=atr_percent,
                stop_loss_price=stop_loss_price,
                stop_loss_percent=stop_loss_percent,
                risk_per_share=risk_per_share,
                suggested_position_size=suggested_position,
                description=description
            )
            
        except Exception as e:
            return ATRStopResult(description=f'ATR 計算錯誤: {str(e)}')
    
    @staticmethod
    def analyze(df: 'pd.DataFrame', entry_price: float = None) -> ATRStopResult:
        """靜態方法（便捷介面）"""
        calculator = ATRStopLossCalculator()
        return calculator.calculate(df, entry_price)


# ============================================================================
# 邏輯檢查與修正
# ============================================================================

class LogicAudit:
    """
    邏輯檢查工具
    
    用於驗證現有分析邏輯的正確性
    """
    
    @staticmethod
    def check_scenario_c_cap(decision_result: Dict) -> Dict:
        """
        檢查 Scenario C (空頭反彈) 的評分上限
        
        規則：空頭市場評分不應超過 70 分
        """
        score = decision_result.get('score', 0)
        scenario = decision_result.get('scenario', '')
        
        if 'C' in str(scenario) or '空頭' in str(scenario):
            if score > 70:
                return {
                    'issue': 'SCORE_CAP_EXCEEDED',
                    'original_score': score,
                    'capped_score': 70,
                    'message': f'空頭反彈場景評分 {score} 超過上限 70，已自動調整'
                }
        
        return {'issue': None}
    
    @staticmethod
    def check_pattern_time_span(pattern_result: Dict, min_days: int = 10) -> Dict:
        """
        檢查形態的時間跨度
        
        規則：W底/M頭的兩腳間隔不應小於 10 個交易日
        """
        pattern_type = pattern_result.get('pattern_name', '')
        key_points = pattern_result.get('key_points', {})
        
        if pattern_type in ['W底', 'M頭', 'Double Bottom', 'Double Top']:
            # 嘗試取得兩腳的索引
            left_idx = key_points.get('left_foot', {}).get('index', 0) or \
                       key_points.get('left_peak', {}).get('index', 0)
            right_idx = key_points.get('right_foot', {}).get('index', 0) or \
                        key_points.get('right_peak', {}).get('index', 0)
            
            if left_idx and right_idx:
                span = abs(right_idx - left_idx)
                if span < min_days:
                    return {
                        'issue': 'TIME_SPAN_TOO_SHORT',
                        'span_days': span,
                        'min_required': min_days,
                        'message': f'{pattern_type} 兩腳間隔 {span} 天，少於最低要求 {min_days} 天，可能為雜訊'
                    }
        
        return {'issue': None}
    
    @staticmethod
    def check_stop_loss_method(risk_result: Dict) -> Dict:
        """
        檢查停損計算方法
        
        規則：應使用 ATR 動態停損，而非固定百分比
        """
        stop_method = risk_result.get('stop_loss_method', 'fixed')
        
        if stop_method == 'fixed':
            return {
                'issue': 'FIXED_STOP_LOSS',
                'message': '正在使用固定百分比停損，建議改用 ATR 動態停損',
                'recommendation': 'Entry - 2 * ATR'
            }
        
        return {'issue': None}


# ============================================================================
# 整合介面
# ============================================================================

class AdvancedAnalyzer:
    """
    進階分析器（整合介面）
    
    提供所有進階分析功能的統一介面
    """
    
    def __init__(self):
        self.vcp_scanner = VCPScanner()
        self.rs_calculator = RelativeStrengthCalculator()
        self.atr_calculator = ATRStopLossCalculator()
    
    def full_analysis(
        self,
        df: 'pd.DataFrame',
        market_df: 'pd.DataFrame' = None,
        entry_price: float = None
    ) -> Dict[str, Any]:
        """
        執行完整進階分析
        
        Args:
            df: 個股 DataFrame
            market_df: 大盤 DataFrame（可選）
            entry_price: 進場價格（可選）
        
        Returns:
            dict: 包含所有分析結果
        """
        results = {}
        
        # VCP 分析
        results['vcp'] = self.vcp_scanner.detect(df)
        
        # RS 分析
        results['rs'] = self.rs_calculator.calculate(df, market_df)
        
        # ATR 停損
        results['atr_stop'] = self.atr_calculator.calculate(df, entry_price)
        
        # 彙總評分影響
        score_impact = 0
        score_impact += results['vcp'].score_impact
        
        if results['rs'].is_market_leader:
            score_impact += 10
        elif results['rs'].relative_performance_20d > 5:
            score_impact += 5
        
        results['total_score_impact'] = score_impact
        
        # 彙總描述
        summaries = []
        if results['vcp'].detected:
            summaries.append(f"VCP: {results['vcp'].description}")
        if results['rs'].is_market_leader:
            summaries.append(f"RS: {results['rs'].description}")
        summaries.append(f"停損: {results['atr_stop'].description}")
        
        results['summary'] = ' | '.join(summaries)
        
        return results


# ============================================================================
# 測試
# ============================================================================

if __name__ == '__main__':
    print("=" * 70)
    print("  進階分析功能測試")
    print("=" * 70)
    
    # 創建測試數據
    if PANDAS_AVAILABLE:
        import numpy as np
        
        dates = pd.date_range('2024-01-01', periods=100)
        
        # 模擬 VCP 形態的數據
        base_price = 100
        prices = [base_price]
        
        for i in range(99):
            # 模擬收斂的價格波動
            volatility = max(0.02, 0.10 - i * 0.001)
            change = np.random.normal(0.001, volatility)
            prices.append(prices[-1] * (1 + change))
        
        df_test = pd.DataFrame({
            'Open': prices,
            'High': [p * 1.01 for p in prices],
            'Low': [p * 0.99 for p in prices],
            'Close': prices,
            'Volume': [1000000 - i * 5000 for i in range(100)]
        }, index=dates)
        
        # 測試 VCP
        print("\n1. VCP Scanner 測試")
        print("-" * 40)
        vcp_result = VCPScanner.analyze(df_test)
        print(f"   偵測結果: {vcp_result.status}")
        print(f"   描述: {vcp_result.description}")
        
        # 測試 RS
        print("\n2. Relative Strength 測試")
        print("-" * 40)
        rs_result = RelativeStrengthCalculator.analyze(df_test)
        print(f"   RS 值: {rs_result.rs_value:.2f}")
        print(f"   描述: {rs_result.description}")
        
        # 測試 ATR 停損
        print("\n3. ATR Stop Loss 測試")
        print("-" * 40)
        atr_result = ATRStopLossCalculator.analyze(df_test, entry_price=100.0)
        print(f"   ATR 值: ${atr_result.atr_value:.2f}")
        print(f"   停損價: ${atr_result.stop_loss_price:.2f}")
        print(f"   風險: {atr_result.stop_loss_percent:.1f}%")
        
        # 整合測試
        print("\n4. 完整進階分析")
        print("-" * 40)
        analyzer = AdvancedAnalyzer()
        full_results = analyzer.full_analysis(df_test, entry_price=100.0)
        print(f"   評分影響: +{full_results['total_score_impact']}")
        print(f"   摘要: {full_results['summary']}")
    else:
        print("pandas 未安裝，無法執行測試")
