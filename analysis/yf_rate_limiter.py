"""yf_rate_limiter.py — 從 StockGOGOV2/main.py 抽出的 YFinance 速率限制器。
原本位於 main.py，MVPTracker 將其獨立，供 data_fetcher 與 quick_analyzer 共用。"""
import time, datetime, warnings
import yfinance as yf


class YFinanceRateLimiter:
    """
    YFinance 速率限制器（帶熔斷機制）
    
    解決 "Too Many Requests" 錯誤：
    1. 請求間隔控制
    2. 指數退避重試（最多 2 次）
    3. 簡易快取
    4. ★ 熔斷機制：連續失敗 3 次後暫停所有請求 5 分鐘
    """
    
    _last_request_time = 0
    _min_interval = 1.0  # 最小請求間隔（秒）- 加大到 1 秒
    _cache = {}  # 簡易快取 {ticker: {'data': df, 'timestamp': time}}
    _cache_ttl = 600  # 快取有效期（秒）- 加長到 10 分鐘

    # B2 #3：基本面 .info 跨日磁碟快取（.info 盤中幾乎不變，每日抓一次即可）
    _info_disk_cache = None          # 延遲載入的當日磁碟快取 {ticker: data}
    _info_disk_cache_date = None     # 磁碟快取的日期字串
    _info_disk_dirty = False         # 是否有新資料待寫回
    
    # 熔斷機制
    _consecutive_failures = 0  # 連續失敗次數
    _circuit_breaker_triggered = False  # 熔斷是否觸發
    _circuit_breaker_until = 0  # 熔斷解除時間
    _max_failures = 3  # 觸發熔斷的連續失敗次數
    _cooldown_duration = 300  # 熔斷冷卻時間（5 分鐘）
    
    # 請求計數（用於診斷）
    _total_requests = 0
    _total_cache_hits = 0
    _total_failures = 0
    
    @classmethod
    def is_circuit_breaker_active(cls) -> bool:
        """檢查熔斷是否生效中"""
        if cls._circuit_breaker_triggered:
            if time.time() < cls._circuit_breaker_until:
                return True
            else:
                # 熔斷時間已過，重置
                cls._circuit_breaker_triggered = False
                cls._consecutive_failures = 0
                print(f"[YFinance] 熔斷已解除，恢復請求")
                return False
        return False
    
    @classmethod
    def get_circuit_breaker_remaining(cls) -> int:
        """取得熔斷剩餘秒數"""
        if cls._circuit_breaker_triggered:
            remaining = int(cls._circuit_breaker_until - time.time())
            return max(0, remaining)
        return 0
    
    @classmethod
    def trigger_circuit_breaker(cls, reason: str = ""):
        """觸發熔斷"""
        cls._circuit_breaker_triggered = True
        cls._circuit_breaker_until = time.time() + cls._cooldown_duration
        print(f"⛔ [YFinance] 熔斷觸發！原因：{reason}")
        print(f"⛔ [YFinance] 所有 API 請求暫停 {cls._cooldown_duration} 秒")
        print(f"⛔ [YFinance] 統計：總請求 {cls._total_requests}，快取命中 {cls._total_cache_hits}，失敗 {cls._total_failures}")
    
    @classmethod
    def get_history(cls, ticker_obj, **kwargs):
        """
        帶速率限制和熔斷機制的 history() 調用
        
        Args:
            ticker_obj: yf.Ticker 物件
            **kwargs: 傳遞給 history() 的參數
        
        Returns:
            DataFrame or None
        """
        # 檢查熔斷
        if cls.is_circuit_breaker_active():
            remaining = cls.get_circuit_breaker_remaining()
            print(f"⚠️ [YFinance] 熔斷中，剩餘 {remaining} 秒，返回快取或 None")
            # 嘗試返回快取
            ticker_symbol = ticker_obj.ticker if hasattr(ticker_obj, 'ticker') else str(ticker_obj)
            cache_key = f"{ticker_symbol}_{hash(frozenset(kwargs.items()))}"
            if cache_key in cls._cache:
                cls._total_cache_hits += 1
                return cls._cache[cache_key]['data'].copy()
            return None
        
        # 生成快取鍵
        ticker_symbol = ticker_obj.ticker if hasattr(ticker_obj, 'ticker') else str(ticker_obj)
        cache_key = f"{ticker_symbol}_{hash(frozenset(kwargs.items()))}"
        
        # 檢查快取
        if cache_key in cls._cache:
            cached = cls._cache[cache_key]
            if time.time() - cached['timestamp'] < cls._cache_ttl:
                cls._total_cache_hits += 1
                return cached['data'].copy()
        
        # 速率限制：確保請求間隔
        current_time = time.time()
        time_since_last = current_time - cls._last_request_time
        if time_since_last < cls._min_interval:
            sleep_time = cls._min_interval - time_since_last
            time.sleep(sleep_time)
        
        # 指數退避重試（最多 2 次，避免無限循環）
        max_retries = 2
        base_delay = 3
        
        for attempt in range(max_retries):
            try:
                cls._last_request_time = time.time()
                cls._total_requests += 1
                
                result = ticker_obj.history(**kwargs)
                
                # 成功，重置失敗計數
                cls._consecutive_failures = 0
                
                # 存入快取
                if result is not None and not result.empty:
                    cls._cache[cache_key] = {
                        'data': result.copy(),
                        'timestamp': time.time()
                    }
                
                return result
                
            except Exception as e:
                error_str = str(e).lower()
                cls._total_failures += 1
                
                # 檢查是否為速率限制錯誤
                if 'rate' in error_str or 'limit' in error_str or 'too many' in error_str:
                    cls._consecutive_failures += 1
                    
                    # 檢查是否需要觸發熔斷
                    if cls._consecutive_failures >= cls._max_failures:
                        cls.trigger_circuit_breaker(f"連續 {cls._consecutive_failures} 次速率限制錯誤")
                        return None
                    
                    # 重試
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        print(f"⚠️ [YFinance] 速率限制，等待 {delay} 秒... (嘗試 {attempt + 1}/{max_retries})")
                        time.sleep(delay)
                    else:
                        print(f"⚠️ [YFinance] 重試失敗，連續失敗 {cls._consecutive_failures} 次")
                        return None
                else:
                    # 其他錯誤，不重試
                    print(f"⚠️ [YFinance] 非速率限制錯誤: {e}")
                    return None
        
        return None
    
    @classmethod
    def get_ticker_safe(cls, symbol):
        """
        安全取得 Ticker 物件
        
        Returns:
            Ticker 物件（不會觸發 API 請求）
        """
        # 檢查熔斷
        if cls.is_circuit_breaker_active():
            remaining = cls.get_circuit_breaker_remaining()
            print(f"⚠️ [YFinance] 熔斷中（剩餘 {remaining} 秒），但仍返回 Ticker 物件")
        
        # 建立 Ticker 物件不會觸發 API 請求
        return yf.Ticker(symbol)
    
    @classmethod
    def get_info_safe(cls, ticker_obj, timeout: int = 10):
        """
        安全取得 stock.info（帶快取和熔斷）
        
        Args:
            ticker_obj: yf.Ticker 物件
            timeout: 超時秒數
        
        Returns:
            dict: info 字典，失敗返回空字典
        """
        # 檢查熔斷
        if cls.is_circuit_breaker_active():
            return {}
        
        ticker_symbol = ticker_obj.ticker if hasattr(ticker_obj, 'ticker') else str(ticker_obj)
        cache_key = f"{ticker_symbol}_info"
        
        # 檢查記憶體快取
        if cache_key in cls._cache:
            cached = cls._cache[cache_key]
            if time.time() - cached['timestamp'] < cls._cache_ttl:
                cls._total_cache_hits += 1
                return cached['data'].copy()

        # B2 #3：檢查當日磁碟快取（.info 盤中幾乎不變，跨執行/重啟仍有效）
        _disk = cls._info_disk_get(ticker_symbol)
        if _disk is not None:
            cls._total_cache_hits += 1
            cls._cache[cache_key] = {'data': _disk, 'timestamp': time.time()}
            return _disk.copy()

        # 速率限制
        current_time = time.time()
        time_since_last = current_time - cls._last_request_time
        if time_since_last < cls._min_interval:
            time.sleep(cls._min_interval - time_since_last)

        try:
            cls._last_request_time = time.time()
            cls._total_requests += 1

            info = ticker_obj.info

            # 成功，重置失敗計數並存入快取
            cls._consecutive_failures = 0
            cls._cache[cache_key] = {
                'data': info.copy() if info else {},
                'timestamp': time.time()
            }
            # B2 #3：寫入當日磁碟快取
            if info:
                cls._info_disk_put(ticker_symbol, info)

            return info if info else {}
            
        except Exception as e:
            error_str = str(e).lower()
            cls._total_failures += 1
            
            if 'rate' in error_str or 'limit' in error_str or 'too many' in error_str:
                cls._consecutive_failures += 1
                if cls._consecutive_failures >= cls._max_failures:
                    cls.trigger_circuit_breaker(f"info 請求連續 {cls._consecutive_failures} 次失敗")
            
            print(f"⚠️ [YFinance] 取得 info 失敗: {e}")
            return {}
    
    # ── B2 #3：基本面 .info 跨日磁碟快取 ──────────────────────────────
    @classmethod
    def _info_disk_path(cls):
        import os as _os
        return _os.path.join(
            _os.path.dirname(_os.path.abspath(__file__)), 'info_cache.json'
        )

    @classmethod
    def _load_info_disk_cache(cls):
        """載入當日磁碟快取（檔案含日期，跨日自動失效）。"""
        import os as _os, json as _json, datetime as _dt
        today = _dt.date.today().isoformat()
        if cls._info_disk_cache is not None and cls._info_disk_cache_date == today:
            return
        cls._info_disk_cache = {}
        cls._info_disk_cache_date = today
        try:
            p = cls._info_disk_path()
            if _os.path.exists(p):
                with open(p, 'r', encoding='utf-8') as f:
                    raw = _json.load(f)
                if raw.get('date') == today:
                    cls._info_disk_cache = raw.get('data', {}) or {}
        except Exception:
            cls._info_disk_cache = {}

    @classmethod
    def _info_disk_get(cls, ticker_symbol):
        cls._load_info_disk_cache()
        return cls._info_disk_cache.get(ticker_symbol)

    @classmethod
    def _info_disk_put(cls, ticker_symbol, info):
        import json as _json
        cls._load_info_disk_cache()
        try:
            # 僅保留可 JSON 序列化的純量欄位，避免寫入失敗
            slim = {k: v for k, v in (info or {}).items()
                    if isinstance(v, (str, int, float, bool, type(None)))}
            cls._info_disk_cache[ticker_symbol] = slim
            with open(cls._info_disk_path(), 'w', encoding='utf-8') as f:
                _json.dump({'date': cls._info_disk_cache_date,
                            'data': cls._info_disk_cache}, f, ensure_ascii=False)
        except Exception:
            pass  # 磁碟快取寫入失敗不影響主流程

    @classmethod
    def clear_cache(cls):
        """清除快取"""
        cls._cache.clear()
        cls._info_disk_cache = None
        cls._info_disk_cache_date = None
        print(f"[YFinance] 快取已清除")
    
    @classmethod
    def reset_circuit_breaker(cls):
        """手動重置熔斷"""
        cls._circuit_breaker_triggered = False
        cls._consecutive_failures = 0
        cls._circuit_breaker_until = 0
        print(f"[YFinance] 熔斷已手動重置")
    
    @classmethod
    def get_stats(cls) -> dict:
        """取得統計資訊"""
        return {
            'total_requests': cls._total_requests,
            'cache_hits': cls._total_cache_hits,
            'failures': cls._total_failures,
            'consecutive_failures': cls._consecutive_failures,
            'circuit_breaker_active': cls.is_circuit_breaker_active(),
            'circuit_breaker_remaining': cls.get_circuit_breaker_remaining(),
            'cache_size': len(cls._cache)
        }
