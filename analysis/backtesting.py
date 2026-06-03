"""
backtesting.py - Backtesting Engine Module
"""

import datetime
import sqlite3
import json
import threading
import time
import hashlib
import warnings
import sys
import os

warnings.filterwarnings('ignore', category=FutureWarning)

import yfinance as yf
import mplfinance as mpf
import pandas as pd
import numpy as np
from scipy.stats import linregress, percentileofscore
import twstock

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from matplotlib.figure import Figure

from io import StringIO
import requests
from bs4 import BeautifulSoup

# v4.4.4 新增：從 config 導入 QuantConfig
from config import QuantConfig

# ============================================================================
# v4.0 改進：增強版回測引擎
# ============================================================================

class BacktestEngine:
    """回測引擎 v4.4.4 - 整合 QuantConfig 成本模型"""
    
    @staticmethod
    def get_risk_free_rate():
        """動態取得無風險利率（嘗試抓取10年期美債收益率）"""
        try:
            tnx = yf.Ticker("^TNX")  # 10-Year Treasury Yield
            hist = tnx.history(period="5d")
            if not hist.empty:
                return hist['Close'].iloc[-1] / 100  # 轉換為小數
        except:
            pass
        return QuantConfig.RISK_FREE_RATE
    
    @staticmethod
    def calculate_total_cost(slippage_pct=0.3):
        """
        v4.4.4 新增：計算總交易成本
        
        台股成本結構：
        - 手續費（雙邊）：0.1425% × 2 = 0.285%
        - 交易稅（賣出）：0.3%
        - 滑價（預設）：0.3%
        
        總計約：0.885%（單次來回）
        
        Args:
            slippage_pct: 滑價百分比（預設 0.3%）
        
        Returns:
            float: 總交易成本（小數形式）
        """
        if QuantConfig.ENABLE_COST_MODEL:
            # 手續費（買進 + 賣出）
            commission_cost = QuantConfig.COMMISSION_RATE * 2
            # 交易稅（僅賣出）
            tax_cost = QuantConfig.TAX_RATE
            # 滑價
            slippage_cost = slippage_pct / 100
            
            total_cost = commission_cost + tax_cost + slippage_cost
            return total_cost
        else:
            # 不啟用成本模型時，僅計算滑價
            return slippage_pct / 100
    
    @staticmethod
    def backtest_trend_strategy(df, fast_ma=5, slow_ma=20, slippage_pct=0.3):
        """趨勢策略回測（均線交叉）- v4.4.4 整合成本模型"""
        df = df.copy()
        df['MA_Fast'] = df['Close'].rolling(window=fast_ma).mean()
        df['MA_Slow'] = df['Close'].rolling(window=slow_ma).mean()
        
        df['Signal'] = 0
        df.loc[df['MA_Fast'] > df['MA_Slow'], 'Signal'] = 1
        df.loc[df['MA_Fast'] < df['MA_Slow'], 'Signal'] = -1
        
        df['Position'] = df['Signal'].shift(1)
        
        # v4.4.4 Fix: 使用完整成本模型
        total_cost = BacktestEngine.calculate_total_cost(slippage_pct)
        
        df['Next_Open'] = df['Open'].shift(-1)
        df['Returns'] = df['Next_Open'].pct_change(fill_method=None)
        df['Strategy_Returns'] = df['Position'] * df['Returns']
        
        # v4.4.4 Fix: 交易時扣除完整成本（包含手續費、稅、滑價）
        df.loc[df['Position'] != df['Position'].shift(1), 'Strategy_Returns'] -= total_cost
        
        return BacktestEngine._calculate_metrics(df)
    
    @staticmethod
    def backtest_momentum_strategy(df, period=14, slippage_pct=0.3):
        """動能策略回測（RSI）- v4.4.4 整合成本模型"""
        df = df.copy()
        
        delta = df['Close'].diff()
        gain = delta.clip(lower=0).rolling(window=period).mean()
        loss = (-delta).clip(lower=0).rolling(window=period).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))
        
        df['Signal'] = 0
        df.loc[df['RSI'] < 30, 'Signal'] = 1
        df.loc[df['RSI'] > 70, 'Signal'] = -1
        
        df['Position'] = df['Signal'].shift(1)
        
        # v4.4.4 Fix: 使用完整成本模型
        total_cost = BacktestEngine.calculate_total_cost(slippage_pct)
        
        df['Next_Open'] = df['Open'].shift(-1)
        df['Returns'] = df['Next_Open'].pct_change(fill_method=None)
        df['Strategy_Returns'] = df['Position'] * df['Returns']
        
        # v4.4.4 Fix: 交易時扣除完整成本
        df.loc[df['Position'] != df['Position'].shift(1), 'Strategy_Returns'] -= total_cost
        
        return BacktestEngine._calculate_metrics(df)
    
    @staticmethod
    def backtest_channel_strategy(df, window=20, num_std=2, slippage_pct=0.3):
        """通道策略回測（布林通道）- v4.4.4 整合成本模型"""
        df = df.copy()
        
        df['MA'] = df['Close'].rolling(window=window).mean()
        df['STD'] = df['Close'].rolling(window=window).std()
        df['Upper'] = df['MA'] + (num_std * df['STD'])
        df['Lower'] = df['MA'] - (num_std * df['STD'])
        
        df['Signal'] = 0
        df.loc[df['Close'] < df['Lower'], 'Signal'] = 1
        df.loc[df['Close'] > df['Upper'], 'Signal'] = -1
        
        df['Position'] = df['Signal'].shift(1)
        
        # v4.4.4 Fix: 使用完整成本模型
        total_cost = BacktestEngine.calculate_total_cost(slippage_pct)
        
        df['Next_Open'] = df['Open'].shift(-1)
        df['Returns'] = df['Next_Open'].pct_change(fill_method=None)
        df['Strategy_Returns'] = df['Position'] * df['Returns']
        
        # v4.4.4 Fix: 交易時扣除完整成本
        df.loc[df['Position'] != df['Position'].shift(1), 'Strategy_Returns'] -= total_cost
        
        return BacktestEngine._calculate_metrics(df)
    
    @staticmethod
    def backtest_mean_reversion_strategy(df, window=20, entry_z=2, exit_z=0.5, slippage_pct=0.3):
        """均值回歸策略回測 - v4.4.4 整合成本模型"""
        df = df.copy()
        
        df['MA'] = df['Close'].rolling(window=window).mean()
        df['STD'] = df['Close'].rolling(window=window).std()
        df['Z_Score'] = (df['Close'] - df['MA']) / df['STD']
        
        df['Signal'] = 0
        df['Position'] = 0
        
        current_position = 0
        for i in range(1, len(df)):
            z = df['Z_Score'].iloc[i]
            
            if current_position == 0:
                if z < -entry_z:
                    df.loc[df.index[i], 'Signal'] = 1
                    current_position = 1
                elif z > entry_z:
                    df.loc[df.index[i], 'Signal'] = -1
                    current_position = -1
            elif current_position == 1:
                if z > -exit_z:
                    df.loc[df.index[i], 'Signal'] = 0
                    current_position = 0
                elif z > entry_z:
                    df.loc[df.index[i], 'Signal'] = -1
                    current_position = -1
            elif current_position == -1:
                if z < exit_z:
                    df.loc[df.index[i], 'Signal'] = 0
                    current_position = 0
                elif z < -entry_z:
                    df.loc[df.index[i], 'Signal'] = 1
                    current_position = 1
            
            df.loc[df.index[i], 'Position'] = current_position
        
        df['Position'] = df['Position'].shift(1)
        
        # v4.4.4 Fix: 使用完整成本模型
        total_cost = BacktestEngine.calculate_total_cost(slippage_pct)
        
        df['Next_Open'] = df['Open'].shift(-1)
        df['Returns'] = df['Next_Open'].pct_change(fill_method=None)
        df['Strategy_Returns'] = df['Position'] * df['Returns']
        
        # v4.4.4 Fix: 交易時扣除完整成本
        df.loc[df['Position'] != df['Position'].shift(1), 'Strategy_Returns'] -= total_cost
        
        return BacktestEngine._calculate_metrics(df)
    
    @staticmethod
    def _calculate_metrics(df):
        """計算績效指標 - v4.0 修正 Sharpe Ratio + 增加淨值曲線"""
        df = df.dropna()
        
        if len(df) == 0:
            return None
        
        # 累積報酬
        total_return = (1 + df['Strategy_Returns']).prod() - 1
        buy_hold_return = (df['Close'].iloc[-1] / df['Close'].iloc[0]) - 1
        
        # 年化報酬
        days = (df.index[-1] - df.index[0]).days
        annual_return = (1 + total_return) ** (365 / days) - 1 if days > 0 else 0
        
        # v4.0 修正：Sharpe Ratio 扣除無風險利率
        risk_free_rate = BacktestEngine.get_risk_free_rate()
        daily_rf = risk_free_rate / 252  # 日無風險利率
        
        excess_returns = df['Strategy_Returns'] - daily_rf
        if df['Strategy_Returns'].std() > 0:
            sharpe_ratio = excess_returns.mean() / df['Strategy_Returns'].std() * np.sqrt(252)
        else:
            sharpe_ratio = 0
        
        # 最大回撤
        cumulative = (1 + df['Strategy_Returns']).cumprod()
        running_max = cumulative.expanding().max()
        drawdown = (cumulative - running_max) / running_max
        max_drawdown = drawdown.min()
        
        # 勝率
        winning_trades = (df['Strategy_Returns'] > 0).sum()
        total_trades = (df['Strategy_Returns'] != 0).sum()
        win_rate = winning_trades / total_trades if total_trades > 0 else 0
        
        # v4.0 新增：計算 Sortino Ratio（只考慮下行風險）
        downside_returns = df['Strategy_Returns'][df['Strategy_Returns'] < 0]
        if len(downside_returns) > 0 and downside_returns.std() > 0:
            sortino_ratio = (df['Strategy_Returns'].mean() - daily_rf) / downside_returns.std() * np.sqrt(252)
        else:
            sortino_ratio = sharpe_ratio
        
        # v4.0 新增：Calmar Ratio（年化報酬 / 最大回撤）
        calmar_ratio = abs(annual_return / max_drawdown) if max_drawdown != 0 else 0
        
        # v4.0 新增：淨值曲線數據
        equity_curve = cumulative.values.tolist()
        equity_dates = [d.strftime('%Y-%m-%d') for d in cumulative.index]
        
        return {
            'total_return': total_return * 100,
            'annual_return': annual_return * 100,
            'buy_hold_return': buy_hold_return * 100,
            'sharpe_ratio': sharpe_ratio,
            'sortino_ratio': sortino_ratio,  # v4.0 新增
            'calmar_ratio': calmar_ratio,  # v4.0 新增
            'max_drawdown': max_drawdown * 100,
            'win_rate': win_rate * 100,
            'total_trades': total_trades,
            'risk_free_rate': risk_free_rate * 100,  # v4.0 新增：顯示使用的無風險利率
            'equity_curve': equity_curve,  # v4.0 新增：淨值曲線
            'equity_dates': equity_dates  # v4.0 新增：淨值曲線日期
        }


# ============================================================================
# v4.4 新增：交易記錄簿 (Trade Ledger)
# ============================================================================

from config import QuantConfig

class TradeLedger:
    """
    交易記錄簿 v4.4
    
    功能：
    - 從 Position 變化偵測進出場點
    - 記錄每筆交易的完整資訊
    - 計算交易級統計指標
    - 支援成本模型（手續費、稅、滑價）
    """
    
    @staticmethod
    def extract_trades(df):
        """
        從回測結果中提取交易記錄
        
        Args:
            df: 包含 Position 欄位的 DataFrame
        
        Returns:
            list: 交易記錄列表
        """
        if 'Position' not in df.columns:
            return []
        
        trades = []
        df = df.copy()
        
        # 確保有需要的欄位
        if 'Close' not in df.columns:
            return []
        
        in_trade = False
        entry_idx = None
        entry_price = None
        direction = None  # 1 = 多單, -1 = 空單
        
        for i in range(1, len(df)):
            prev_pos = df['Position'].iloc[i-1] if pd.notna(df['Position'].iloc[i-1]) else 0
            curr_pos = df['Position'].iloc[i] if pd.notna(df['Position'].iloc[i]) else 0
            
            # 進場：從 0 變為 1 或 -1
            if prev_pos == 0 and curr_pos != 0:
                in_trade = True
                entry_idx = i
                entry_price = df['Open'].iloc[i] if 'Open' in df.columns else df['Close'].iloc[i]
                direction = 1 if curr_pos > 0 else -1
            
            # 出場：從 1 或 -1 變為 0
            elif prev_pos != 0 and curr_pos == 0 and in_trade:
                exit_price = df['Open'].iloc[i] if 'Open' in df.columns else df['Close'].iloc[i]
                
                # 計算持倉期間的 MFE/MAE
                holding_period = df.iloc[entry_idx:i+1]
                if direction == 1:  # 多單
                    mfe = (holding_period['High'].max() - entry_price) / entry_price * 100
                    mae = (holding_period['Low'].min() - entry_price) / entry_price * 100
                    gross_pnl = (exit_price - entry_price) / entry_price * 100
                else:  # 空單
                    mfe = (entry_price - holding_period['Low'].min()) / entry_price * 100
                    mae = (entry_price - holding_period['High'].max()) / entry_price * 100
                    gross_pnl = (entry_price - exit_price) / entry_price * 100
                
                # 計算成本
                costs = TradeLedger._calculate_costs(entry_price, exit_price, df, entry_idx, i)
                net_pnl = gross_pnl - costs['total_cost_pct']
                
                trades.append({
                    'entry_time': df.index[entry_idx],
                    'entry_price': entry_price,
                    'exit_time': df.index[i],
                    'exit_price': exit_price,
                    'direction': 'Long' if direction == 1 else 'Short',
                    'holding_bars': i - entry_idx,
                    'gross_pnl_pct': round(gross_pnl, 4),
                    'net_pnl_pct': round(net_pnl, 4),
                    'costs': costs,
                    'mfe_pct': round(mfe, 4),
                    'mae_pct': round(mae, 4),
                    'win': net_pnl > 0
                })
                
                in_trade = False
                entry_idx = None
                entry_price = None
                direction = None
        
        return trades
    
    @staticmethod
    def _calculate_costs(entry_price, exit_price, df, entry_idx, exit_idx):
        """計算交易成本"""
        if not QuantConfig.ENABLE_COST_MODEL:
            return {
                'commission': 0,
                'tax': 0,
                'slippage': 0,
                'total_cost_pct': 0
            }
        
        # 手續費（雙邊）
        commission = QuantConfig.COMMISSION_RATE * 2 * 100  # 轉為百分比
        
        # 交易稅（台股僅賣出收）
        tax = QuantConfig.TAX_RATE * 100
        
        # 滑價
        if QuantConfig.SLIPPAGE_MODEL == "fixed":
            slippage = QuantConfig.SLIPPAGE_BASE * 2 * 100  # 進出場各一次
        else:  # vol_liq 模型
            # 計算 ATR%
            if len(df) >= 14:
                atr = TradeLedger._calculate_atr(df.iloc[max(0, entry_idx-14):entry_idx+1])
                atr_pct = atr / entry_price if entry_price > 0 else 0
            else:
                atr_pct = 0.02  # 預設 2%
            
            # 計算流動性因子
            avg_volume = df['Volume'].iloc[max(0, entry_idx-20):entry_idx+1].mean()
            liq_factor = 1 / np.sqrt(avg_volume) if avg_volume > 0 else 0.01
            
            slippage = (QuantConfig.SLIPPAGE_BASE + 
                       QuantConfig.SLIPPAGE_K1 * atr_pct + 
                       QuantConfig.SLIPPAGE_K2 * liq_factor) * 2 * 100
        
        total_cost_pct = commission + tax + slippage
        
        return {
            'commission': round(commission, 4),
            'tax': round(tax, 4),
            'slippage': round(slippage, 4),
            'total_cost_pct': round(total_cost_pct, 4)
        }
    
    @staticmethod
    def _calculate_atr(df, period=14):
        """計算 ATR"""
        if len(df) < 2:
            return 0
        
        high = df['High']
        low = df['Low']
        close = df['Close'].shift(1)
        
        tr1 = high - low
        tr2 = abs(high - close)
        tr3 = abs(low - close)
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(min(period, len(df))).mean().iloc[-1]
        
        return atr if pd.notna(atr) else 0
    
    @staticmethod
    def calculate_trade_metrics(trades):
        """
        計算交易級統計指標
        
        Args:
            trades: 交易記錄列表
        
        Returns:
            dict: 交易級統計指標
        """
        if not trades:
            return {
                'total_trades': 0,
                'win_rate': 0,
                'avg_win': 0,
                'avg_loss': 0,
                'expectancy': 0,
                'profit_factor': 0,
                'max_consecutive_losses': 0,
                'avg_holding_bars': 0,
                'best_trade': 0,
                'worst_trade': 0,
                'avg_mfe': 0,
                'avg_mae': 0
            }
        
        # 基本統計
        total_trades = len(trades)
        winning_trades = [t for t in trades if t['win']]
        losing_trades = [t for t in trades if not t['win']]
        
        win_rate = len(winning_trades) / total_trades * 100 if total_trades > 0 else 0
        
        # 平均獲利/虧損
        avg_win = np.mean([t['net_pnl_pct'] for t in winning_trades]) if winning_trades else 0
        avg_loss = abs(np.mean([t['net_pnl_pct'] for t in losing_trades])) if losing_trades else 0
        
        # 期望值
        expectancy = (win_rate/100 * avg_win) - ((1 - win_rate/100) * avg_loss)
        
        # 獲利因子
        total_profit = sum([t['net_pnl_pct'] for t in winning_trades]) if winning_trades else 0
        total_loss = abs(sum([t['net_pnl_pct'] for t in losing_trades])) if losing_trades else 0
        profit_factor = total_profit / total_loss if total_loss > 0 else float('inf') if total_profit > 0 else 0
        
        # 最大連續虧損
        max_consecutive_losses = 0
        current_streak = 0
        for t in trades:
            if not t['win']:
                current_streak += 1
                max_consecutive_losses = max(max_consecutive_losses, current_streak)
            else:
                current_streak = 0
        
        # 平均持倉天數
        avg_holding_bars = np.mean([t['holding_bars'] for t in trades])
        
        # 最佳/最差交易
        all_pnl = [t['net_pnl_pct'] for t in trades]
        best_trade = max(all_pnl)
        worst_trade = min(all_pnl)
        
        # 平均 MFE/MAE
        avg_mfe = np.mean([t['mfe_pct'] for t in trades])
        avg_mae = np.mean([t['mae_pct'] for t in trades])
        
        return {
            'total_trades': total_trades,
            'winning_trades': len(winning_trades),
            'losing_trades': len(losing_trades),
            'win_rate': round(win_rate, 2),
            'avg_win': round(avg_win, 4),
            'avg_loss': round(avg_loss, 4),
            'expectancy': round(expectancy, 4),
            'profit_factor': round(profit_factor, 2) if profit_factor != float('inf') else 'Inf',
            'max_consecutive_losses': max_consecutive_losses,
            'avg_holding_bars': round(avg_holding_bars, 1),
            'best_trade': round(best_trade, 4),
            'worst_trade': round(worst_trade, 4),
            'avg_mfe': round(avg_mfe, 4),
            'avg_mae': round(avg_mae, 4),
            'total_gross_pnl': round(sum([t['gross_pnl_pct'] for t in trades]), 4),
            'total_net_pnl': round(sum([t['net_pnl_pct'] for t in trades]), 4),
            'total_costs': round(sum([t['costs']['total_cost_pct'] for t in trades]), 4)
        }


class BacktestEngineV2(BacktestEngine):
    """
    回測引擎 v4.4 - 增加交易級統計和成本模型
    
    繼承自 BacktestEngine，新增：
    - 交易記錄提取
    - 交易級統計指標
    - 成本模型支援
    """
    
    @staticmethod
    def backtest_with_trade_metrics(df, strategy_func, **kwargs):
        """
        執行回測並計算交易級指標
        
        Args:
            df: 價格資料
            strategy_func: 策略函數
            **kwargs: 策略參數
        
        Returns:
            dict: 包含原有指標和交易級指標
        """
        # 執行原有回測
        result = strategy_func(df, **kwargs)
        
        if result is None:
            return None
        
        # 提取交易記錄
        # 注意：需要從策略函數取得包含 Position 的 df
        # 這裡假設我們可以重新執行策略取得 df
        
        # 計算交易級指標（使用 TradeLedger）
        # ... 需要修改策略函數返回 df
        
        return result
    
    @staticmethod
    def run_full_backtest(df, fast_ma=5, slow_ma=20):
        """
        執行完整回測（包含交易級統計）
        
        這是一個示範方法，展示如何整合新的交易級統計
        """
        df = df.copy()
        
        # 計算信號
        df['MA_Fast'] = df['Close'].rolling(window=fast_ma).mean()
        df['MA_Slow'] = df['Close'].rolling(window=slow_ma).mean()
        
        df['Signal'] = 0
        df.loc[df['MA_Fast'] > df['MA_Slow'], 'Signal'] = 1
        df.loc[df['MA_Fast'] < df['MA_Slow'], 'Signal'] = -1
        
        df['Position'] = df['Signal'].shift(1)
        
        # 計算報酬
        df['Next_Open'] = df['Open'].shift(-1)
        df['Returns'] = df['Next_Open'].pct_change(fill_method=None)
        df['Strategy_Returns'] = df['Position'] * df['Returns']
        
        # 提取交易記錄
        trades = TradeLedger.extract_trades(df)
        
        # 計算交易級指標
        trade_metrics = TradeLedger.calculate_trade_metrics(trades)
        
        # 計算原有指標
        original_metrics = BacktestEngine._calculate_metrics(df)
        
        if original_metrics is None:
            return None
        
        # 合併指標
        result = {
            **original_metrics,
            'metrics_v2': trade_metrics,
            'trades': trades
        }
        
        return result


# ============================================================================
# v4.0 改進：增強版量化分析模組
# ============================================================================

