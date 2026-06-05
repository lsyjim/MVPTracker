"""
config.py - Configuration Module (v4.4 升級版)
"""

# ============================================================================
# v4.0 新增：全域配置參數
# ============================================================================

class QuantConfig:
    """量化系統配置參數"""
    
    # 風險計算參數
    RISK_DATA_YEARS = 2  # 風險指標計算使用的歷史數據年數
    VAR_CONFIDENCE = 0.95  # VaR 信心水準
    
    # 無風險利率（年化，可動態更新）
    RISK_FREE_RATE = 0.045  # 預設 4.5%（2024年美國10年期公債收益率約水準）
    
    # PE Band 計算參數
    PE_HISTORY_YEARS = 5  # PE 歷史區間計算年數
    
    # 成交量異常判斷
    VOLUME_SPIKE_THRESHOLD = 2.0  # 爆量定義：大於 20 日均量的倍數
    VOLUME_MA_PERIOD = 20  # 成交量移動平均期數
    
    # ADX 趨勢判斷
    ADX_TREND_THRESHOLD = 25  # ADX > 25 視為趨勢盤
    ADX_STRONG_TREND = 40  # ADX > 40 視為強趨勢
    
    # 策略評分權重（v4.0修正：降低回測績效權重，增加穩定性權重）
    WEIGHT_APPLICABILITY = 0.30  # 當前適用性權重
    WEIGHT_PERFORMANCE = 0.35  # 歷史績效權重（從 0.6 降低）
    WEIGHT_STABILITY = 0.35  # 穩定性權重（新增：使用 Sharpe Ratio）
    
    # 籌碼面緩存設定
    CHIP_CACHE_HOURS = 8  # 籌碼數據緩存小時數（同一天內有效）
    
    # 大盤代碼
    MARKET_INDEX_TW = "^TWII"  # 台股加權指數
    MARKET_INDEX_US = "^GSPC"  # S&P 500
    
    # v4.1 新增：波段分析參數
    WAVE_MA_PERIOD = 55  # 波段判斷用均線週期
    THREE_BAR_LOOKBACK = 2  # 三盤突破/跌破回看天數
    VOLUME_SPIKE_WAVE = 2.0  # 波段爆量判斷倍數
    
    # v4.2 新增：均值回歸與乖離模組參數 (Mean Reversion & Bias Module)
    # 左側買進訊號參數
    BIAS_OVERSOLD_THRESHOLD = -10.0  # 負乖離閾值（%），低於此值視為超跌
    RSI_OVERSOLD_LEVEL = 25  # RSI 超賣水準
    LOWER_SHADOW_RATIO = 2.0  # 長下影線判斷（下影線 > 實體 * 此倍數）
    VOLUME_REVERSAL_RATIO = 1.5  # 止跌爆量判斷（> 5日均量的倍數）
    
    # 左側賣出訊號參數
    BIAS_OVERBOUGHT_THRESHOLD = 15.0  # 正乖離閾值（%），高於此值視為過熱
    RSI_OVERBOUGHT_LEVEL = 75  # RSI 超買水準（用於背離判斷）
    HIGH_VOL_NO_RISE_RATIO = 2.0  # 高檔爆量不漲判斷（> 5日均量的倍數）
    UPPER_SHADOW_RATIO = 2.0  # 長上影線判斷（上影線 > 實體 * 此倍數）
    
    # 雙軌出場參數
    TREND_EXIT_MA = 20  # 趨勢出場參考均線
    TARGET_RR_RATIO = 3.0  # 積極停利的風險回報比閾值
    
    # v4.3 新增：多因子決策矩陣參數 (Multi-Factor Decision Matrix)
    # 乖離位置判斷閾值
    BIAS_HIGH_THRESHOLD = 15.0  # 高位乖離（過熱）
    BIAS_NEUTRAL_HIGH = 5.0  # 中性偏高
    BIAS_NEUTRAL_LOW = -5.0  # 中性偏低
    BIAS_LOW_THRESHOLD = -10.0  # 低位乖離（超跌）
    BIAS_DEEP_LOW = -15.0  # 深度超跌
    
    # 黃金買點條件
    GOLDEN_BUY_BIAS_MAX = 2.0  # 黃金買點乖離上限
    GOLDEN_BUY_BIAS_MIN = -5.0  # 黃金買點乖離下限
    GOLDEN_BUY_RSI_MAX = 60  # 黃金買點 RSI 上限（v4.4.3 放寬：55→60，適應強勢股回檔）
    
    # 濾網參數
    MIN_RR_RATIO = 1.5  # 最低可接受風險回報比
    VOLUME_SHRINK_RATIO = 0.7  # 量縮判斷（< 5日均量 * 此值）
    ADX_RANGE_THRESHOLD = 20  # 盤整判斷 ADX 閾值

    # ============================================================================
    # v4.4 新增：回測成本模型參數
    # ============================================================================
    ENABLE_COST_MODEL = True  # 啟用成本模型
    COMMISSION_RATE = 0.001425  # 手續費率（雙邊，台股約 0.1425%）
    TAX_RATE = 0.003  # 交易稅率（台股賣出 0.3%）
    SLIPPAGE_MODEL = "vol_liq"  # 滑價模型：fixed / vol_liq
    SLIPPAGE_BASE = 0.001  # 固定滑價基礎 (0.1%)
    SLIPPAGE_K1 = 0.5  # ATR 連動係數
    SLIPPAGE_K2 = 0.1  # 流動性連動係數
    
    # ============================================================================
    # v4.4 新增：風險管理模組參數
    # ============================================================================
    ENABLE_RISK_MANAGER = True  # 啟用風險管理模組
    RISK_PER_TRADE = 0.01  # 每筆交易最大風險（佔資金比例，1%）
    ATR_PERIOD = 14  # ATR 計算週期
    ATR_K_STOP = 2.0  # ATR 止損倍數
    R_MULTIPLE_TARGET = 2.0  # 預設 R 倍數目標（停利）
    SWING_LOOKBACK = 20  # 波段高低點回看天數
    
    # ============================================================================
    # v4.4 新增：量價分析模組參數
    # ============================================================================
    ENABLE_VOLUME_PRICE_ANALYSIS = True  # 啟用量價分析
    VP_VOLUME_UP_THRESHOLD = 1.2  # 量增判斷（> 20日均量的倍數）
    VP_VOLUME_DOWN_THRESHOLD = 0.9  # 量縮判斷（< 20日均量的倍數）
    VP_BREAKOUT_VOLUME_THRESHOLD = 1.5  # 突破放量判斷
    VP_LARGE_VOLUME_THRESHOLD = 2.0  # 爆量判斷
    VP_PRICE_UP_DAYS = 3  # 價格上漲判斷天數
    VP_PRICE_DOWN_DAYS = 3  # 價格下跌判斷天數
    
    # ============================================================================
    # v4.4 新增：流動性過濾（Gate）參數
    # ============================================================================
    ENABLE_LIQUIDITY_GATE = True  # 啟用流動性過濾
    MIN_AVG_VOLUME_20 = 500  # 最低 20 日均量（張）
    MIN_TURNOVER_20 = 5000000  # 最低 20 日均成交金額（元）
    
    # ============================================================================
    # v4.4 新增：動態閾值參數
    # ============================================================================
    DYNAMIC_THRESHOLD_WINDOW = 60  # 滾動窗口（用於 z-score/percentile 計算）
    BIAS_PERCENTILE_HIGH = 90  # BIAS 高位百分位
    BIAS_PERCENTILE_LOW = 10  # BIAS 低位百分位
    
    # ============================================================================
    # v4.4.3 新增：動態成交量閾值參數 (Z-Score 方式)
    # ============================================================================
    VOLUME_ZSCORE_SPIKE = 2.5  # 成交量 Z-Score 爆量閾值（標準差倍數）
    VOLUME_ZSCORE_HEAVY = 3.0  # 成交量 Z-Score 大爆量閾值
    VOLUME_ZSCORE_WINDOW = 60  # Z-Score 計算滾動窗口
    
    # ============================================================================
    # v4.4.3 新增：布林通道帶寬參數 (Bollinger Bandwidth)
    # ============================================================================
    BB_SQUEEZE_PERCENTILE = 20  # 帶寬處於歷史低檔（壓縮）的百分位閾值
    BB_SQUEEZE_LOOKBACK = 120  # 帶寬歷史回顧天數
    
    # ============================================================================
    # v4.4.3 新增：時間停損參數
    # ============================================================================
    TIME_STOP_DAYS = 5  # 時間停損天數（進場後 N 天未獲利則提醒）
    TIME_STOP_THRESHOLD = 0.0  # 成本區定義（獲利 < 此百分比視為未脫離成本區）
    
    # ============================================================================
    # v4.4.6 新增：形態分析模組參數 (Pattern Analysis)
    # ============================================================================
    ENABLE_PATTERN_ANALYSIS = True  # 啟用形態分析
    PATTERN_LOOKBACK_DAYS = 60  # 形態回看天數
    PATTERN_PEAK_WINDOW = 5  # 高低點識別窗口
    PATTERN_TOLERANCE = 0.03  # 高低點誤差容許度（3%）
    PATTERN_VOLUME_CONFIRM = 1.0  # 形態確認量能倍數（需 >= 5日均量）
    
    # 形態權重配置（加權評分制）
    WEIGHT_PATTERN = 0.40  # 形態學權重（最高優先級）
    WEIGHT_WAVE = 0.30  # 波段策略權重
    WEIGHT_VOLUME = 0.20  # 量價分析權重
    WEIGHT_INDICATOR = 0.10  # 輔助指標權重
    
    # 形態分數影響
    PATTERN_CONFIRMED_SCORE = 40  # 形態確立對總分的影響（±40分）
    PATTERN_FORMING_SCORE = 15  # 形態形成中對總分的影響（±15分）
    
    # 做空設定（台股限制多，預設關閉）
    ENABLE_SHORT_SELLING = False  # 是否啟用做空訊號
    
    # ============================================================================
    # v4.4.7 新增：交易風控強化參數
    # ============================================================================
    
    # 1. 數據源驗證
    REQUIRE_REALTIME_DATA = True  # 是否要求即時數據（False 允許延遲數據）
    MAX_DATA_DELAY_MINUTES = 15  # 最大可接受的數據延遲（分鐘）
    WARN_ON_DELAYED_DATA = True  # 延遲數據時是否警告
    
    # 2. 盤整盤過濾（避免雙巴）
    RANGE_MARKET_ADX_THRESHOLD = 20  # ADX < 此值視為盤整盤（auto_trader 使用）
    
    # 4. 移動停利（Trailing Stop）
    TRAILING_STOP_ENABLED = True  # 是否啟用移動停利
    TRAILING_STOP_ACTIVATION_PCT = 0.10  # 啟動移動停利的獲利百分比（10%）
    TRAILING_STOP_DISTANCE_PCT = 0.05  # 移動停利回撤百分比（從最高點回落 5%）
    TRAILING_STOP_USE_MA5 = True  # 是否使用跌破 5 日線作為停利訊號
    TRAILING_STOP_USE_MA10 = False  # 是否使用跌破 10 日線作為停利訊號
    
    # 5. 目標價動態調整
    DYNAMIC_TARGET_ENABLED = True  # 是否啟用動態目標價
    DYNAMIC_TARGET_STEP_PCT = 0.08  # 目標價向上調整步幅（8%）
    DYNAMIC_TARGET_MAX_EXTENSIONS = 3  # 最大向上調整次數


# ============================================================================
# v4.3 新增：即時股價爬蟲（Yahoo 股市）
# ============================================================================



# ============================================================================
# MVPTracker — 今日精選股門檻（可調）
# ============================================================================
PICKS = {
    "grades": ("grade_A", "grade_B"),  # 評級 A/B
    "rs_min": 80,                       # 相對強度
    "inst_total_5d_min": 0,            # 近5日法人合計 > 此值（買超）
    "cons_buy_min": 2,                 # 外資/投信連續買超天數 ≥
    "vol_ratio_min": 1.2,             # 量比（今日量/20日均量）≥
    "bias_max": 15.0,                 # 20日乖離 ≤ %（且非背離）
    "top_n": 10,                       # 全中清單取前 N
    # 綜合分權重（RS、法人強度、量比、5日動能）
    "w_rs": 0.4, "w_inst": 0.2, "w_vol": 0.2, "w_mom": 0.2,
}
