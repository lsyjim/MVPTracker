"""
data_fetcher.py - Data Fetching Module
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

from config import QuantConfig

# ============================================================================
# v4.3 新增：即時股價爬蟲（Yahoo 股市）
# ============================================================================

class RealtimePriceFetcher:
    """
    即時股價爬蟲
    
    從 Yahoo 股市爬取即時股價，支援上市和上櫃公司。
    如果爬蟲失敗，自動 fallback 到 yfinance。
    
    URL 格式：
    - 上市：https://tw.stock.yahoo.com/quote/2330
    - 上櫃：https://tw.stock.yahoo.com/quote/6547
    """
    
    # 緩存（避免頻繁請求）
    _cache = {}
    _cache_timeout = 60  # 緩存 60 秒
    
    @classmethod
    def get_realtime_price(cls, symbol, market="台股"):
        """
        取得即時股價
        
        Args:
            symbol: 股票代碼（如 2330, 6547）
            market: 市場（台股/美股）
        
        Returns:
            dict: {
                'price': 即時價格,
                'change': 漲跌,
                'change_pct': 漲跌幅(%),
                'name': 股票名稱,
                'source': 'yahoo_scrape' 或 'yfinance',
                'time': 更新時間
            }
        """
        # 檢查緩存
        cache_key = f"{symbol}_{market}"
        if cache_key in cls._cache:
            cached_data, cached_time = cls._cache[cache_key]
            if time.time() - cached_time < cls._cache_timeout:
                return cached_data
        
        result = None
        
        # 台股嘗試爬蟲
        if market == "台股":
            result = cls._scrape_yahoo_tw(symbol)
        
        # 爬蟲失敗，fallback 到 yfinance
        if result is None:
            result = cls._fallback_yfinance(symbol, market)
        
        # 更新緩存
        if result:
            cls._cache[cache_key] = (result, time.time())
        
        return result
    
    @classmethod
    def _scrape_yahoo_tw(cls, symbol):
        """從 Yahoo 股市爬取台股即時股價"""
        try:
            url = f'https://tw.stock.yahoo.com/quote/{symbol}'
            headers = {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
            
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code != 200:
                return None
            
            soup = BeautifulSoup(response.text, "html.parser")
            
            # 取得股票名稱（在 #main-0-QuoteHeader-Proxy 內的 h1）
            name = symbol  # 預設為代碼
            try:
                header_proxy = soup.select('#main-0-QuoteHeader-Proxy')
                if header_proxy:
                    h1_elem = header_proxy[0].find('h1')
                    if h1_elem:
                        name = h1_elem.get_text().strip()
            except:
                pass
            
            # 如果名稱還是網站標題，則設為代碼
            if 'Yahoo' in name or name == symbol:
                name = symbol
            
            # 取得即時價格（class 為 Fz(32px)）
            price_elem = soup.select('.Fz\\(32px\\)')
            if not price_elem:
                price_elem = soup.select('[class*="Fz(32px)"]')
            if not price_elem:
                return None
            
            price_text = price_elem[0].get_text().strip().replace(',', '')
            try:
                price = float(price_text)
            except:
                return None
            
            # 取得漲跌幅（class 為 Fz(20px)）
            change = 0
            change_pct = 0
            change_elem = soup.select('.Fz\\(20px\\)')
            if not change_elem:
                change_elem = soup.select('[class*="Fz(20px)"]')
            
            if change_elem:
                change_text = change_elem[0].get_text().strip()
                # 解析格式如 "10.50 (1.25%)" 或 "+10.50(+1.25%)"
                try:
                    # 移除所有非數字字符（保留小數點和負號）
                    import re
                    numbers = re.findall(r'[-+]?[\d,]+\.?\d*', change_text)
                    if len(numbers) >= 2:
                        change = float(numbers[0].replace(',', ''))
                        change_pct = float(numbers[1].replace(',', ''))
                except:
                    pass
            
            # 判斷漲跌方向
            trend = 0  # 0=平盤, 1=上漲, -1=下跌
            try:
                header_proxy = soup.select('#main-0-QuoteHeader-Proxy')
                if header_proxy:
                    # 檢查是否有下跌的 class
                    down_elems = header_proxy[0].select('[class*="trend-down"]')
                    up_elems = header_proxy[0].select('[class*="trend-up"]')
                    
                    if down_elems:
                        trend = -1
                        change = -abs(change)
                        change_pct = -abs(change_pct)
                    elif up_elems:
                        trend = 1
                        change = abs(change)
                        change_pct = abs(change_pct)
            except:
                pass
            
            # 同源昨收：用爬蟲自身的現價與漲跌推算（與現價同來源同時刻）。
            # change 解析失敗或平盤(=0)時回 None，讓呼叫端改用「日期對齊」的
            # hist 昨收，避免錯誤地把現價當昨收（顯示 0%）。
            prev_close = round(price - change, 2) if change else None
            return {
                'price': price,
                'change': change,
                'change_pct': change_pct,
                'prev_close': prev_close,
                'name': name,
                'source': 'yahoo_scrape',
                'time': datetime.datetime.now().strftime('%H:%M:%S'),
                'trend': trend
            }
            
        except Exception as e:
            print(f"Yahoo 爬蟲錯誤 {symbol}: {e}")
            return None
    
    @classmethod
    def _fallback_yfinance(cls, symbol, market):
        """Fallback 到 yfinance 取得股價"""
        try:
            if market == "台股":
                # 先嘗試上市 (.TW)
                ticker_symbol = f"{symbol}.TW"
                stock = yf.Ticker(ticker_symbol)
                hist = stock.history(period="2d")
                
                # 如果沒數據，嘗試上櫃 (.TWO)
                if hist.empty:
                    ticker_symbol = f"{symbol}.TWO"
                    stock = yf.Ticker(ticker_symbol)
                    hist = stock.history(period="2d")
            else:
                ticker_symbol = symbol
                stock = yf.Ticker(ticker_symbol)
                hist = stock.history(period="2d")
            
            if hist.empty:
                return None
            
            current_price = hist['Close'].iloc[-1]
            prev_close = hist['Close'].iloc[-2] if len(hist) > 1 else current_price
            change = current_price - prev_close
            change_pct = (change / prev_close) * 100 if prev_close > 0 else 0
            
            # 嘗試取得股票名稱
            name = symbol
            try:
                info = stock.info
                name = info.get('shortName', info.get('longName', symbol))
            except:
                pass
            
            return {
                'price': round(current_price, 2),
                'change': round(change, 2),
                'change_pct': round(change_pct, 2),
                'prev_close': round(prev_close, 2),  # 同源昨收（同一 2d fetch）
                'name': name,
                'source': 'yfinance',
                'time': datetime.datetime.now().strftime('%H:%M:%S'),
                'trend': 1 if change > 0 else (-1 if change < 0 else 0)
            }
            
        except Exception as e:
            print(f"yfinance fallback 錯誤 {symbol}: {e}")
            return None
    
    @classmethod
    def clear_cache(cls):
        """清除緩存"""
        cls._cache.clear()


# ============================================================================
# v4.3 新增：悟空 API 市場排行數據
# ============================================================================



# ============================================================================
# v4.3 新增：悟空 API 市場排行數據
# ============================================================================



# ============================================================================
# v4.3 新增：悟空 API 市場排行數據
# ============================================================================

class WukongAPI:
    """
    悟空 API 數據獲取
    
    API 端點：
    - 外資：/rank/institutionalInvestors?type=foreign_investors_buy_sell&range=day
    - 投信：/rank/institutionalInvestors?type=investment_trust_buy_sell&range=day
    - 自營商：/rank/institutionalInvestors?type=dealer_buy_sell&range=day
    - 產業分類：/stock/category/industry
    - 產業股列表：/stock/category/industry/{id}
    - 概念股分類：/stock/category/concept
    - 概念股列表：/stock/category/concept/{id}
    - 個股資訊：/stock/{stockID}/trade/max
    - 收盤排行：/rank/trade?type=capacity
    """
    
    BASE_URL = "https://api.wukong.com.tw"
    
    # 緩存
    _cache = {}
    _cache_date = None
    
    @classmethod
    def _get_today_str(cls):
        """取得今天日期字串"""
        return datetime.datetime.now().strftime('%Y-%m-%d')
    
    @classmethod
    def _is_cache_valid(cls):
        """檢查緩存是否有效（當天有效）"""
        return cls._cache_date == cls._get_today_str()
    
    @classmethod
    def _make_request(cls, url):
        """發送 API 請求"""
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Accept': 'application/json',
            'Referer': 'https://wukong.com.tw/'
        }
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            print(f"API 請求錯誤 {url}: {e}")
        return None
    
    @classmethod
    def get_institutional_ranking(cls, top_n=50):
        """
        取得三大法人買賣超排行
        
        API 格式：
        {
            "type": "foreign_investors_buy_sell",
            "buy": [{"stockId": "2884", "val": 36091027}, ...],
            "sell": [{"stockId": "2885", "val": -42001262}, ...],
            "stockInfo": {"2884": {"company_abbreviation": "玉山金", ...}, ...}
        }
        """
        cache_key = f"institutional_{top_n}"
        if cls._is_cache_valid() and cache_key in cls._cache:
            return cls._cache[cache_key]
        
        result = {
            'foreign_buy': [],
            'foreign_sell': [],
            'trust_buy': [],
            'trust_sell': [],
            'dealer_buy': [],
            'dealer_sell': [],
            'date': cls._get_today_str()
        }
        
        # 外資買賣超
        foreign_url = f"{cls.BASE_URL}/rank/institutionalInvestors?type=foreign_investors_buy_sell&range=day"
        foreign_data = cls._make_request(foreign_url)
        if foreign_data:
            result['foreign_buy'] = cls._parse_inst_list(foreign_data.get('buy', []), foreign_data.get('companys', {}),foreign_data.get('trade', {}), top_n)
            result['foreign_sell'] = cls._parse_inst_list(foreign_data.get('sell', []), foreign_data.get('companys', {}),foreign_data.get('trade', {}), top_n)
        
        # 投信買賣超
        trust_url = f"{cls.BASE_URL}/rank/institutionalInvestors?type=investment_trust_buy_sell&range=day"
        trust_data = cls._make_request(trust_url)
        if trust_data:
            result['trust_buy'] = cls._parse_inst_list(trust_data.get('buy', []), trust_data.get('companys', {}),trust_data.get('trade', {}), top_n)
            result['trust_sell'] = cls._parse_inst_list(trust_data.get('sell', []), trust_data.get('companys', {}),trust_data.get('trade', {}), top_n)
        
        # 自營商買賣超
        dealer_url = f"{cls.BASE_URL}/rank/institutionalInvestors?type=dealer_buy_sell&range=day"
        dealer_data = cls._make_request(dealer_url)
        if dealer_data:
            result['dealer_buy'] = cls._parse_inst_list(dealer_data.get('buy', []), dealer_data.get('companys', {}),dealer_data.get('trade', {}), top_n)
            result['dealer_sell'] = cls._parse_inst_list(dealer_data.get('sell', []), dealer_data.get('companys', {}),dealer_data.get('trade', {}), top_n)
        
        # 更新緩存
        cls._cache[cache_key] = result
        cls._cache_date = cls._get_today_str()
        
        return result
    
    @classmethod
    def _parse_inst_list(cls, items, stock_info,trade_dict, top_n):
        """
        解析法人買賣超列表
        
        items: [{"stockId": "2884", "val": 36091027}, ...]
        stock_info: {"2884": {"company_abbreviation": "玉山金", ...}, ...}
        """
        result = []
        for item in items[:top_n]:
            stock_id = item.get('stockId', '')
            val = item.get('val', 0)
            # 從 stockInfo 取得公司資訊
            
            info = stock_info.get(stock_id, {})
            name = info.get('company_abbreviation', '')
            # 2. 抓取個股交易資訊 (從 trade 鍵值)
            trade_info = trade_dict.get(stock_id, {})
            price = trade_info.get('close', 0)    # 收盤價
            change = trade_info.get('change', 0)  # 漲跌幅數值
            # 成交張數：JSON 裡的 capacity 是股數，除以 1000 變張數
            total_vol = int(trade_info.get('capacity', 0) / 1000)
            
            # val 是股數，轉換為張數（除以 1000）
            volume = abs(val) / 1000
            
            result.append({
                'symbol': stock_id,
                'name': name,
                'volume': int(volume),  # 張數
                'price': price,  
                'change_pct': change,
                'total_vol': total_vol
            })
        
        return result
    
    @classmethod
    def get_industry_list(cls):
        """
        取得產業分類列表
        
        API 格式：
        {
            "data": [
                {"id": 24, "name": "半導體業", "up_count": 90, "down_count": 80, "unchanged_count": 11}
            ]
        }
        """
        cache_key = "industry_list"
        if cls._is_cache_valid() and cache_key in cls._cache:
            return cls._cache[cache_key]
        
        url = f"{cls.BASE_URL}/stock/category/industry"
        data = cls._make_request(url)
        
        result = []
        if data and 'data' in data:
            for item in data['data']:
                # 計算漲跌比例
                up = item.get('up_count', 0) or 0
                down = item.get('down_count', 0) or 0
                total = up + down
                
                # 用漲跌家數差距來表示整體趨勢
                if total > 0:
                    change_pct = ((up - down) / total) * 100
                else:
                    change_pct = 0
                
                result.append({
                    'id': str(item.get('id', '')),
                    'name': item.get('name', '').strip(),
                    'up_count': up,
                    'down_count': down,
                    'change_pct': round(change_pct, 1)
                })
        
        cls._cache[cache_key] = result
        cls._cache_date = cls._get_today_str()
        
        return result
    
    @classmethod
    def get_industry_stocks(cls, industry_id, top_n=50):
        """
        取得特定產業的個股列表
        
        API 格式：
        {
            "data": [
                {
                    "stock_code": "8299",
                    "close": 1450,
                    "change": 110,
                    "capacity": 13499000,
                    "company": {"company_abbreviation": "群聯", ...}
                }
            ],
            "title": "半導體業"
        }
        """
        cache_key = f"industry_stocks_{industry_id}"
        if cls._is_cache_valid() and cache_key in cls._cache:
            return cls._cache[cache_key]
        
        url = f"{cls.BASE_URL}/stock/category/industry/{industry_id}"
        data = cls._make_request(url)
        
        result = []
        if data and 'data' in data:
            result = cls._parse_stock_list(data['data'], top_n)
        
        cls._cache[cache_key] = result
        cls._cache_date = cls._get_today_str()
        
        return result
    
    @classmethod
    def get_concept_list(cls):
        """
        取得概念股分類列表
        
        API 格式同產業分類
        """
        cache_key = "concept_list"
        if cls._is_cache_valid() and cache_key in cls._cache:
            return cls._cache[cache_key]
        
        url = f"{cls.BASE_URL}/stock/category/concept"
        data = cls._make_request(url)
        
        result = []
        if data and 'data' in data:
            for item in data['data']:
                up = item.get('up_count', 0) or 0
                down = item.get('down_count', 0) or 0
                total = up + down
                
                if total > 0:
                    change_pct = ((up - down) / total) * 100
                else:
                    change_pct = 0
                
                result.append({
                    'id': str(item.get('id', '')),
                    'name': item.get('name', '').strip(),
                    'up_count': up,
                    'down_count': down,
                    'change_pct': round(change_pct, 1)
                })
        
        cls._cache[cache_key] = result
        cls._cache_date = cls._get_today_str()
        
        return result
    
    @classmethod
    def get_concept_stocks(cls, concept_id, top_n=50):
        """
        取得特定概念股的個股列表
        
        API 格式同產業股列表
        """
        cache_key = f"concept_stocks_{concept_id}"
        if cls._is_cache_valid() and cache_key in cls._cache:
            return cls._cache[cache_key]
        
        url = f"{cls.BASE_URL}/stock/category/concept/{concept_id}"
        data = cls._make_request(url)
        
        result = []
        if data and 'data' in data:
            result = cls._parse_stock_list(data['data'], top_n)
        
        cls._cache[cache_key] = result
        cls._cache_date = cls._get_today_str()
        
        return result
    
    @classmethod
    def _parse_stock_list(cls, items, top_n):
        """
        解析股票列表
        
        items: [
            {
                "stock_code": "2330",
                "close": 1550,
                "change": 30,
                "capacity": 31282568,
                "company": {"company_abbreviation": "台積電", ...}
            }
        ]
        """
        result = []
        for item in items[:top_n]:
            if not isinstance(item, dict):
                continue
            
            stock_code = item.get('stock_code', '')
            close = item.get('close', 0) or 0
            change = item.get('change', 0) or 0
            capacity = item.get('capacity', 0) or 0
            
            # 從 company 取得名稱
            company = item.get('company', {})
            name = company.get('company_abbreviation', '') if company else ''
            
            # 計算漲跌幅
            if close and change:
                prev_close = close - change
                change_pct = (change / prev_close * 100) if prev_close > 0 else 0
            else:
                change_pct = 0
            
            # capacity 是股數，轉換為張數
            volume = capacity / 1000
            
            result.append({
                'symbol': stock_code,
                'name': name,
                'price': close,
                'change': change,
                'change_pct': round(change_pct, 2),
                'volume': int(volume)
            })
        
        return result
    
    @classmethod
    def get_stock_info(cls, stock_id):
        """
        取得個股資訊
        
        API 格式：
        {
            "data": {
                "stock_code": "2330",
                "close": 1550,
                "change": 30,
                "capacity": 31282568,
                "high": 1550,
                "low": 1515,
                "open": 1520
            }
        }
        """
        url = f"{cls.BASE_URL}/stock/{stock_id}/trade/max"
        data = cls._make_request(url)
        
        if data and 'data' in data:
            item = data['data']
            close = item.get('close', 0) or 0
            change = item.get('change', 0) or 0
            
            if close and change:
                prev_close = close - change
                change_pct = (change / prev_close * 100) if prev_close > 0 else 0
            else:
                change_pct = 0
            
            return {
                'symbol': item.get('stock_code', stock_id),
                'price': close,
                'change': change,
                'change_pct': round(change_pct, 2),
                'volume': int((item.get('capacity', 0) or 0) / 1000),
                'high': item.get('high', 0),
                'low': item.get('low', 0),
                'open': item.get('open', 0)
            }
        return None
    
    @classmethod
    def get_volume_ranking(cls, top_n=50):
        """
        取得成交量排行
        """
        cache_key = f"volume_ranking_{top_n}"
        if cls._is_cache_valid() and cache_key in cls._cache:
            return cls._cache[cache_key]
        
        url = f"{cls.BASE_URL}/rank/trade?type=capacity"
        data = cls._make_request(url)
        
        result = []
        if data and 'data' in data:
            result = cls._parse_stock_list(data['data'], top_n)
        
        cls._cache[cache_key] = result
        cls._cache_date = cls._get_today_str()
        
        return result
    
    @classmethod
    def clear_cache(cls):
        """清除緩存"""
        cls._cache.clear()
        cls._cache_date = None


# ============================================================================
# v4.4.7 新增：富邦行情 API 整合（優先數據源）
# ============================================================================

class FubonMarketData:
    """
    富邦行情 API 數據提供者
    
    使用富邦 Neo API 取得即時和歷史行情數據。
    優先使用此數據源，失敗時才 fallback 到 yfinance。
    
    API 文檔：https://www.fbs.com.tw/TradeAPI/docs/market-data/intro
    """
    
    _sdk = None
    _rest_client = None
    _is_initialized = False
    _init_lock = threading.Lock()
    _cache = {}
    _cache_ttl = 300  # 快取 5 分鐘
    
    # 統計
    _total_requests = 0
    _total_failures = 0
    
    @classmethod
    def initialize(cls, sdk=None):
        """
        初始化富邦行情 API
        
        Args:
            sdk: 已登入的 FubonSDK 實例（可選，若不提供則嘗試新建）
        
        Returns:
            bool: 是否初始化成功
        """
        with cls._init_lock:
            if cls._is_initialized:
                return True
            
            try:
                if sdk is not None:
                    # 使用傳入的 SDK
                    cls._sdk = sdk
                else:
                    # 嘗試導入並初始化
                    try:
                        from fubon_neo.sdk import FubonSDK
                        cls._sdk = FubonSDK()
                    except ImportError:
                        print("[FubonMarketData] 未安裝 fubon_neo SDK")
                        return False
                
                # 初始化行情連線
                cls._sdk.init_realtime()
                cls._rest_client = cls._sdk.marketdata.rest_client.stock
                cls._is_initialized = True
                print("[FubonMarketData] 富邦行情 API 初始化成功")
                return True
                
            except Exception as e:
                print(f"[FubonMarketData] 初始化失敗: {e}")
                return False
    
    @classmethod
    def is_available(cls) -> bool:
        """檢查富邦 API 是否可用"""
        return cls._is_initialized and cls._rest_client is not None
    
    @classmethod
    def get_historical_candles(cls, symbol: str, start_date: str, end_date: str, timeframe: str = 'D') -> pd.DataFrame:
        """
        取得歷史 K 線數據
        
        Args:
            symbol: 股票代碼（如 '2330'）
            start_date: 開始日期（'YYYY-MM-DD'）
            end_date: 結束日期（'YYYY-MM-DD'）
            timeframe: 時間框架（'D'=日線, 'W'=週線, 'M'=月線, '1'/'5'/'15'/'30'/'60'=分鐘線）
        
        Returns:
            pd.DataFrame: OHLCV 數據，格式與 yfinance 相容
        """
        if not cls.is_available():
            print("[FubonMarketData] API 未初始化，無法取得數據")
            return None
        
        # 檢查快取
        cache_key = f"hist_{symbol}_{start_date}_{end_date}_{timeframe}"
        if cache_key in cls._cache:
            cached_data, cached_time = cls._cache[cache_key]
            if time.time() - cached_time < cls._cache_ttl:
                return cached_data.copy()
        
        try:
            cls._total_requests += 1
            
            # 呼叫富邦 API
            response = cls._rest_client.historical.candles(**{
                'symbol': symbol,
                'from': start_date,
                'to': end_date,
                'timeframe': timeframe
            })
            
            if response and 'data' in response and response['data']:
                # 轉換為 DataFrame
                df = cls._convert_to_dataframe(response['data'])
                
                # 存入快取
                cls._cache[cache_key] = (df.copy(), time.time())
                
                print(f"[FubonMarketData] {symbol} 歷史數據取得成功：{len(df)} 筆")
                return df
            else:
                print(f"[FubonMarketData] {symbol} 無歷史數據")
                return None
                
        except Exception as e:
            cls._total_failures += 1
            print(f"[FubonMarketData] {symbol} 歷史數據取得失敗: {e}")
            return None
    
    @classmethod
    def get_intraday_candles(cls, symbol: str, timeframe: str = '1') -> pd.DataFrame:
        """
        取得日內 K 線數據
        
        Args:
            symbol: 股票代碼
            timeframe: 時間框架（'1'/'5'/'10'/'15'/'30'/'60' 分鐘）
        
        Returns:
            pd.DataFrame: 日內 OHLCV 數據
        """
        if not cls.is_available():
            return None
        
        try:
            cls._total_requests += 1
            
            response = cls._rest_client.intraday.candles(symbol=symbol, timeframe=timeframe)
            
            if response and 'data' in response and response['data']:
                df = cls._convert_to_dataframe(response['data'], is_intraday=True)
                print(f"[FubonMarketData] {symbol} 日內數據取得成功：{len(df)} 筆")
                return df
            else:
                return None
                
        except Exception as e:
            cls._total_failures += 1
            print(f"[FubonMarketData] {symbol} 日內數據取得失敗: {e}")
            return None
    
    @classmethod
    def get_quote(cls, symbol: str) -> dict:
        """
        取得即時報價
        
        Args:
            symbol: 股票代碼
        
        Returns:
            dict: 即時報價資訊
        """
        if not cls.is_available():
            return None
        
        # 檢查快取（即時報價快取較短）
        cache_key = f"quote_{symbol}"
        if cache_key in cls._cache:
            cached_data, cached_time = cls._cache[cache_key]
            if time.time() - cached_time < 10:  # 10 秒快取
                return cached_data.copy()
        
        try:
            cls._total_requests += 1
            
            response = cls._rest_client.intraday.quote(symbol=symbol)
            
            if response:
                result = {
                    'symbol': symbol,
                    'price': response.get('closePrice', 0) or response.get('lastPrice', 0),
                    'open': response.get('openPrice', 0),
                    'high': response.get('highPrice', 0),
                    'low': response.get('lowPrice', 0),
                    'volume': response.get('totalVolume', 0),
                    'change': response.get('change', 0),
                    'change_pct': response.get('changePercent', 0),
                    'time': response.get('lastUpdated', ''),
                    'source': 'fubon_api'
                }
                
                # 存入快取
                cls._cache[cache_key] = (result.copy(), time.time())
                
                return result
            else:
                return None
                
        except Exception as e:
            cls._total_failures += 1
            print(f"[FubonMarketData] {symbol} 報價取得失敗: {e}")
            return None
    
    @classmethod
    def _convert_to_dataframe(cls, data: list, is_intraday: bool = False) -> pd.DataFrame:
        """
        將富邦 API 數據轉換為與 yfinance 相容的 DataFrame 格式
        
        Args:
            data: 富邦 API 返回的 K 線數據列表
            is_intraday: 是否為日內數據
        
        Returns:
            pd.DataFrame: 標準化的 OHLCV DataFrame
        """
        if not data:
            return pd.DataFrame()
        
        records = []
        for item in data:
            date_str = item.get('date', '')
            
            # 解析日期
            if 'T' in date_str:
                # 日內數據格式: '2024-04-17T09:00:00.000+08:00'
                try:
                    dt = pd.to_datetime(date_str)
                except:
                    continue
            else:
                # 日線數據格式: '2024-04-18'
                try:
                    dt = pd.to_datetime(date_str)
                except:
                    continue
            
            records.append({
                'Date': dt,
                'Open': float(item.get('open', 0) or 0),
                'High': float(item.get('high', 0) or 0),
                'Low': float(item.get('low', 0) or 0),
                'Close': float(item.get('close', 0) or 0),
                'Volume': int(item.get('volume', 0) or 0)
            })
        
        if not records:
            return pd.DataFrame()
        
        df = pd.DataFrame(records)
        df.set_index('Date', inplace=True)
        df.sort_index(inplace=True)
        
        return df
    
    @classmethod
    def clear_cache(cls):
        """清除快取"""
        cls._cache.clear()
    
    @classmethod
    def get_stats(cls) -> dict:
        """取得統計資訊"""
        return {
            'is_available': cls.is_available(),
            'total_requests': cls._total_requests,
            'total_failures': cls._total_failures,
            'cache_size': len(cls._cache)
        }


# ============================================================================
# v4.4.7 新增：統一數據源管理器
# ============================================================================

class DataSourceManager:
    """
    統一數據源管理器
    
    優先順序：
    1. 富邦 API（即時、無延遲）
    2. yfinance（備用，可能有延遲）
    
    特點：
    - 自動切換數據源
    - 統一的數據格式輸出
    - 失敗自動重試
    """
    
    # 數據源優先級
    SOURCE_FUBON = 'fubon'
    SOURCE_YFINANCE = 'yfinance'
    
    _current_source = None
    _fubon_failed_count = 0
    _fubon_disabled_until = 0
    _fubon_disable_duration = 300  # 富邦失敗後暫停 5 分鐘

    # ── B2 #1：批次 history 快取（掃描題材前一次抓完，逐檔 get_history 命中快取）──
    # 結構：{ "{SYMBOL}|{MARKET}": (date_str, DataFrame_long) }
    _batch_hist_cache = {}

    # ── B2 #2：即時報價併發預抓快取（短 TTL，掃描期間有效）──
    _realtime_cache = {}          # { "{SYMBOL}|{MARKET}": (timestamp, dict) }
    _realtime_cache_ttl = 90      # 秒（即時報價需新鮮，僅供一次掃描內共用）

    @classmethod
    def prefetch_realtime(cls, symbols, market="台股", max_workers=10):
        """
        B2 #2：併發預抓多檔即時報價（逐檔 Yahoo 爬蟲不受 yfinance 限速器序列化，
        可安全併發）。結果存入 _realtime_cache，get_realtime_price 命中即回。
        Returns: int 成功快取的檔數
        """
        import time as _t
        from concurrent.futures import ThreadPoolExecutor, as_completed
        if not symbols or (market == "台股" and cls.is_fubon_available()):
            return 0
        now = _t.time()
        want = [s for s in symbols
                if not (cls._realtime_cache.get(f"{s}|{market}")
                        and now - cls._realtime_cache[f"{s}|{market}"][0] < cls._realtime_cache_ttl)]
        if not want:
            return 0

        def _fetch(sym):
            try:
                return sym, RealtimePriceFetcher.get_realtime_price(sym, market)
            except Exception:
                return sym, None

        cached = 0
        try:
            with ThreadPoolExecutor(max_workers=min(max_workers, len(want))) as ex:
                for fut in as_completed([ex.submit(_fetch, s) for s in want]):
                    sym, data = fut.result()
                    if data and data.get('price', 0):
                        cls._realtime_cache[f"{sym}|{market}"] = (_t.time(), data)
                        cached += 1
        except Exception as e:
            print(f"[DataSourceManager] 即時報價併發預抓略過: {e}")
        if cached:
            print(f"[DataSourceManager] 即時報價併發預抓：{cached}/{len(want)} 檔")
        return cached

    @classmethod
    def prefetch_histories(cls, symbols, market="台股", period="2y"):
        """
        B2 #1：批次預抓多檔歷史數據（單次 yf.download，threads 併發）。
        把整個題材的 history 一次抓完並灌進 _batch_hist_cache，之後逐檔
        get_history 直接命中快取、切片回傳，省下逐檔 yfinance 呼叫與限速 sleep。
        Returns: int 成功快取的檔數
        """
        import datetime as _dt
        if not symbols:
            return 0
        if market == "台股" and cls.is_fubon_available():
            return 0  # 富邦可用時維持原逐檔路徑

        today = _dt.date.today().isoformat()
        want = [s for s in symbols
                if not (cls._batch_hist_cache.get(f"{s}|{market}")
                        and cls._batch_hist_cache[f"{s}|{market}"][0] == today)]
        if not want:
            return 0

        suffix = ".TW" if market == "台股" else ""
        tickers = [f"{s}{suffix}" for s in want]
        try:
            data = yf.download(tickers, period=period, group_by='ticker',
                               threads=True, progress=False, auto_adjust=False)
        except Exception as e:
            print(f"[DataSourceManager] 批次預抓失敗，回退逐檔: {e}")
            return 0

        cached = 0
        for s, tk in zip(want, tickers):
            try:
                df = data if len(tickers) == 1 else (
                    data[tk] if tk in data.columns.get_level_values(0) else None)
                if df is None:
                    continue
                df = df.dropna(how='all')
                if df is None or df.empty or 'Close' not in df.columns:
                    continue
                cls._batch_hist_cache[f"{s}|{market}"] = (today, df.copy())
                cached += 1
            except Exception:
                continue
        if cached:
            print(f"[DataSourceManager] 批次預抓完成：{cached}/{len(want)} 檔")
        return cached

    @classmethod
    def _serve_from_batch(cls, symbol, market, start_date, end_date, period):
        """從批次快取切片回傳；無快取/過期則回 None。"""
        import datetime as _dt
        c = cls._batch_hist_cache.get(f"{symbol}|{market}")
        if not c or c[0] != _dt.date.today().isoformat():
            return None
        df = c[1]
        try:
            if period:
                days_map = {'5d': 7, '1mo': 31, '3mo': 95, '6mo': 190,
                            '1y': 370, '2y': 740}
                n_days = days_map.get(period)
                if n_days is None:
                    return df.copy()
                cutoff = df.index[-1] - _dt.timedelta(days=n_days)
                return df[df.index >= cutoff].copy()
            out = df
            idx_naive = out.index.tz_localize(None) if out.index.tz is not None else out.index
            if isinstance(start_date, _dt.datetime):
                out = out[idx_naive >= start_date]
                idx_naive = out.index.tz_localize(None) if out.index.tz is not None else out.index
            if isinstance(end_date, _dt.datetime):
                out = out[idx_naive <= end_date]
            return out.copy()
        except Exception:
            return None

    @classmethod
    def initialize(cls, fubon_sdk=None):
        """
        初始化數據源管理器
        
        Args:
            fubon_sdk: 已登入的 FubonSDK 實例
        """
        # 嘗試初始化富邦 API
        if FubonMarketData.initialize(fubon_sdk):
            cls._current_source = cls.SOURCE_FUBON
            print("[DataSourceManager] 主數據源：富邦 API")
        else:
            cls._current_source = cls.SOURCE_YFINANCE
            print("[DataSourceManager] 主數據源：yfinance（富邦 API 不可用）")
    
    @classmethod
    def is_fubon_available(cls) -> bool:
        """檢查富邦 API 是否可用"""
        # 檢查是否被暫時停用
        if time.time() < cls._fubon_disabled_until:
            return False
        
        return FubonMarketData.is_available()
    
    @classmethod
    def _disable_fubon_temporarily(cls):
        """暫時停用富邦 API"""
        cls._fubon_failed_count += 1
        if cls._fubon_failed_count >= 3:
            cls._fubon_disabled_until = time.time() + cls._fubon_disable_duration
            cls._fubon_failed_count = 0
            print(f"[DataSourceManager] 富邦 API 連續失敗，暫停 {cls._fubon_disable_duration} 秒")
    
    @classmethod
    def _reset_fubon_failure(cls):
        """重置富邦失敗計數"""
        cls._fubon_failed_count = 0
    
    @classmethod
    def get_history(cls, symbol: str, market: str = "台股", 
                    start_date=None, end_date=None, period: str = None) -> pd.DataFrame:
        """
        取得歷史數據（統一介面）
        
        優先使用富邦 API，失敗時 fallback 到 yfinance
        
        Args:
            symbol: 股票代碼
            market: 市場（台股/美股）
            start_date: 開始日期（datetime 或字串）
            end_date: 結束日期（datetime 或字串）
            period: 期間（如 '6mo', '1y'），與 start_date/end_date 互斥
        
        Returns:
            pd.DataFrame: OHLCV 數據
        """
        result = None
        source_used = None

        # B2 #1：優先命中批次預抓快取（掃描題材時最大加速來源）
        _batch = cls._serve_from_batch(symbol, market, start_date, end_date, period)
        if _batch is not None and not _batch.empty:
            return _batch

        # 富邦(Fugle)單次歷史請求上限 < 1 年；> 1 年的範圍直接走 yfinance，
        # 避免 400/429 並避免因該失敗誤觸富邦暫時熔斷。
        _long_range = cls._range_days(start_date, end_date, period) > 364

        # 台股優先使用富邦 API（僅 <= 1 年範圍）
        if market == "台股" and cls.is_fubon_available() and not _long_range:
            result = cls._get_history_fubon(symbol, start_date, end_date, period)
            if result is not None and not result.empty:
                source_used = cls.SOURCE_FUBON
                cls._reset_fubon_failure()
            else:
                cls._disable_fubon_temporarily()
        
        # Fallback 到 yfinance
        if result is None or result.empty:
            result = cls._get_history_yfinance(symbol, market, start_date, end_date, period)
            if result is not None and not result.empty:
                source_used = cls.SOURCE_YFINANCE
        
        if source_used:
            print(f"[DataSourceManager] {symbol} 數據來源：{source_used}")
        
        return result
    
    @classmethod
    def _get_history_fubon(cls, symbol: str, start_date, end_date, period: str) -> pd.DataFrame:
        """從富邦 API 取得歷史數據"""
        try:
            # 處理日期
            if period:
                # 將 period 轉換為日期範圍
                end_dt = datetime.datetime.now()
                if period == '5d':
                    start_dt = end_dt - datetime.timedelta(days=5)
                elif period == '1mo':
                    start_dt = end_dt - datetime.timedelta(days=30)
                elif period == '3mo':
                    start_dt = end_dt - datetime.timedelta(days=90)
                elif period == '6mo':
                    start_dt = end_dt - datetime.timedelta(days=180)
                elif period == '1y':
                    start_dt = end_dt - datetime.timedelta(days=365)
                elif period == '2y':
                    start_dt = end_dt - datetime.timedelta(days=730)
                elif period == '5y':
                    start_dt = end_dt - datetime.timedelta(days=1825)
                else:
                    start_dt = end_dt - datetime.timedelta(days=180)  # 預設 6 個月
                
                start_str = start_dt.strftime('%Y-%m-%d')
                end_str = end_dt.strftime('%Y-%m-%d')
            else:
                # 使用傳入的日期
                if isinstance(start_date, datetime.datetime):
                    start_str = start_date.strftime('%Y-%m-%d')
                else:
                    start_str = str(start_date)
                
                if isinstance(end_date, datetime.datetime):
                    end_str = end_date.strftime('%Y-%m-%d')
                else:
                    end_str = str(end_date) if end_date else datetime.datetime.now().strftime('%Y-%m-%d')
            
            return FubonMarketData.get_historical_candles(symbol, start_str, end_str, 'D')

        except Exception as e:
            print(f"[DataSourceManager] 富邦 API 取得 {symbol} 失敗: {e}")
            return None

    @classmethod
    def _range_days(cls, start_date, end_date, period) -> int:
        """估算請求的天數跨度（用於決定是否超過富邦單次上限）。"""
        pmap = {'5d': 5, '1mo': 30, '3mo': 90, '6mo': 180, '1y': 365, '2y': 730, '5y': 1825}
        if period:
            return pmap.get(period, 180)
        try:
            s = start_date if isinstance(start_date, datetime.datetime) else datetime.datetime.strptime(str(start_date)[:10], '%Y-%m-%d')
            e = end_date if isinstance(end_date, datetime.datetime) else (
                datetime.datetime.strptime(str(end_date)[:10], '%Y-%m-%d') if end_date else datetime.datetime.now())
            return (e - s).days
        except Exception:
            return 180

    @classmethod
    def _get_history_yfinance(cls, symbol: str, market: str, start_date, end_date, period: str) -> pd.DataFrame:
        """從 yfinance 取得歷史數據（帶熔斷保護）"""
        try:
            # 導入 YFinanceRateLimiter（避免循環導入）
            from yf_rate_limiter import YFinanceRateLimiter
            
            # 檢查熔斷
            if YFinanceRateLimiter.is_circuit_breaker_active():
                remaining = YFinanceRateLimiter.get_circuit_breaker_remaining()
                print(f"[DataSourceManager] yfinance 熔斷中，剩餘 {remaining} 秒")
                return None
            
            # 構建 ticker symbol
            if market == "台股":
                ticker_symbol = f"{symbol}.TW"
            else:
                ticker_symbol = symbol
            
            ticker = YFinanceRateLimiter.get_ticker_safe(ticker_symbol)
            
            if period:
                result = YFinanceRateLimiter.get_history(ticker, period=period)
            else:
                if isinstance(start_date, datetime.datetime):
                    start_str = start_date.strftime('%Y-%m-%d')
                else:
                    start_str = str(start_date)
                
                if isinstance(end_date, datetime.datetime):
                    end_str = end_date.strftime('%Y-%m-%d')
                else:
                    end_str = str(end_date) if end_date else datetime.datetime.now().strftime('%Y-%m-%d')
                
                result = YFinanceRateLimiter.get_history(ticker, start=start_str, end=end_str)
            
            # 如果 .TW 沒數據，嘗試 .TWO（上櫃）
            if (result is None or result.empty) and market == "台股":
                ticker_symbol = f"{symbol}.TWO"
                ticker = YFinanceRateLimiter.get_ticker_safe(ticker_symbol)
                if period:
                    result = YFinanceRateLimiter.get_history(ticker, period=period)
                else:
                    result = YFinanceRateLimiter.get_history(ticker, start=start_str, end=end_str)
            
            return result
            
        except Exception as e:
            print(f"[DataSourceManager] yfinance 取得 {symbol} 失敗: {e}")
            return None
    
    @classmethod
    def get_realtime_price(cls, symbol: str, market: str = "台股") -> dict:
        """
        取得即時價格
        
        優先使用富邦 API，失敗時 fallback 到其他來源
        """
        import time as _t
        result = None

        # B2 #2：命中併發預抓快取（短 TTL）即回
        _rc = cls._realtime_cache.get(f"{symbol}|{market}")
        if _rc and _t.time() - _rc[0] < cls._realtime_cache_ttl:
            return _rc[1]

        # 台股優先使用富邦 API
        if market == "台股" and cls.is_fubon_available():
            result = FubonMarketData.get_quote(symbol)
            if result and result.get('price', 0) > 0:
                cls._reset_fubon_failure()
                return result
            else:
                cls._disable_fubon_temporarily()
        
        # Fallback 到 RealtimePriceFetcher
        result = RealtimePriceFetcher.get_realtime_price(symbol, market)
        
        return result
    
    @classmethod
    def get_current_source(cls) -> str:
        """取得目前使用的數據源"""
        if cls.is_fubon_available():
            return cls.SOURCE_FUBON
        return cls.SOURCE_YFINANCE
    
    @classmethod
    def get_stats(cls) -> dict:
        """取得統計資訊"""
        return {
            'current_source': cls.get_current_source(),
            'fubon_available': cls.is_fubon_available(),
            'fubon_stats': FubonMarketData.get_stats(),
            'fubon_disabled_until': cls._fubon_disabled_until,
            'fubon_failed_count': cls._fubon_failed_count
        }


# ============================================================================
# v4.3 新增：市場排行彈跳視窗
# ============================================================================

