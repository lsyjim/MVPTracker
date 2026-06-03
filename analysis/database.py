"""
database.py - Database Management Module
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

# ============================================================================
# v4.0 改進：增強版資料庫管理（含籌碼緩存）
# ============================================================================

class WatchlistDatabase:
    """自選股資料庫管理 + 籌碼緩存"""
    
    def __init__(self, db_name="watchlist_v4.db"):
        self.db_name = db_name
        self.init_database()
    
    def init_database(self):
        """初始化資料庫（v4.5.17 新增：族群分類與量化欄位）"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        # 自選股表格
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS watchlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL UNIQUE,
                name TEXT,
                market TEXT DEFAULT '台股',
                added_date TEXT,
                notes TEXT,
                recommendation TEXT DEFAULT '待分析'
            )
        ''')
        
        # ★★★ v4.5.17 重點新增：檢查並添加新欄位 ★★★
        cursor.execute("PRAGMA table_info(watchlist)")
        existing_columns = [col[1] for col in cursor.fetchall()]
        
        # 新增欄位列表（使用單引號作為 SQL 字串預設值）
        new_columns = [
            ('sort_order', "INTEGER DEFAULT 0"),
            ('industry', "TEXT DEFAULT '未分類'"),
            ('quant_score', "REAL DEFAULT 0"),
            ('trend_status', "TEXT DEFAULT '待分析'"),
            ('chip_signal', "TEXT DEFAULT ''"),
            ('bias_20', "REAL DEFAULT 0"),
            ('last_analyzed', "TEXT DEFAULT ''"),
        ]

        for col_name, col_def in new_columns:
            if col_name not in existing_columns:
                try:
                    cursor.execute(f'ALTER TABLE watchlist ADD COLUMN {col_name} {col_def}')
                    print(f"[Database] 資料庫升級：已添加 {col_name} 欄位")
                except sqlite3.OperationalError as e:
                    print(f"[Database] 警告：無法添加 {col_name} 欄位: {e}")
        
        # 分析歷史表格
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS analysis_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                analysis_date TEXT,
                technical_signal TEXT,
                fundamental_signal TEXT,
                recommendation TEXT,
                analysis_data TEXT,
                FOREIGN KEY (symbol) REFERENCES watchlist(symbol)
            )
        ''')
        
        # v4.0 新增：籌碼面緩存表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS chip_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                data_date TEXT NOT NULL,
                cache_time TEXT NOT NULL,
                foreign_investor INTEGER,
                investment_trust INTEGER,
                dealer INTEGER,
                raw_data TEXT,
                UNIQUE(symbol, data_date)
            )
        ''')
        
        # v4.0 新增：大盤數據緩存表（用於 Beta 計算和市場環境判斷）
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS market_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                index_code TEXT NOT NULL,
                cache_date TEXT NOT NULL,
                price_data TEXT,
                adx_value REAL,
                trend_direction TEXT,
                UNIQUE(index_code, cache_date)
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def add_stock(self, symbol, name="", market="台股", notes="", industry="未分類"):
        """新增自選股（v4.5.17 支援族群分類）"""
        try:
            conn = sqlite3.connect(self.db_name)
            cursor = conn.cursor()
            added_date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # 取得目前最大的 sort_order
            cursor.execute('SELECT COALESCE(MAX(sort_order), -1) + 1 FROM watchlist')
            next_order = cursor.fetchone()[0]
            
            # ★★★ v4.5.17 自動判斷族群 (如果未提供) ★★★
            if industry == "未分類" and market == "台股":
                try:
                    if symbol in twstock.codes:
                        # 從 twstock 獲取分類
                        stock_info = twstock.codes[symbol]
                        industry = stock_info.group or stock_info.type or "未分類"
                except Exception:
                    pass
            
            # 插入資料 (包含 industry 和 sort_order)
            cursor.execute('''
                INSERT INTO watchlist (symbol, name, market, added_date, notes, recommendation, sort_order, industry)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (symbol, name, market, added_date, notes, "待分析", next_order, industry))
            
            conn.commit()
            conn.close()
            print(f"[Database] 新增自選股: {symbol} {name} (族群: {industry})")
            return True
        except sqlite3.IntegrityError:
            return False
    
    def remove_stock(self, symbol):
        """移除自選股"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM watchlist WHERE symbol = ?', (symbol,))
        cursor.execute('DELETE FROM analysis_history WHERE symbol = ?', (symbol,))
        conn.commit()
        conn.close()
    
    def get_all_stocks(self, order_by='industry'):
        """取得所有自選股（v4.5.18 加入分析時間）"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        # 增加 industry 排序選項
        valid_orders = {
            'sort_order': 'sort_order ASC',
            'symbol': 'symbol ASC',
            'name': 'name ASC',
            'added_date': 'added_date DESC',
            'industry': 'industry ASC, symbol ASC',  # 先排族群，再排代碼
            'recommendation': 'recommendation ASC',
            'quant_score': 'quant_score DESC'
        }
        order_clause = valid_orders.get(order_by, 'industry ASC, symbol ASC')
        
        # v4.5.18：加入 last_analyzed 欄位（索引 12）
        cursor.execute(f'''
            SELECT symbol, name, market, added_date, notes, recommendation, 
                   COALESCE(industry, "未分類") as industry,
                   COALESCE(sort_order, 0) as sort_order,
                   COALESCE(quant_score, 0) as quant_score,
                   COALESCE(trend_status, "待分析") as trend_status,
                   COALESCE(chip_signal, "") as chip_signal,
                   COALESCE(bias_20, 0) as bias_20,
                   COALESCE(last_analyzed, "") as last_analyzed
            FROM watchlist 
            ORDER BY {order_clause}
        ''')
        stocks = cursor.fetchall()
        conn.close()
        return stocks
    
    def update_industry(self, symbol, industry):
        """更新股票的族群分類"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('UPDATE watchlist SET industry = ? WHERE symbol = ?', (industry, symbol))
        conn.commit()
        conn.close()
    
    def update_quant_data(self, symbol, quant_score=None, trend_status=None, 
                          chip_signal=None, bias_20=None, recommendation=None):
        """更新量化數據（v4.5.17 新增）"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        updates = []
        values = []
        
        if quant_score is not None:
            updates.append('quant_score = ?')
            values.append(quant_score)
        if trend_status is not None:
            updates.append('trend_status = ?')
            values.append(trend_status)
        if chip_signal is not None:
            updates.append('chip_signal = ?')
            values.append(chip_signal)
        if bias_20 is not None:
            updates.append('bias_20 = ?')
            values.append(bias_20)
        if recommendation is not None:
            updates.append('recommendation = ?')
            values.append(recommendation)
        
        # 更新最後分析時間
        updates.append('last_analyzed = ?')
        values.append(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        
        values.append(symbol)
        
        if updates:
            cursor.execute(f'UPDATE watchlist SET {", ".join(updates)} WHERE symbol = ?', values)
            conn.commit()
        
        conn.close()
    
    def get_stocks_grouped_by_industry(self):
        """按族群分組取得股票（v4.5.17 新增）"""
        stocks = self.get_all_stocks(order_by='industry')
        
        grouped = {}
        for stock in stocks:
            # stock 格式: (symbol, name, market, added_date, notes, recommendation, industry, sort_order, quant_score, trend_status, chip_signal, bias_20)
            industry = stock[6] if len(stock) > 6 else '未分類'
            if industry not in grouped:
                grouped[industry] = []
            grouped[industry].append(stock)
        
        return grouped
    
    def get_industry_summary(self):
        """取得族群彙總統計（v4.5.17 新增）"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT 
                COALESCE(industry, '未分類') as industry,
                COUNT(*) as count,
                AVG(COALESCE(quant_score, 0)) as avg_score,
                SUM(CASE WHEN bias_20 > 0 THEN 1 ELSE 0 END) as up_count,
                SUM(CASE WHEN bias_20 < 0 THEN 1 ELSE 0 END) as down_count
            FROM watchlist
            GROUP BY industry
            ORDER BY avg_score DESC
        ''')
        
        results = []
        for row in cursor.fetchall():
            results.append({
                'industry': row[0],
                'count': row[1],
                'avg_score': row[2] or 0,
                'up_count': row[3] or 0,
                'down_count': row[4] or 0
            })
        
        conn.close()
        return results
    
    def auto_tag_all_industries(self):
        """自動為所有股票標註族群（v4.5.17 新增）"""
        stocks = self.get_all_stocks()
        tagged_count = 0
        
        for stock in stocks:
            symbol = stock[0]
            current_industry = stock[6] if len(stock) > 6 else '未分類'
            
            if current_industry == '未分類' or not current_industry:
                try:
                    if symbol in twstock.codes:
                        stock_info = twstock.codes[symbol]
                        new_industry = stock_info.group or stock_info.type or '未分類'
                        if new_industry != '未分類':
                            self.update_industry(symbol, new_industry)
                            tagged_count += 1
                            print(f"[Database] 自動標註: {symbol} -> {new_industry}")
                except Exception:
                    pass
        
        print(f"[Database] 自動標註完成: {tagged_count} 檔")
        return tagged_count
    
    def get_stock_count(self):
        """取得自選股數量"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM watchlist')
        count = cursor.fetchone()[0]
        conn.close()
        return count
    
    def update_recommendation(self, symbol, recommendation):
        """更新投資建議"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('UPDATE watchlist SET recommendation = ? WHERE symbol = ?', 
                      (recommendation, symbol))
        conn.commit()
        conn.close()
    
    def save_analysis(self, symbol, tech_signal, fund_signal, recommendation, analysis_data):
        """儲存分析結果"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        analysis_date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # 安全處理 analysis_data
        if isinstance(analysis_data, dict):
            # 深拷貝並清理不可序列化的數據
            clean_data = {}
            for key, value in analysis_data.items():
                if isinstance(value, (str, int, float, bool, type(None))):
                    clean_data[key] = value
                elif isinstance(value, dict):
                    clean_data[key] = {k: v for k, v in value.items() 
                                       if isinstance(v, (str, int, float, bool, type(None)))}
                else:
                    clean_data[key] = str(value)
            analysis_json = json.dumps(clean_data, ensure_ascii=False, default=str)
        else:
            analysis_json = str(analysis_data)
        
        cursor.execute('''
            INSERT INTO analysis_history 
            (symbol, analysis_date, technical_signal, fundamental_signal, recommendation, analysis_data)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (symbol, analysis_date, tech_signal, fund_signal, recommendation, analysis_json))
        conn.commit()
        conn.close()
    
    # ==================== v4.0 新增：籌碼緩存方法 ====================
    
    def get_cached_chip_data(self, symbol, data_date):
        """取得緩存的籌碼數據"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT foreign_investor, investment_trust, dealer, raw_data, cache_time
            FROM chip_cache 
            WHERE symbol = ? AND data_date = ?
        ''', (symbol, data_date))
        
        result = cursor.fetchone()
        conn.close()
        
        if result:
            cache_time = datetime.datetime.strptime(result[4], "%Y-%m-%d %H:%M:%S")
            # 檢查緩存是否過期
            if (datetime.datetime.now() - cache_time).total_seconds() < QuantConfig.CHIP_CACHE_HOURS * 3600:
                return {
                    'foreign_investor': result[0],
                    'investment_trust': result[1],
                    'dealer': result[2],
                    'raw_data': json.loads(result[3]) if result[3] else None
                }
        return None
    
    def save_chip_cache(self, symbol, data_date, foreign_inv, invest_trust, dealer=0, raw_data=None):
        """儲存籌碼數據到緩存"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cache_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        raw_json = json.dumps(raw_data, ensure_ascii=False) if raw_data else None
        
        cursor.execute('''
            INSERT OR REPLACE INTO chip_cache 
            (symbol, data_date, cache_time, foreign_investor, investment_trust, dealer, raw_data)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (symbol, data_date, cache_time, foreign_inv, invest_trust, dealer, raw_json))
        
        conn.commit()
        conn.close()
    
    def clean_old_cache(self, days=7):
        """清理過期緩存"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cutoff_date = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
        cursor.execute('DELETE FROM chip_cache WHERE data_date < ?', (cutoff_date,))
        cursor.execute('DELETE FROM market_cache WHERE cache_date < ?', (cutoff_date,))
        
        conn.commit()
        conn.close()


# ============================================================================
# v4.0 新增：市場環境分析器
# ============================================================================

