"""
analyzers.py - Analysis Tools Module
"""

import datetime
import warnings

warnings.filterwarnings('ignore', category=FutureWarning)

import yfinance as yf
import pandas as pd
import numpy as np
from scipy.stats import linregress

from config import QuantConfig
from decision_engine import ThreeLayerEngine   # v2: 三層決策引擎

class DecisionMatrix:
    """
    多因子決策矩陣 v4.5.20 - 因子正交化版
    
    v4.5.20 因子正交化 (Orthogonalization)：
    - 突破類訊號互斥：三盤突破、爆量長紅、VP05 只取最強一個
    - RS 階梯式評分：領頭羊(+15) vs 強於大盤(+10) 互斥
    - 布林壓縮+突破：合併為單一訊號（+10 或 +15）
    - 解決同一事件被多個因子重複計分的問題
    - 評分系統更具鑑別度
    
    v4.5.19 高盛級量化優化：
    - KD 動態權重：低檔強勢金叉 +8，普通金叉 +3
    - MACD 背離偵測：底背離 +15，頂背離 -15
    - RSI 區間判斷：多頭控盤區 (40~80 + MA20上揚) +8
    - 相對強度 (RS)：核心選股因子
    - 布林壓縮：蓄勢待發訊號
    - 高階量價訊號：VP09吸籌、VP05帶量突破、VP04良性回檔
    
    v4.5.17 高盛級量化整合：
    - 新增 VCPScanner、RelativeStrengthCalculator、ATRStopLossCalculator
    - 邏輯審計：確認 Scenario C 評分上限 70 分正確
    - 確認 W底/M頭 時間跨度檢查 (double_min_spacing=15)
    - 形態失效檢查、形成超時檢查均已正確實作
    
    v4.5.16 形態失效檢查 (Pattern Invalidation)：
    - W底/M頭/頭肩頂/頭肩底 加入「形態失效檢查」
    - 價格突破停損點 → 形態失效，不再回報 FORMING
    - 新增「形成超時檢查」：超過 30 天未突破頸線視為無效
    - 解決「形態已被破壞但仍顯示 FORMING」的邏輯漏洞
    
    v4.5.15 效能優化：
    - _find_significant_points 使用 scipy.signal.argrelextrema
    - 極值點尋找速度提升 10-50 倍
    
    v4.5.14 重大更新：
    - PatternAnalyzer 加入「陳舊突破檢查」(Stale Breakout Check)
    - 新增 PULLBACK_TEST 狀態，過濾「漲多拉回」假訊號
    - 分數區間：High ≥65, Mid 45-65, Low ≤45
    
    整合「市場背景 (Regime)」與「形態訊號 (Pattern)」的交叉場景判斷，
    解決訊號矛盾問題，並優化目標價計算邏輯。
    
    新版場景定義（基於 Regime x Signal 交叉）：
    - 場景 A：順勢多頭 (Strong Bull) - 多頭 + 突破訊號
    - 場景 B：多頭拉回 (Bullish Pullback) - 多頭 + 回檔修正
    - 場景 C：空頭反彈 (Bearish Rebound) - 空頭 + 底部形態 ★解決矛盾關鍵
    - 場景 D：高檔反轉 (Reversal Risk) - 多頭 + 頭部形態
    - 場景 E：順勢空頭 (Strong Bear) - 空頭 + 跌破訊號
    - 場景 F：盤整震盪 (Range) - ADX < 20
    
    新增機制：
    1. 趨勢濾網：空頭反彈場景評分上限 70 分
    2. 成交量確認：量縮訊號權重減半
    3. 訊號冷卻期：避免忽買忽賣
    4. 目標價動態推移：解決目標價過低問題
    """
    
    # 趨勢狀態常量
    TREND_BULL = "Bull"
    TREND_BEAR = "Bear"
    TREND_RANGE = "Range"
    
    # 乖離位置常量
    BIAS_HIGH = "High"
    BIAS_NEUTRAL = "Neutral"
    BIAS_LOW = "Low"
    BIAS_DEEP_LOW = "DeepLow"
    
    # 場景代碼（v4.4.7 重新定義）
    SCENARIO_STRONG_BULL = 'A'      # 順勢多頭
    SCENARIO_BULLISH_PULLBACK = 'B'  # 多頭拉回
    SCENARIO_BEARISH_REBOUND = 'C'   # 空頭反彈
    SCENARIO_REVERSAL_RISK = 'D'     # 高檔反轉
    SCENARIO_STRONG_BEAR = 'E'       # 順勢空頭
    SCENARIO_RANGE = 'F'             # 盤整震盪
    
    # 訊號冷卻期記憶（類變數）
    _last_signals = {}  # {symbol: {'action': 'BUY/SELL', 'score': 75, 'date': 'YYYY-MM-DD'}}
    
    @staticmethod
    def analyze(result):
        """
        v2.0 - 三層決策引擎

        完全取代舊版的雙軌評分 + 查表邏輯。
        現在由 ThreeLayerEngine 統一決策，不再有多套規則互搶主導。

        三層架構：
          Layer 1 方向分 → 否決門檻 40（趨勢不對直接跳過）
          Layer 2 位置分 → 否決門檻 40（過熱/位置差等待）
          Layer 3 時機分 → 輸出 A/B/C 分級（而非 0-100 的模糊分數）

        Args:
            result: QuickAnalyzer.analyze_stock() 的回傳結果

        Returns:
            dict: 決策結果，包含 three_layer 詳細分解供報告顯示
        """
        return ThreeLayerEngine.analyze(result)
    
    @staticmethod
    def calculate_price_targets(result, scenario_result):
        """
        v4.4.7 新增：計算動態目標價
        
        解決「目標價過低」問題：
        1. 若現價已超過目標價，自動向上推移
        2. 形態學測幅優先於均線目標價
        """
        current_price = result.get('current_price', 0)
        if current_price <= 0:
            return {'available': False}
        
        # 取得形態分析
        pattern = result.get('pattern_analysis', {})
        pattern_detected = pattern.get('detected', False)
        pattern_type = pattern.get('pattern_type', '')
        pattern_target = pattern.get('target_price', 0)
        pattern_stop = pattern.get('stop_loss', 0)
        neckline = pattern.get('neckline_price', 0)
        
        # 取得支撐壓力位
        sr = result.get('support_resistance', {})
        resistance1 = sr.get('resistance1', 0)
        support1 = sr.get('support1', 0)
        
        # 取得均線
        tech = result.get('technical', {})
        ma20 = tech.get('ma20', 0)
        ma60 = tech.get('ma60', 0)
        
        # 初始化目標價
        target_price = 0
        stop_loss = 0
        target_source = ''
        target_note = ''
        
        # ============================================================
        # 1. 形態學測幅（最高優先）
        # ============================================================
        if pattern_detected and pattern_target > 0:
            if pattern_type == 'bottom':
                # W底/頭肩底：目標價 = 頸線 + (頸線 - 最低點)
                target_price = pattern_target
                stop_loss = pattern_stop
                target_source = f'{pattern.get("pattern_name", "底部形態")}測幅'
                
                # 檢查目標價是否已達標
                if current_price >= target_price:
                    # 動態推移：目標價向上移動 10%
                    old_target = target_price
                    target_price = round(current_price * 1.10, 2)
                    target_note = f'原目標${old_target:.2f}已達標，動態追蹤中'
                    target_source += '（動態推移）'
                    
            elif pattern_type == 'top':
                # M頭/頭肩頂：目標價 = 頸線 - (最高點 - 頸線)
                target_price = pattern_target
                stop_loss = pattern_stop
                target_source = f'{pattern.get("pattern_name", "頭部形態")}測幅'
        
        # ============================================================
        # 2. 均線目標價（次優先）
        # ============================================================
        if target_price <= 0:
            # 多頭：使用上方壓力位或 MA60 * 1.1
            if result.get('technical', {}).get('signal') == '偏多':
                if resistance1 > current_price:
                    target_price = resistance1
                    target_source = '近期壓力位'
                elif ma60 > 0:
                    target_price = round(ma60 * 1.10, 2)
                    target_source = 'MA60 上方 10%'
                else:
                    target_price = round(current_price * 1.08, 2)
                    target_source = '預估上漲 8%'
                
                # 停損：下方支撐位或 MA20 下方
                if support1 > 0 and support1 < current_price:
                    stop_loss = support1
                elif ma20 > 0:
                    stop_loss = round(ma20 * 0.97, 2)
                else:
                    stop_loss = round(current_price * 0.92, 2)
            
            # 空頭：使用下方支撐位
            else:
                if support1 > 0 and support1 < current_price:
                    target_price = support1
                    target_source = '近期支撐位'
                elif ma20 > 0:
                    target_price = round(ma20 * 0.95, 2)
                    target_source = 'MA20 下方 5%'
                else:
                    target_price = round(current_price * 0.95, 2)
                    target_source = '預估下跌 5%'
                
                stop_loss = round(current_price * 1.05, 2)  # 反彈 5% 止損
        
        # ============================================================
        # 3. 動態推移檢查（若現價已超過目標價）
        # ============================================================
        if target_price > 0 and current_price >= target_price and pattern_type != 'top':
            old_target = target_price
            # 使用 MA60 * 1.1 或現價 * 1.1
            if ma60 > current_price:
                target_price = round(ma60 * 1.10, 2)
            else:
                target_price = round(current_price * 1.10, 2)
            target_note = f'原目標${old_target:.2f}已達標，動態追蹤中'
            target_source += '（動態推移）'
        
        # 計算潛在報酬與風險
        potential_gain = ((target_price - current_price) / current_price * 100) if target_price > 0 else 0
        potential_loss = ((current_price - stop_loss) / current_price * 100) if stop_loss > 0 else 0
        rr_ratio = abs(potential_gain / potential_loss) if potential_loss != 0 else 0
        
        return {
            'available': True,
            'target_price': round(target_price, 2) if target_price > 0 else None,
            'stop_loss': round(stop_loss, 2) if stop_loss > 0 else None,
            'target_source': target_source,
            'target_note': target_note,
            'potential_gain_pct': round(potential_gain, 2),
            'potential_loss_pct': round(potential_loss, 2),
            'rr_ratio': round(rr_ratio, 2),
            'current_price': current_price
        }
    
    @staticmethod
    def _calculate_decision_variables(result):
        """計算核心決策變數"""
        
        # 1. Trend_Status (趨勢狀態)
        trend_status = DecisionMatrix._determine_trend_status(result)
        
        # 2. Position_Bias (乖離位置)
        position_bias, bias_20 = DecisionMatrix._determine_position_bias(result)
        
        # 3. Risk_Reward_Ratio (RR值)
        rr_ratio = DecisionMatrix._calculate_rr_ratio(result)
        
        # 4. Vol_Anomaly (量能異常)
        vol_anomaly = DecisionMatrix._detect_volume_anomaly(result)
        
        # 5. RSI 狀態
        rsi = result.get('technical', {}).get('rsi', 50)
        
        # 6. 左側訊號狀態
        mr = result.get('mean_reversion', {})
        left_buy_triggered = mr.get('left_buy_signal', {}).get('triggered', False) if mr.get('available') else False
        left_sell_triggered = mr.get('left_sell_signal', {}).get('triggered', False) if mr.get('available') else False
        
        # 7. ADX 狀態
        adx = result.get('technical', {}).get('adx', 25)
        
        return {
            'trend_status': trend_status,
            'position_bias': position_bias,
            'bias_20': bias_20,
            'rr_ratio': rr_ratio,
            'vol_anomaly': vol_anomaly,
            'rsi': rsi,
            'adx': adx,
            'left_buy_triggered': left_buy_triggered,
            'left_sell_triggered': left_sell_triggered,
            # v4.4.1 新增：量價分析整合
            'volume_price': DecisionMatrix._get_volume_price_factors(result)
        }
    
    @staticmethod
    def _get_volume_price_factors(result):
        """v4.4.1 新增：從量價分析中提取決策因子"""
        vp = result.get('volume_price', {})
        if not vp.get('available'):
            return {
                'available': False,
                'vp_score': 0,
                'signals': [],
                'high_risk_signals': [],
                'bullish_count': 0,
                'bearish_count': 0
            }
        
        signals = vp.get('signals', [])
        vp_score = vp.get('vp_score', 0)
        
        # 分類訊號
        bullish_signals = [s for s in signals if s.get('direction') == 'bullish']
        bearish_signals = [s for s in signals if s.get('direction') == 'bearish']
        high_risk_signals = [s for s in signals if s.get('severity', 0) >= 4]
        
        return {
            'available': True,
            'vp_score': vp_score,
            'signals': signals,
            'high_risk_signals': high_risk_signals,
            'bullish_count': len(bullish_signals),
            'bearish_count': len(bearish_signals),
            'has_breakdown': any(s.get('code') == 'VP08' for s in signals),  # 放量跌破
            'has_supply_overhang': any(s.get('code') == 'VP07' for s in signals),  # 放量不漲
            'has_valid_breakout': any(s.get('code') == 'VP05' for s in signals),  # 帶量突破
            'has_gap_down': any(s.get('code') == 'VP12' for s in signals),  # 跳空下跌
            'decision_hint': vp.get('decision_hint', ''),
            'risk_notes': vp.get('risk_notes', '')
        }
    
    @staticmethod
    def _determine_trend_status(result):
        """判斷趨勢狀態"""
        tech = result.get('technical', {})
        wave = result.get('wave_analysis', {})
        market = result.get('market_regime', {})
        
        trend = tech.get('trend', '')
        signal = tech.get('signal', '')
        adx = tech.get('adx', 25)
        
        # 三盤突破/跌破優先
        if wave.get('available'):
            breakout = wave.get('breakout_signal', {}).get('detected', False)
            breakdown = wave.get('breakdown_signal', {}).get('detected', False)
            is_bullish_env = wave.get('is_bullish_env', False)
            
            if breakdown:
                return DecisionMatrix.TREND_BEAR
            if breakout and is_bullish_env:
                return DecisionMatrix.TREND_BULL
        
        # ADX 判斷盤整
        if adx < QuantConfig.ADX_RANGE_THRESHOLD:
            return DecisionMatrix.TREND_RANGE
        
        # 均線排列判斷
        if '上升' in trend or signal == '偏多':
            return DecisionMatrix.TREND_BULL
        elif '下降' in trend or signal == '偏空':
            return DecisionMatrix.TREND_BEAR
        else:
            return DecisionMatrix.TREND_RANGE
    
    @staticmethod
    def _determine_position_bias(result):
        """判斷乖離位置"""
        mr = result.get('mean_reversion', {})
        
        if mr.get('available'):
            bias_20 = mr.get('bias_analysis', {}).get('bias_20', 0)
        else:
            # 自行計算
            current_price = result.get('current_price', 0)
            ma20 = result.get('technical', {}).get('ma20', current_price)
            bias_20 = ((current_price - ma20) / ma20 * 100) if ma20 > 0 else 0
        
        if bias_20 > QuantConfig.BIAS_HIGH_THRESHOLD:
            return DecisionMatrix.BIAS_HIGH, bias_20
        elif bias_20 < QuantConfig.BIAS_DEEP_LOW:
            return DecisionMatrix.BIAS_DEEP_LOW, bias_20
        elif bias_20 < QuantConfig.BIAS_LOW_THRESHOLD:
            return DecisionMatrix.BIAS_LOW, bias_20
        else:
            return DecisionMatrix.BIAS_NEUTRAL, bias_20
    
    @staticmethod
    def _calculate_rr_ratio(result):
        """計算風險回報比"""
        sr = result.get('support_resistance', {})
        current_price = result.get('current_price', 0)
        
        take_profit = sr.get('take_profit', current_price * 1.1)
        stop_loss = sr.get('stop_loss', current_price * 0.95)
        
        # 確保數值有效
        if isinstance(take_profit, str):
            take_profit = current_price * 1.1
        if isinstance(stop_loss, str):
            stop_loss = current_price * 0.95
        
        potential_gain = take_profit - current_price
        potential_loss = current_price - stop_loss
        
        if potential_loss > 0:
            rr_ratio = potential_gain / potential_loss
        else:
            rr_ratio = 0
        
        return round(rr_ratio, 2)
    
    @staticmethod
    def _detect_volume_anomaly(result):
        """偵測量能異常"""
        vol_analysis = result.get('volume_analysis', {})
        mr = result.get('mean_reversion', {})
        
        anomalies = []
        
        # 1. 創高量縮（假突破疑慮）
        wave = result.get('wave_analysis', {})
        if wave.get('available'):
            breakout = wave.get('breakout_signal', {})
            if breakout.get('detected') and not breakout.get('volume_confirmed'):
                anomalies.append({
                    'type': 'breakout_low_volume',
                    'message': '創高量縮，多頭力道可疑',
                    'severity': 'warning'
                })
        
        # 2. 高檔爆量不漲
        if mr.get('available'):
            left_sell = mr.get('left_sell_signal', {})
            if left_sell.get('triggered'):
                for reason in left_sell.get('trigger_reasons', []):
                    if '爆量' in reason and ('收黑' in reason or '上影' in reason):
                        anomalies.append({
                            'type': 'high_volume_no_rise',
                            'message': '高檔爆量不漲，賣壓湧現',
                            'severity': 'danger'
                        })
                        break
        
        # 3. 價漲量縮
        if vol_analysis.get('volume_trend') == '量縮':
            vol_ratio = vol_analysis.get('volume_ratio', 1)
            if vol_ratio < QuantConfig.VOLUME_SHRINK_RATIO:
                anomalies.append({
                    'type': 'price_up_volume_down',
                    'message': f'量能萎縮至均量{vol_ratio:.0%}',
                    'severity': 'caution'
                })
        
        return {
            'has_anomaly': len(anomalies) > 0,
            'anomalies': anomalies
        }
    
    @staticmethod
    def generate_report_text(decision_result, result):
        """生成決策報告文字（v4.4.2 增強：加入量價分析）"""
        
        if not decision_result.get('available'):
            return "決策矩陣分析不可用"
        
        dv = decision_result['decision_vars']
        
        lines = []
        lines.append("=" * 50)
        lines.append("【🎯 多因子決策矩陣 v4.4】")
        lines.append("=" * 50)
        
        # 核心決策變數
        lines.append("\n📊 核心決策變數：")
        lines.append(f"  • 趨勢狀態：{dv['trend_status']}")
        lines.append(f"  • 乖離位置：{dv['position_bias']}（{dv['bias_20']:+.1f}%）")
        lines.append(f"  • 風險回報比：{dv['rr_ratio']:.2f}")
        lines.append(f"  • RSI：{dv['rsi']:.0f} | ADX：{dv['adx']:.0f}")
        
        if dv['vol_anomaly']['has_anomaly']:
            lines.append(f"  • ⚠️ 量能異常：{', '.join([a['message'] for a in dv['vol_anomaly']['anomalies']])}")
        
        # v4.4.2 新增：量價分析因子
        vp = dv.get('volume_price', {})
        if vp.get('available'):
            lines.append(f"\n📈 量價分析因子：")
            lines.append(f"  • 量價分數：{vp.get('vp_score', 0):+d}")
            lines.append(f"  • 多方訊號：{vp.get('bullish_count', 0)} 個")
            lines.append(f"  • 空方訊號：{vp.get('bearish_count', 0)} 個")
            if vp.get('high_risk_signals'):
                lines.append(f"  • ⚠️ 高風險訊號：{len(vp['high_risk_signals'])} 個")
            if vp.get('decision_hint'):
                lines.append(f"  • 決策提示：{vp['decision_hint']}")
        
        # 場景判斷
        lines.append(f"\n🎬 觸發場景：{decision_result['scenario']} - {decision_result['scenario_name']}")
        
        # 投資建議
        lines.append(f"\n💡 投資建議：{decision_result['recommendation']}")
        lines.append(f"⏰ 進場時機：{decision_result['action_timing']}")
        lines.append(f"📝 說明：{decision_result['warning_message']}")
        
        # 濾網結果
        if decision_result['downgraded']:
            lines.append(f"\n⚠️ 濾網降級：")
            lines.append(f"  原始建議：{decision_result['original_recommendation']}")
            for f in decision_result['filters_applied']:
                lines.append(f"  • [{f['filter']}] {f['reason']} → {f['action']}")
        
        lines.append(f"\n信心度：{decision_result['confidence']}")
        lines.append("=" * 50)
        
        return "\n".join(lines)
    
    # ========================================================================
    # v4.4.8 新增：雙軌評分系統 (Dual-Track Scoring System)
    # ========================================================================
    
    # 短線波段評分權重
    # ========================================
    # v4.5.19 高盛級量化優化權重
    # ========================================
    SHORT_TERM_WEIGHTS = {
        # === 形態學 (維持高權重) ===
        'PATTERN_BOTTOM_CONFIRMED': 25,      # W底/頭肩底確立
        'PATTERN_BOTTOM_FORMING': 10,        # 底部形態形成中
        'PATTERN_TOP_CONFIRMED': -30,        # M頭/頭肩頂確立
        'PATTERN_TOP_FORMING': -15,          # 頭部形態形成中
        
        # === 波段 ===
        'WAVE_BREAKOUT': 20,                 # 三盤突破/均線多排
        'WAVE_BULLISH_ENV': 8,               # 多頭環境
        'WAVE_BREAKDOWN': -20,               # 三盤跌破
        'WAVE_BEARISH_ENV': -10,             # 空頭環境
        
        # === 量能 (新增高階訊號) ===
        'VOLUME_BULLISH_SURGE': 15,          # 爆量長紅
        'VOLUME_BEARISH_SURGE': -15,         # 爆量長黑
        'VOLUME_BREAKOUT_CONFIRM': 8,        # 突破有量確認
        'VOLUME_VP09_ACCUMULATION': 10,      # VP09 吸籌跡象 (新增)
        'VOLUME_VP05_VALID_BREAKOUT': 12,    # VP05 帶量突破 (新增)
        'VOLUME_VP04_SELLING_EASING': 5,     # VP04 良性回檔 (新增)
        'VOLUME_HIGH_RVOL_AT_KEY': 5,        # 關鍵位置爆量 (新增)
        'VOLUME_NO_RISE': -10,               # 放量不漲
        
        # === 技術指標 (動態權重) ===
        'TECH_KD_LOW_GOLDEN': 8,             # KD 低檔強勢金叉 K<20 (新增)
        'TECH_KD_GOLDEN_CROSS': 3,           # KD 普通金叉 (降權 5→3)
        'TECH_KD_DEATH_CROSS': -5,           # KD 死亡交叉
        'TECH_RSI_BULLISH_ZONE': 8,          # RSI 多頭區間 40~80 (新增)
        'TECH_RSI_GOLDEN_CROSS': 3,          # RSI 回升 (降權 5→3)
        'TECH_RSI_DIVERGENCE': -5,           # RSI 背離
        'TECH_MACD_BULLISH': 5,              # MACD 多頭
        'TECH_MACD_BEARISH': -5,             # MACD 空頭
        'TECH_MACD_BULLISH_DIVERGENCE': 15,  # MACD 底背離 (新增)
        'TECH_MACD_BEARISH_DIVERGENCE': -15, # MACD 頂背離 (新增)
        
        # === 動能/相對強度 (核心新增) ===
        'MOMENTUM_RS_POSITIVE': 10,          # 強於大盤 (新增)
        'MOMENTUM_RS_LEADER': 10,            # 市場領頭羊 RS>80 (新增)
        'MOMENTUM_BB_SQUEEZE': 10,           # 布林壓縮 (新增)
        'MOMENTUM_BB_SQUEEZE_BREAKOUT': 5,   # 壓縮後突破加成 (新增)
        
        # === 風險 ===
        'RISK_BIAS_OVERHEATED': -12,         # 乖離率過熱 (調整 -15→-12)
        'RISK_BIAS_OVERSOLD': 5,             # 乖離率超跌 (新增)
        'RISK_RSI_OVERBOUGHT': -8,           # RSI 超買
        'RISK_VOLUME_SHRINK': -5,            # 量縮風險
        'RISK_LOW_RR': -8,                   # 風險回報比不佳
    }
    
    # 中長線投資評分權重
    LONG_TERM_WEIGHTS = {
        # 年線趨勢（做多）
        'ABOVE_MA240': 20,                   # 站上年線
        'MA240_RISING': 15,                  # 年線上揚
        'ABOVE_MA120': 10,                   # 站上半年線
        'MA_BULLISH_ALIGN': 10,              # 均線多頭排列
        
        # 年線趨勢（做空）
        'BELOW_MA240': -20,                  # 跌破年線
        'MA240_FALLING': -15,                # 年線下彎
        'BELOW_MA120': -10,                  # 跌破半年線
        'MA_BEARISH_ALIGN': -10,             # 均線空頭排列
        
        # 價值面（做多）
        'PE_LOW': 20,                        # PE < 15
        'PE_HISTORICAL_LOW': 10,             # PE 位於歷史低檔
        'PB_LOW': 10,                        # PB < 1.5
        'DIVIDEND_HIGH': 8,                  # 高殖利率
        
        # 價值面（做空）
        'PE_HIGH': -15,                      # PE > 30
        'PE_HISTORICAL_HIGH': -10,           # PE 位於歷史高檔
        'PB_HIGH': -8,                       # PB > 5
        
        # 籌碼面（做多）
        'INSTITUTIONAL_BUY_STREAK': 10,      # 法人連買
        'INSTITUTIONAL_BIG_BUY': 8,          # 法人大量買超
        'FOREIGN_NET_BUY': 5,                # 外資淨買
        
        # 籌碼面（做空）
        'INSTITUTIONAL_SELL_STREAK': -10,    # 法人連賣
        'INSTITUTIONAL_BIG_SELL': -8,        # 法人大量賣超
        'FOREIGN_NET_SELL': -5,              # 外資淨賣
    }
    
    # 分數等級定義
    SCORE_LABELS = {
        (90, 100): ('極強', '強力買進', '長線布局'),
        (75, 89): ('偏多', '建議買進', '可長期持有'),
        (60, 74): ('中性偏多', '可考慮買進', '適度布局'),
        (40, 59): ('中性', '觀望', '觀望'),
        (25, 39): ('中性偏空', '謹慎操作', '減碼'),
        (10, 24): ('偏空', '建議賣出', '長線觀望'),
        (0, 9): ('極弱', '清倉', '清倉'),
    }
    
    @staticmethod
    def calculate_short_term_score(result):
        """
        v2.0 - 短線評分：對應三層引擎的 Layer 3（時機分）

        回傳格式與舊版兼容，但分數來源改為時機分級：
          A 級 → 85 分（主攻）
          B 級 → 65 分（追蹤）
          C 級 → 45 分（觀察）
          X   → 35 分（無訊號）

        此方法僅供報告顯示使用，實際買賣決策由 analyze() 主導。

        =====================================================
        [已移除舊版加分制邏輯 v4.5.x]
        =====================================================
        
        【重要】數學邏輯修正 v4.4.9：
        - 先累計所有加減分到 raw_score
        - 最後一步才進行 0-100 的截斷 (Clamp)
        - 避免「分數失真」問題
        
        1. 形態學 (Pattern) - 權重最重
           - W底/頭肩底確立: +25
           - M頭/頭肩頂確立: -30
        
        2. 波段 (Wave) - 趨勢確認
           - 三盤突破/均線多排: +20
           - 三盤跌破: -20
        
        3. 量能 (Volume) - 動能確認
           - 爆量長紅: +15
           - 爆量長黑: -15
        
        4. 技術指標 (Tech) - 輔助確認
           - KD/RSI 黃金交叉: +10
           - 背離: -10
        
        5. 風險 (Risk) - 純扣分項
           - 乖離率過熱: -15
        
        Args:
            result: QuickAnalyzer.analyze_stock() 的回傳結果
        
        Returns:
            dict: 短線波段評分結果
        """
        # v2.0: 直接呼叫三層引擎 Layer 3（時機分）
        timing = ThreeLayerEngine.score_timing(result)
        grade = timing['grade']

        _grade_score = {'A': 85, 'B': 65, 'C': 45, 'X': 35}
        score = _grade_score.get(grade, 45)

        _grade_label = {
            'A': '主攻（強觸發）',
            'B': '追蹤（訊號形成）',
            'C': '觀察（環境偏多）',
            'X': '無訊號',
        }
        label = _grade_label.get(grade, '觀察')
        action = timing['label']
        confidence = 'High' if grade == 'A' else 'Medium' if grade == 'B' else 'Low'

        # 將總加減分（score - 50）分配到各 trigger，供 GUI 明細顯示
        total_adj = score - 50  # e.g. X=-15, C=-5, B=+15, A=+35
        triggers = timing['triggers'] or ['無明確進場訊號']
        n = len(triggers)
        # 平均分配，餘數補到第一項
        per = total_adj // n
        remainder = total_adj - per * n
        components = []
        for i, t in enumerate(triggers):
            s = per + (remainder if i == 0 else 0)
            components.append({
                'name':        t,
                'score':       s,
                'reason':      t,
                'category':    'Timing',
                'is_positive': s >= 0,
            })

        return {
            'score':            score,
            'grade':            grade,
            'label':            label,
            'action':           action,
            'confidence':       confidence,
            'components':       components,
            'breakdown':        {'Timing': total_adj},
            'raw_score':        score,
            'base_score':       50,
            'total_adjustment': total_adj,
        }

        # =====================================================
        # [舊版加分制邏輯已移除，下方為廢棄程式碼，保留供比對]
        # =====================================================
        _DEAD_CODE_START = True  # 此行之後的程式碼不會被執行
        w = DecisionMatrix.SHORT_TERM_WEIGHTS  # noqa
        base_score = 50
        raw_score = base_score  # 使用 raw_score 累計，不提前截斷
        components = []
        
        def add_score(name, score, reason, category):
            """
            累計加減分（不截斷）
            
            修正說明：
            - 舊邏輯：每次加分就 max(0, min(100, ...)) 截斷
            - 問題：超級多頭 (+80) 加小缺點 (-10) = 90分，而非正確的 100分
            - 新邏輯：先累計到 raw_score，最後才截斷
            """
            nonlocal raw_score
            components.append({
                'name': name,
                'score': score,
                'reason': reason,
                'category': category,
                'is_positive': score > 0
            })
            raw_score += score  # 只累加，不截斷
        
        # ========================================
        # 1. 形態學評分 (Pattern)
        # ========================================
        pattern = result.get('pattern_analysis', {})
        if pattern.get('detected'):
            pattern_type = pattern.get('pattern_type', '')
            pattern_status = pattern.get('status', '')
            pattern_name = pattern.get('pattern_name', '')
            pattern_confidence = pattern.get('confidence', 50)
            
            if pattern_type == 'bottom':
                if 'CONFIRMED' in pattern_status:
                    add_score('底部形態確立', w['PATTERN_BOTTOM_CONFIRMED'],
                             f'{pattern_name}突破頸線確立（信心度{pattern_confidence}%）', 'Pattern')
                else:
                    add_score('底部形態形成中', w['PATTERN_BOTTOM_FORMING'],
                             f'{pattern_name}形成中，等待突破頸線', 'Pattern')
            elif pattern_type == 'top':
                if 'CONFIRMED' in pattern_status:
                    add_score('頭部形態確立', w['PATTERN_TOP_CONFIRMED'],
                             f'{pattern_name}跌破頸線確立（信心度{pattern_confidence}%）', 'Pattern')
                else:
                    add_score('頭部形態形成中', w['PATTERN_TOP_FORMING'],
                             f'{pattern_name}形成中，留意頸線支撐', 'Pattern')
        
        # ========================================
        # 2. 波段評分 (Wave) - v4.5.20 正交化版
        # ========================================
        # 【正交化說明】
        # - 三盤突破/爆量長紅/VP05 已移至量能區段統一處理（互斥）
        # - 此區段只處理「環境」和「跌破」訊號
        wave = result.get('wave_analysis', {})
        
        # 三盤跌破（負面訊號，獨立處理）
        if wave.get('available'):
            breakdown = wave.get('breakdown_signal', {})
            if breakdown.get('detected'):
                add_score('三盤跌破', w['WAVE_BREAKDOWN'],
                         '收盤價跌破前三日低點，趨勢轉空', 'Wave')
            elif wave.get('is_bearish_env'):
                add_score('空頭環境', w['WAVE_BEARISH_ENV'],
                         '均線空頭排列，趨勢向下', 'Wave')
        
        # ========================================
        # 3. 量能評分 (Volume) - v4.5.20 因子正交化版
        # ========================================
        # 【正交化原則】
        # - 突破類訊號互斥：三盤突破、爆量長紅、VP05 只取最強一個
        # - 吸籌/良性回檔是獨立維度，可累加
        # - 放量不漲是負面訊號，獨立計算
        
        vol = result.get('volume_analysis', {})
        vp = result.get('volume_price', {})
        
        # === 突破類訊號收集 (互斥，只取最高分) ===
        breakout_signals = []  # [(score, name, reason)]
        
        if vol:
            volume_ratio = vol.get('volume_ratio', 1.0)
            price_change_pct = result.get('price_change_pct', 0)
            
            # 候選1：爆量長紅
            if volume_ratio > 1.5 and price_change_pct > 2:
                breakout_signals.append((
                    w['VOLUME_BULLISH_SURGE'],
                    '爆量長紅',
                    f'成交量達均量{volume_ratio:.1f}倍，收漲{price_change_pct:.1f}%'
                ))
            
            # 爆量長黑獨立處理（負面訊號）
            if volume_ratio > 1.5 and price_change_pct < -2:
                add_score('爆量長黑', w['VOLUME_BEARISH_SURGE'],
                         f'成交量達均量{volume_ratio:.1f}倍，收跌{abs(price_change_pct):.1f}%', 'Volume')
        
        # 高階量價訊號
        vp_has_breakout = False
        if vp.get('available'):
            signals = vp.get('signals', [])
            signal_codes = {s.get('code') for s in signals}
            
            # 候選2：VP05 帶量突破 (優先級更高，因為更精確)
            if 'VP05' in signal_codes:
                breakout_signals.append((
                    w.get('VOLUME_VP05_VALID_BREAKOUT', 12),
                    '帶量有效突破',
                    '突破20日高點且成交量放大，突破有效'
                ))
                vp_has_breakout = True
            
            # VP09: 吸籌跡象 (獨立維度，不與突破互斥)
            if 'VP09' in signal_codes:
                add_score('吸籌跡象', w.get('VOLUME_VP09_ACCUMULATION', 10),
                         '低位區量能漸增，聰明錢進場特徵', 'Volume')
            
            # VP04: 價跌量縮 (獨立維度，良性回檔)
            if 'VP04' in signal_codes:
                add_score('良性回檔', w.get('VOLUME_VP04_SELLING_EASING', 5),
                         '價跌量縮，賣壓減緩，拉回買點浮現', 'Volume')
            
            # VP07: 放量不漲 (負面訊號)
            if 'VP07' in signal_codes:
                add_score('放量不漲', w['VOLUME_NO_RISE'],
                         '高位放量但價格未漲，疑似派發', 'Volume')
        
        # === 從波段分析補充突破訊號 ===
        # 注意：如果 VP05 已觸發，三盤突破不再重複加分
        if not vp_has_breakout:
            breakout = wave.get('breakout_signal', {}) if wave.get('available') else {}
            if breakout.get('detected'):
                volume_confirmed = breakout.get('volume_confirmed', False)
                if volume_confirmed:
                    breakout_signals.append((
                        w['WAVE_BREAKOUT'],
                        '三盤突破（帶量）',
                        '收盤價突破前三日高點，且成交量放大確認'
                    ))
                else:
                    breakout_signals.append((
                        int(w['WAVE_BREAKOUT'] * 0.6),
                        '三盤突破（量縮）',
                        '收盤價突破前三日高點，但成交量萎縮'
                    ))
        
        # === 正交化：突破類只取最高分 ===
        if breakout_signals:
            # 按分數排序，取最高
            breakout_signals.sort(key=lambda x: x[0], reverse=True)
            best_score, best_name, best_reason = breakout_signals[0]
            add_score(best_name, best_score, best_reason, 'Volume')
            
            # 如果有多個突破訊號，記錄但不重複加分
            if len(breakout_signals) > 1:
                other_signals = [s[1] for s in breakout_signals[1:]]
                # 可選：印出被過濾的訊號（調試用）
                # print(f"[正交化] 過濾重複突破訊號: {other_signals}")
        
        # === 非突破類多頭/空頭環境 ===
        if wave.get('available') and not breakout_signals:
            if wave.get('is_bullish_env'):
                add_score('多頭環境', w['WAVE_BULLISH_ENV'],
                         '均線多頭排列，趨勢向上', 'Wave')
        
        if wave.get('available'):
            breakdown = wave.get('breakdown_signal', {})
            if breakdown.get('detected'):
                add_score('三盤跌破', w['WAVE_BREAKDOWN'],
                         '收盤價跌破前三日低點，趨勢轉空', 'Wave')
            elif wave.get('is_bearish_env') and not breakdown.get('detected'):
                add_score('空頭環境', w['WAVE_BEARISH_ENV'],
                         '均線空頭排列，趨勢向下', 'Wave')
        
        # ========================================
        # 4. 技術指標評分 (Tech) - v4.5.19 高盛級動態權重
        # ========================================
        tech = result.get('technical', {})
        if tech:
            k_value = tech.get('k', 50)
            d_value = tech.get('d', 50)
            rsi = tech.get('rsi', 50)
            macd_hist = tech.get('macd_histogram', 0)
            
            # === KD 動態權重 (高盛優化) ===
            if k_value > d_value:
                if k_value < 20:
                    # 低檔強勢金叉 (超賣區)
                    add_score('KD 低檔強勢金叉', w.get('TECH_KD_LOW_GOLDEN', 8),
                             f'K={k_value:.0f} < 20，超賣區金叉，反彈機率高', 'Tech')
                elif k_value < 50:
                    # 普通金叉
                    add_score('KD 黃金交叉', w['TECH_KD_GOLDEN_CROSS'],
                             f'K={k_value:.0f} > D={d_value:.0f}，多頭排列', 'Tech')
                # K > 50 的金叉不加分（肉不多）
            elif k_value < d_value and k_value > 70:
                add_score('KD 死亡交叉', w['TECH_KD_DEATH_CROSS'],
                         f'K={k_value:.0f} > 70，超買區死叉，回檔機率高', 'Tech')
            
            # === RSI 區間判斷 (高盛優化: 區間 > 交叉) ===
            trend = result.get('trend', {})
            ma20_slope = trend.get('ma20_slope', 0) if isinstance(trend, dict) else 0
            
            if 40 < rsi < 80 and ma20_slope > 0:
                # 多頭強勢區: RSI 維持在 40~80，MA20 上揚
                add_score('RSI 多頭控盤區', w.get('TECH_RSI_BULLISH_ZONE', 8),
                         f'RSI={rsi:.0f}，維持在40~80強勢區間，MA20上揚', 'Tech')
            elif 30 < rsi < 40:
                # 從超賣區回升
                add_score('RSI 回升', w['TECH_RSI_GOLDEN_CROSS'],
                         f'RSI={rsi:.0f}，從超賣區回升', 'Tech')
            
            # === MACD ===
            if macd_hist > 0:
                add_score('MACD 多頭', w['TECH_MACD_BULLISH'],
                         'MACD 柱狀體為正，多頭動能', 'Tech')
            elif macd_hist < 0:
                add_score('MACD 空頭', w['TECH_MACD_BEARISH'],
                         'MACD 柱狀體為負，空頭動能', 'Tech')
            
            # === MACD 背離偵測 (高盛新增) ===
            macd_divergence = tech.get('macd_divergence', {})
            if macd_divergence.get('bullish_divergence'):
                add_score('MACD 底背離', w.get('TECH_MACD_BULLISH_DIVERGENCE', 15),
                         '股價創新低但MACD底部墊高，強烈反轉訊號', 'Tech')
            if macd_divergence.get('bearish_divergence'):
                add_score('MACD 頂背離', w.get('TECH_MACD_BEARISH_DIVERGENCE', -15),
                         '股價創新高但MACD頂部降低，動能減弱', 'Tech')
            
            # 如果 MACD 背離不存在，嘗試從 RSI 背離推斷
            if not macd_divergence:
                mr_temp = result.get('mean_reversion', {})
                rsi_div = mr_temp.get('divergence', {}) if isinstance(mr_temp, dict) else {}
                if rsi_div.get('rsi_bullish_divergence'):
                    add_score('RSI 底背離', int(w.get('TECH_MACD_BULLISH_DIVERGENCE', 15) * 0.7),
                             'RSI 與價格背離，暗示反轉', 'Tech')
        
        # ========================================
        # 4.5 動能/相對強度評分 (Momentum) - v4.5.20 正交化版
        # ========================================
        # 【正交化說明】
        # - RS 採用階梯式評分，不重複加分
        # - 布林壓縮與突破加成是獨立維度
        
        # === 相對強度 (RS) - 核心選股因子（階梯式，互斥）===
        rs = result.get('relative_strength', {})
        if rs:
            rs_score = rs.get('rs_score', 0)
            rs_vs_market = rs.get('vs_market', 0)
            
            # 階梯式評分（只取最高等級）
            if rs_score > 80:
                # 最高等級：市場領頭羊
                add_score('市場領頭羊', w.get('MOMENTUM_RS_LEADER', 10) + 5,  # 合併為 +15
                         f'RS={rs_score:.0f}，強度在市場前20%，領漲股特徵', 'Momentum')
            elif rs_score > 50 or rs_vs_market > 0:
                # 中等級：強於大盤
                add_score('強於大盤', w.get('MOMENTUM_RS_POSITIVE', 10),
                         f'相對強度={rs_score:.0f}，逆勢抗跌，法人護盤特徵', 'Momentum')
            # rs_score <= 50 且 rs_vs_market <= 0：不加分
        
        # === 布林通道壓縮 (BB Squeeze) - 捕捉變盤前夕 ===
        regime = result.get('market_regime', {})
        if regime:
            is_squeeze = regime.get('is_squeeze', False)
            bb_width = regime.get('bb_width', 0)
            
            if is_squeeze:
                # 布林壓縮本身 +10
                squeeze_score = w.get('MOMENTUM_BB_SQUEEZE', 10)
                
                # 壓縮後突破額外加成 +5（合併顯示）
                if wave.get('breakout_signal', {}).get('detected'):
                    squeeze_score += w.get('MOMENTUM_BB_SQUEEZE_BREAKOUT', 5)
                    add_score('布林壓縮突破', squeeze_score,
                             f'BB寬度={bb_width:.2f}，壓縮後突破，大行情啟動', 'Momentum')
                else:
                    add_score('布林壓縮', squeeze_score,
                             f'BB寬度={bb_width:.2f}，波動率收窄，蓄勢待發', 'Momentum')
        
        # ========================================
        # 5. 風險評分 (Risk)
        # ========================================
        mr = result.get('mean_reversion', {})
        if mr.get('available'):
            bias_20 = mr.get('bias_analysis', {}).get('bias_20', 0)
            if bias_20 > 8:
                add_score('乖離率過熱', w['RISK_BIAS_OVERHEATED'],
                         f'乖離率 {bias_20:+.1f}% 超過 +8%，短線超漲', 'Risk')
            elif bias_20 < -8:
                # 高盛新增：超跌反彈機會
                add_score('乖離率超跌', w.get('RISK_BIAS_OVERSOLD', 5),
                         f'乖離率 {bias_20:+.1f}% 低於 -8%，超跌反彈機會', 'Risk')
        
        rsi = tech.get('rsi', 50) if tech else 50
        if rsi > 80:
            add_score('RSI 超買', w['RISK_RSI_OVERBOUGHT'],
                     f'RSI={rsi:.0f} > 80，技術面過熱', 'Risk')
        
        if vol:
            volume_ratio = vol.get('volume_ratio', 1.0)
            if volume_ratio < 0.6:
                add_score('量縮風險', w['RISK_VOLUME_SHRINK'],
                         f'成交量萎縮至均量{volume_ratio*100:.0f}%', 'Risk')
        
        # RR 檢查
        sr = result.get('support_resistance', {})
        current_price = result.get('current_price', 0)
        if current_price > 0:
            take_profit = sr.get('take_profit', current_price * 1.1)
            stop_loss = sr.get('stop_loss', current_price * 0.95)
            
            if isinstance(take_profit, str):
                take_profit = current_price * 1.1
            if isinstance(stop_loss, str):
                stop_loss = current_price * 0.95
            
            potential_gain = (take_profit - current_price) / current_price if current_price > 0 else 0
            potential_loss = (current_price - stop_loss) / current_price if current_price > 0 else 0
            
            if potential_loss > 0:
                rr_ratio = potential_gain / potential_loss
                if rr_ratio < 1.5:
                    add_score('風險回報比不佳', w['RISK_LOW_RR'],
                             f'RR={rr_ratio:.2f} < 1.5，上檔空間有限', 'Risk')
        
        # ========================================
        # 【關鍵修正】最後一步才進行 0-100 截斷
        # ========================================
        # raw_score 可能 > 100 或 < 0，這裡才進行最終截斷
        final_score = max(0, min(100, raw_score))
        
        # 計算標籤和建議
        label, short_action, _ = DecisionMatrix._get_score_label(final_score)
        
        # 計算信心度
        positive_count = sum(1 for c in components if c['is_positive'])
        negative_count = sum(1 for c in components if not c['is_positive'])
        total_count = len(components)
        
        if total_count > 0:
            consistency = abs(positive_count - negative_count) / total_count
            deviation = abs(final_score - 50) / 50
            
            if consistency > 0.7 and deviation > 0.4:
                confidence = 'High'
            elif consistency > 0.4 or deviation > 0.2:
                confidence = 'Medium'
            else:
                confidence = 'Low'
        else:
            confidence = 'Low'
        
        # 計算各類別分數
        breakdown = {}
        for comp in components:
            cat = comp['category']
            if cat not in breakdown:
                breakdown[cat] = 0
            breakdown[cat] += comp['score']
        
        # 計算總加減分（用於顯示）
        total_adjustment = sum(c['score'] for c in components)
        
        return {
            'score': final_score,
            'raw_score': raw_score,           # 新增：未截斷的原始分數
            'base_score': base_score,
            'total_adjustment': total_adjustment,  # 新增：總加減分
            'label': label,
            'action': short_action,
            'confidence': confidence,
            'components': components,
            'breakdown': breakdown
        }
    
    @staticmethod
    def calculate_long_term_score(result):
        """
        計算中長線投資評分
        
        =====================================================
        評分邏輯（加分制，基準 50 分）：
        =====================================================
        
        【重要】數學邏輯修正 v4.4.9：
        - 先累計所有加減分到 raw_score
        - 最後一步才進行 0-100 的截斷 (Clamp)
        - 避免「分數失真」問題
        
        1. 年線趨勢 - 最重要
           - 站上 MA240: +20
           - MA240 上揚: +15
           - 跌破 MA240: -20
        
        2. 價值面 - 估值判斷
           - PE < 15: +20
           - PE 位於歷史低檔: +10
           - PE > 30: -15
        
        3. 籌碼面 - 大戶動向
           - 法人連買: +10
           - 法人連賣: -10
        
        Args:
            result: QuickAnalyzer.analyze_stock() 的回傳結果
        
        Returns:
            dict: 中長線投資評分結果
        """
        # v2.0: 直接呼叫三層引擎 Layer 1（方向分）
        # 方向分代表「長期趨勢」，是中長線投資最重要的單一因子
        direction = ThreeLayerEngine.score_direction(result)
        score     = direction['score']
        label     = direction['label']
        confidence = 'High' if score >= 65 else 'Medium' if score >= 45 else 'Low'
        bull_count = direction['bull_count']
        details    = direction['details']   # e.g. ['均線偏空排列（1/4）', '趨勢強 ADX=56']

        # 計算兩個子因子的真實貢獻分，供 GUI 明細正確顯示
        # 均線排列基礎分（相對中性基準 50）
        alignment_raw = int(15 + bull_count * 16.25)   # 0/4→15, 1/4→31, 2/4→47, 3/4→63, 4/4→80
        alignment_adj = alignment_raw - 50              # 相對 50 的貢獻

        # ADX 修正（最大 ±10）— 剩餘差值
        adx_adj = score - alignment_raw                 # 等於原始 adx_mod

        components = []
        if details:
            components.append({
                'name':        details[0],        # 均線排列描述
                'score':       alignment_adj,
                'reason':      details[0],
                'category':    'Direction',
                'is_positive': alignment_adj >= 0,
            })
        if len(details) > 1:
            components.append({
                'name':        details[1],        # ADX 描述
                'score':       adx_adj,
                'reason':      details[1],
                'category':    'Direction',
                'is_positive': adx_adj >= 0,
            })

        total_adj = alignment_adj + adx_adj       # 應等於 score - 50

        return {
            'score':            score,
            'label':            label,
            'action':           '持續關注' if score >= 55 else '暫時觀望',
            'confidence':       confidence,
            'components':       components,
            'breakdown':        {'Direction': total_adj},
            'raw_score':        score,
            'base_score':       50,
            'total_adjustment': total_adj,
            'bull_count':       bull_count,
            'bear_count':       direction['bear_count'],
        }

        # =====================================================
        # [舊版加分制邏輯已移除，下方為廢棄程式碼，保留供比對]
        # =====================================================
        _DEAD_CODE_START = True  # 此行之後的程式碼不會被執行
        w = DecisionMatrix.LONG_TERM_WEIGHTS  # noqa
        base_score = 50
        raw_score = base_score  # 使用 raw_score 累計，不提前截斷
        components = []
        
        def add_score(name, score, reason, category):
            """
            累計加減分（不截斷）
            """
            nonlocal raw_score
            components.append({
                'name': name,
                'score': score,
                'reason': reason,
                'category': category,
                'is_positive': score > 0
            })
            raw_score += score  # 只累加，不截斷
        
        tech = result.get('technical', {})
        current_price = result.get('current_price', 0)
        
        # ========================================
        # 1. 年線趨勢評分
        # ========================================
        ma240 = tech.get('ma240', 0)
        ma120 = tech.get('ma120', 0)
        ma60 = tech.get('ma60', 0)
        ma20 = tech.get('ma20', 0)
        
        if current_price > 0:
            # 站上/跌破年線
            if ma240 > 0:
                if current_price > ma240:
                    add_score('站上年線', w['ABOVE_MA240'],
                             f'現價${current_price:.2f} > MA240=${ma240:.2f}', 'YearlyTrend')
                else:
                    add_score('跌破年線', w['BELOW_MA240'],
                             f'現價${current_price:.2f} < MA240=${ma240:.2f}', 'YearlyTrend')
            
            # 站上/跌破半年線
            if ma120 > 0:
                if current_price > ma120:
                    add_score('站上半年線', w['ABOVE_MA120'],
                             f'現價 > MA120=${ma120:.2f}', 'YearlyTrend')
                else:
                    add_score('跌破半年線', w['BELOW_MA120'],
                             f'現價 < MA120=${ma120:.2f}', 'YearlyTrend')
            
            # 均線多頭/空頭排列
            if ma20 > 0 and ma60 > 0 and ma240 > 0:
                if ma20 > ma60 > ma240:
                    add_score('均線多頭排列', w['MA_BULLISH_ALIGN'],
                             'MA20 > MA60 > MA240，長期趨勢向上', 'YearlyTrend')
                elif ma20 < ma60 < ma240:
                    add_score('均線空頭排列', w['MA_BEARISH_ALIGN'],
                             'MA20 < MA60 < MA240，長期趨勢向下', 'YearlyTrend')
        
        # ========================================
        # 2. 價值面評分
        # ========================================
        fundamental = result.get('fundamental', {})
        
        # v4.5.12 修正：確保數值類型正確（可能是字串）
        def safe_float(value, default=None):
            """安全轉換為浮點數"""
            if value is None:
                return default
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, str):
                # 移除百分號和其他符號
                cleaned = value.replace('%', '').replace(',', '').strip()
                try:
                    return float(cleaned)
                except ValueError:
                    return default
            return default
        
        pe_ratio = safe_float(fundamental.get('pe_ratio'))
        pb_ratio = safe_float(fundamental.get('pb_ratio'))
        dividend_yield = safe_float(fundamental.get('dividend_yield'))
        
        if pe_ratio is not None and pe_ratio > 0:
            if pe_ratio < 15:
                add_score('低本益比', w['PE_LOW'],
                         f'PE={pe_ratio:.1f} < 15，估值偏低', 'Valuation')
            elif pe_ratio > 30:
                add_score('高本益比', w['PE_HIGH'],
                         f'PE={pe_ratio:.1f} > 30，估值偏高', 'Valuation')
        
        if pb_ratio is not None and pb_ratio > 0:
            if pb_ratio < 1.5:
                add_score('低股價淨值比', w['PB_LOW'],
                         f'PB={pb_ratio:.2f} < 1.5，可能被低估', 'Valuation')
            elif pb_ratio > 5:
                add_score('高股價淨值比', w['PB_HIGH'],
                         f'PB={pb_ratio:.2f} > 5，估值偏高', 'Valuation')
        
        if dividend_yield is not None and dividend_yield > 4:
            add_score('高殖利率', w['DIVIDEND_HIGH'],
                     f'殖利率={dividend_yield:.1f}% > 4%，配息穩定', 'Valuation')
        
        # ========================================
        # 3. 籌碼面評分
        # ========================================
        chip = result.get('chip_analysis', {})
        if chip.get('available'):
            foreign_net = chip.get('foreign_net', 0)
            consecutive_buy_days = chip.get('consecutive_buy_days', 0)
            consecutive_sell_days = chip.get('consecutive_sell_days', 0)
            
            if consecutive_buy_days >= 3:
                add_score('法人連買', w['INSTITUTIONAL_BUY_STREAK'],
                         f'法人連續{consecutive_buy_days}天淨買超', 'Chip')
            elif consecutive_sell_days >= 3:
                add_score('法人連賣', w['INSTITUTIONAL_SELL_STREAK'],
                         f'法人連續{consecutive_sell_days}天淨賣超', 'Chip')
            
            if foreign_net > 0:
                add_score('外資淨買', w['FOREIGN_NET_BUY'],
                         f'外資今日淨買超{foreign_net:,}張', 'Chip')
            elif foreign_net < 0:
                add_score('外資淨賣', w['FOREIGN_NET_SELL'],
                         f'外資今日淨賣超{abs(foreign_net):,}張', 'Chip')
        
        # ========================================
        # 【關鍵修正】最後一步才進行 0-100 截斷
        # ========================================
        final_score = max(0, min(100, raw_score))
        
        # 計算標籤和建議
        label, _, long_action = DecisionMatrix._get_score_label(final_score)
        
        # 計算信心度
        positive_count = sum(1 for c in components if c['is_positive'])
        negative_count = sum(1 for c in components if not c['is_positive'])
        total_count = len(components)
        
        if total_count > 0:
            consistency = abs(positive_count - negative_count) / total_count
            deviation = abs(final_score - 50) / 50
            
            if consistency > 0.7 and deviation > 0.4:
                confidence = 'High'
            elif consistency > 0.4 or deviation > 0.2:
                confidence = 'Medium'
            else:
                confidence = 'Low'
        else:
            confidence = 'Low'
        
        # 計算各類別分數
        breakdown = {}
        for comp in components:
            cat = comp['category']
            if cat not in breakdown:
                breakdown[cat] = 0
            breakdown[cat] += comp['score']
        
        # 計算總加減分（用於顯示）
        total_adjustment = sum(c['score'] for c in components)
        
        return {
            'score': final_score,
            'raw_score': raw_score,           # 新增：未截斷的原始分數
            'base_score': base_score,
            'total_adjustment': total_adjustment,  # 新增：總加減分
            'label': label,
            'action': long_action,
            'confidence': confidence,
            'components': components,
            'breakdown': breakdown
        }
    
    @staticmethod
    def _get_score_label(final_score):
        """根據分數取得標籤和建議"""
        for (low, high), (label, short_action, long_action) in DecisionMatrix.SCORE_LABELS.items():
            if low <= final_score <= high:
                return label, short_action, long_action
        return '中性', '觀望', '觀望'
    
    @staticmethod
    def get_comprehensive_report(result):
        """
        取得綜合雙軌評分報告
        
        =====================================================
        報告結構：
        =====================================================
        1. 短線波段評分：專注於形態、均線突破、量能爆發
        2. 中長線投資評分：專注於年線趨勢、基本面、籌碼面
        3. 綜合建議：根據短線和長線評分的組合給出操作建議
        
        組合矩陣：
        ┌───────────┬─────────────┬─────────────┬─────────────┐
        │           │ 長線偏多    │ 長線中性    │ 長線偏空    │
        ├───────────┼─────────────┼─────────────┼─────────────┤
        │短線偏多   │ 積極買進    │ 短線操作    │ 搶反彈      │
        │短線中性   │ 長線布局    │ 觀望        │ 減碼        │
        │短線偏空   │ 逢低布局    │ 減碼觀望    │ 清倉        │
        └───────────┴─────────────┴─────────────┴─────────────┘
        
        Args:
            result: QuickAnalyzer.analyze_stock() 的回傳結果
        
        Returns:
            dict: 完整的雙軌評分報告
        """
        short_term = DecisionMatrix.calculate_short_term_score(result)
        long_term = DecisionMatrix.calculate_long_term_score(result)
        
        # 產生綜合建議
        ss = short_term['score']
        ls = long_term['score']
        
        # 判斷方向
        short_bullish = ss >= 60
        short_neutral = 40 <= ss < 60
        short_bearish = ss < 40
        
        long_bullish = ls >= 60
        long_neutral = 40 <= ls < 60
        long_bearish = ls < 40
        
        # 判斷是否存在矛盾
        conflict = (short_bullish and long_bearish) or (short_bearish and long_bullish)
        
        # 組合判斷
        if short_bullish and long_bullish:
            combined = {
                'advice': '積極買進',
                'explanation': '短線與長線訊號一致偏多，可積極進場。',
                'short_action': short_term['action'],
                'long_action': long_term['action'],
                'conflict': False,
                'risk_level': 'Low'
            }
        elif short_bullish and long_neutral:
            combined = {
                'advice': '短線操作',
                'explanation': '短線有買訊但長線趨勢不明，建議短線進出，快進快出。',
                'short_action': short_term['action'],
                'long_action': '觀望',
                'conflict': False,
                'risk_level': 'Medium'
            }
        elif short_bullish and long_bearish:
            combined = {
                'advice': '搶反彈（輕倉）',
                'explanation': '⚠️ 短線有買訊但長線趨勢偏空，僅適合搶反彈，嚴設停損。',
                'short_action': '搶短（輕倉）',
                'long_action': '不建議持有',
                'conflict': True,
                'risk_level': 'High'
            }
        elif short_neutral and long_bullish:
            combined = {
                'advice': '長線布局',
                'explanation': '短線無明確訊號但長線趨勢向上，適合分批布局。',
                'short_action': '觀望',
                'long_action': long_term['action'],
                'conflict': False,
                'risk_level': 'Low'
            }
        elif short_neutral and long_neutral:
            combined = {
                'advice': '觀望',
                'explanation': '短線和長線皆無明確方向，建議觀望等待。',
                'short_action': '觀望',
                'long_action': '觀望',
                'conflict': False,
                'risk_level': 'Medium'
            }
        elif short_neutral and long_bearish:
            combined = {
                'advice': '減碼',
                'explanation': '長線趨勢偏空，建議減碼降低風險。',
                'short_action': '觀望',
                'long_action': '減碼',
                'conflict': False,
                'risk_level': 'High'
            }
        elif short_bearish and long_bullish:
            combined = {
                'advice': '逢低布局',
                'explanation': '短線超跌但長線趨勢向上，可考慮逢低分批布局。',
                'short_action': '勿追空',
                'long_action': '逢低布局',
                'conflict': True,
                'risk_level': 'Medium'
            }
        elif short_bearish and long_neutral:
            combined = {
                'advice': '減碼觀望',
                'explanation': '短線偏空且長線趨勢不明，建議減碼觀望。',
                'short_action': short_term['action'],
                'long_action': '觀望',
                'conflict': False,
                'risk_level': 'High'
            }
        else:  # short_bearish and long_bearish
            combined = {
                'advice': '清倉',
                'explanation': '短線與長線訊號一致偏空，建議清倉避險。',
                'short_action': short_term['action'],
                'long_action': long_term['action'],
                'conflict': False,
                'risk_level': 'Very High'
            }
        
        return {
            'available': True,
            'symbol': result.get('symbol', ''),
            'current_price': result.get('current_price', 0),
            
            # 短線評分
            'short_term': short_term,
            
            # 長線評分
            'long_term': long_term,
            
            # 綜合建議
            'combined': combined
        }
    
    @staticmethod
    def generate_dual_score_report_text(result):
        """產生雙軌評分文字報告"""
        report = DecisionMatrix.get_comprehensive_report(result)
        
        if not report.get('available'):
            return "雙軌評分不可用"
        
        lines = []
        lines.append("=" * 60)
        lines.append("【📊 雙軌評分系統報告 v1.0】")
        lines.append("=" * 60)
        
        # 基本資訊
        lines.append(f"\n股票代碼：{report['symbol']}")
        lines.append(f"現價：${report['current_price']:.2f}")
        
        # 短線評分
        st = report['short_term']
        lines.append(f"\n{'─' * 30}")
        lines.append(f"📈 短線波段評分：{st['score']} 分（{st['label']}）")
        lines.append(f"   操作建議：{st['action']}")
        lines.append(f"   信心度：{st['confidence']}")
        lines.append(f"   評分明細：")
        for comp in st['components']:
            sign = '+' if comp['score'] > 0 else ''
            lines.append(f"     • [{comp['category']}] {comp['name']}: {sign}{comp['score']} - {comp['reason']}")
        
        # 長線評分
        lt = report['long_term']
        lines.append(f"\n{'─' * 30}")
        lines.append(f"📉 中長線投資評分：{lt['score']} 分（{lt['label']}）")
        lines.append(f"   操作建議：{lt['action']}")
        lines.append(f"   信心度：{lt['confidence']}")
        lines.append(f"   評分明細：")
        for comp in lt['components']:
            sign = '+' if comp['score'] > 0 else ''
            lines.append(f"     • [{comp['category']}] {comp['name']}: {sign}{comp['score']} - {comp['reason']}")
        
        # 綜合建議
        cb = report['combined']
        lines.append(f"\n{'─' * 30}")
        lines.append(f"🎯 綜合建議：{cb['advice']}")
        lines.append(f"   {cb['explanation']}")
        if cb['conflict']:
            lines.append(f"   ⚠️ 注意：短線與長線訊號存在矛盾")
        lines.append(f"   風險等級：{cb['risk_level']}")
        
        # v4.4.9 新增：顯示原始分數（如果有截斷）
        if st.get('raw_score') and st['raw_score'] != st['score']:
            lines.append(f"\n📐 分數計算說明：")
            lines.append(f"   短線原始分數：{st['raw_score']} → 截斷後：{st['score']}")
        if lt.get('raw_score') and lt['raw_score'] != lt['score']:
            if not (st.get('raw_score') and st['raw_score'] != st['score']):
                lines.append(f"\n📐 分數計算說明：")
            lines.append(f"   長線原始分數：{lt['raw_score']} → 截斷後：{lt['score']}")
        
        lines.append("=" * 60)
        
        return "\n".join(lines)
    
    # ========================================================================
    # v4.4.8 新增：九大投資場景矩陣 (Investment Scenario Matrix)
    # ========================================================================
    
    # 評分區間定義（v4.5.10 調整：縮窄中性區間，強迫系統表態）
    # 原本：High >= 70, Mid 40-70, Low <= 40（中性區間太寬）
    # 調整：High >= 65, Mid 45-65, Low <= 45（更敏感的訊號）
    SCORE_ZONE_HIGH = 65      # 高分區：分數 >= 65
    SCORE_ZONE_MID_LOW = 45   # 低分區：分數 <= 45
    # 中分區：45 < 分數 < 65
    
    # 九大投資場景定義
    INVESTMENT_SCENARIOS = {
        # ================================================================
        # 情境 A：【雙強共振 - 強力進攻型】 (短線高 / 長線高)
        # ================================================================
        'A': {
            'action': 'Strong Buy',
            'action_zh': '強力買進',
            'title': '【雙強共振 - 強力進攻型】',
            'short_zone': 'High',
            'long_zone': 'High',
            'description': (
                '趨勢完美共振！長線基本面與籌碼面位於優勢區間，且短線技術面出現強勢'
                '突破訊號（如爆量長紅或形態確立）。這是勝率最高的「右側交易」機會，'
                '建議積極建倉，並可適度放大部位，沿 5 日線操作。'
            ),
            'risk_level': 'Low',
            'position_advice': '可放大部位至 80-100%',
            'stop_loss_advice': '跌破 5 日線減碼，跌破 10 日線停損',
            'emoji': '🚀'
        },
        
        # ================================================================
        # 情境 B：【拉回佈局 - 價值投資型】 (短線低 / 長線高)
        # ================================================================
        'B': {
            'action': 'Buy on Dip',
            'action_zh': '逢低佈局',
            'title': '【拉回佈局 - 價值投資型】',
            'short_zone': 'Low',
            'long_zone': 'High',
            'description': (
                '長線保護短線。雖然短線技術指標轉弱或出現回檔（如 KD 死叉、跌破短均），'
                '但長線趨勢與價值面仍完好。這屬於良性的「乖離修正」，是絕佳的「左側交易」'
                '買點。建議採分批向下承接策略，不追高，掛單等待。'
            ),
            'risk_level': 'Medium',
            'position_advice': '分批建倉，每跌 3-5% 加碼一次',
            'stop_loss_advice': '跌破年線或基本面惡化時停損',
            'emoji': '💰'
        },
        
        # ================================================================
        # 情境 C：【投機反彈 - 短線價差型】 (短線高 / 長線低)
        # ================================================================
        'C': {
            'action': 'Speculative Buy',
            'action_zh': '搶短反彈',
            'title': '【投機反彈 - 短線價差型】',
            'short_zone': 'High',
            'long_zone': 'Low',
            'description': (
                '逆勢搶反彈。長線趨勢偏空（如空頭排列或營收衰退），但短線出現乖離過大'
                '或底部形態突破。此為「跌深反彈」行情，上方壓力重重。建議「輕倉操作」，'
                '嚴格設定停損，有賺就跑，切勿談戀愛。'
            ),
            'risk_level': 'High',
            'position_advice': '輕倉操作，不超過總資金 30%',
            'stop_loss_advice': '反彈目標達成或跌破底部形態即出場',
            'emoji': '⚡'
        },
        
        # ================================================================
        # 情境 D：【高檔震盪 - 獲利守成型】 (短線中 / 長線高)
        # ================================================================
        'D': {
            'action': 'Hold',
            'action_zh': '持股續抱',
            'title': '【高檔震盪 - 獲利守成型】',
            'short_zone': 'Mid',
            'long_zone': 'High',
            'description': (
                '多頭中繼站。長線多頭架構不變，但短線動能減弱，進入高檔橫盤整理。'
                '建議「持股續抱」，並設定移動停利點（如跌破 20 日線），等待下一波攻擊'
                '發起，暫時不急於加碼。'
            ),
            'risk_level': 'Low',
            'position_advice': '維持現有部位，不加碼',
            'stop_loss_advice': '跌破 20 日線減碼 50%，跌破 60 日線清倉',
            'emoji': '🛡️'
        },
        
        # ================================================================
        # 情境 E：【多空不明 - 雞肋觀望型】 (短線中 / 長線中)
        # ================================================================
        'E': {
            'action': 'Neutral',
            'action_zh': '觀望',
            'title': '【多空不明 - 雞肋觀望型】',
            'short_zone': 'Mid',
            'long_zone': 'Mid',
            'description': (
                '方向迷失中。長線缺乏亮點，短線也無明確形態或量能。股價陷入膠著的'
                '「垃圾時間」。資金效率極低，建議「空手觀望」，將資金轉移至其他更有'
                '效率的標的。'
            ),
            'risk_level': 'Medium',
            'position_advice': '空手觀望，尋找更好機會',
            'stop_loss_advice': '不建議進場',
            'emoji': '🤷'
        },
        
        # ================================================================
        # 情境 F：【弱勢盤整 - 陰跌抵抗型】 (短線中 / 長線低)
        # ================================================================
        'F': {
            'action': 'Reduce',
            'action_zh': '減碼觀望',
            'title': '【弱勢盤整 - 陰跌抵抗型】',
            'short_zone': 'Mid',
            'long_zone': 'Low',
            'description': (
                '溫水煮青蛙。長線結構已遭破壞，短線雖有抵抗但無力反攻（量縮整理）。'
                '這往往是「盤跌」的前兆。建議利用盤中反彈機會「調節持股」，降低曝險。'
            ),
            'risk_level': 'High',
            'position_advice': '逢反彈減碼 50% 以上',
            'stop_loss_advice': '跌破近期低點即清倉',
            'emoji': '⚠️'
        },
        
        # ================================================================
        # 情境 G：【頭部確立 - 獲利了結型】 (短線低 / 長線中)
        # ================================================================
        'G': {
            'action': 'Sell',
            'action_zh': '賣出',
            'title': '【頭部確立 - 獲利了結型】',
            'short_zone': 'Low',
            'long_zone': 'Mid',
            'description': (
                '派對結束。雖然長線尚未完全翻空，但短線已出現明顯的「頭部形態」'
                '（如 M 頭、爆量長黑）。主力正在出貨，建議「獲利了結」，不要期待'
                '股價會馬上創新高。'
            ),
            'risk_level': 'High',
            'position_advice': '獲利了結，保留 20% 觀察部位',
            'stop_loss_advice': '若反彈無力創高，清空剩餘部位',
            'emoji': '🚪'
        },
        
        # ================================================================
        # 情境 H：【空頭確認 - 逃命避險型】 (短線低 / 長線低)
        # ================================================================
        'H': {
            'action': 'Strong Sell',
            'action_zh': '強力賣出',
            'title': '【空頭確認 - 逃命避險型】',
            'short_zone': 'Low',
            'long_zone': 'Low',
            'description': (
                '雪崩式下跌。長短線同步翻空，支撐全面瓦解，且伴隨基本面惡化。'
                '這是最危險的「主跌段」。多單應「無條件砍倉」，積極者可順勢「建立空單」。'
            ),
            'risk_level': 'Very High',
            'position_advice': '無條件清倉，積極者可放空',
            'stop_loss_advice': '不持有多單',
            'emoji': '🔴'
        },
        
        # ================================================================
        # 情境 I：【動能交易 - 轉強觀察型】 (短線高 / 長線中)
        # v4.5.10 更新：原名「蓄勢待發」改為「動能交易」
        # ================================================================
        'I': {
            'action': 'Momentum Buy',
            'action_zh': '動能買進',
            'title': '【動能交易 - 股價先行型】',
            'short_zone': 'High',
            'long_zone': 'Mid',
            'description': (
                '動能爆發！基本面尚未完全轉佳，但短線突然爆量轉強。通常是「轉機股」'
                '或「消息面驅動」，股價先行於基本面。屬於「右側交易」機會，'
                '建議順勢跟進，但須嚴格執行停損。'
            ),
            'risk_level': 'Medium',
            'position_advice': '中等部位 40-60%，嚴格止損',
            'stop_loss_advice': '跌破突破點即停損，不留情面',
            'emoji': '⚡'
        }
    }
    
    @staticmethod
    def _get_score_zone(score):
        """
        判斷分數所屬區間
        
        評分區間定義（v4.5.10 調整）：
        - 高分區 (High): 分數 >= 65（偏多）
        - 中分區 (Mid): 45 < 分數 < 65（中性）
        - 低分區 (Low): 分數 <= 45（偏空）
        
        縮窄中性區間的原因：
        1. 原本 40-70 範圍太廣，大部分股票都落在 "E: 多空不明"
        2. 調整後訊號更靈敏，更容易觸發買賣建議
        
        Args:
            score: 評分（0-100）
        
        Returns:
            str: 'High', 'Mid', 或 'Low'
        """
        if score >= DecisionMatrix.SCORE_ZONE_HIGH:
            return 'High'
        elif score <= DecisionMatrix.SCORE_ZONE_MID_LOW:
            return 'Low'
        else:
            return 'Mid'
    
    @staticmethod
    def _determine_scenario_code(short_zone, long_zone):
        """
        根據短線和長線評分區間，決定投資場景代碼
        
        九大場景矩陣：
        ┌───────────┬─────────────┬─────────────┬─────────────┐
        │ 短線＼長線 │ 長線高(H)   │ 長線中(M)   │ 長線低(L)   │
        ├───────────┼─────────────┼─────────────┼─────────────┤
        │ 短線高(H) │ A: 雙強共振  │ I: 蓄勢待發  │ C: 投機反彈  │
        ├───────────┼─────────────┼─────────────┼─────────────┤
        │ 短線中(M) │ D: 高檔震盪  │ E: 多空不明  │ F: 弱勢盤整  │
        ├───────────┼─────────────┼─────────────┼─────────────┤
        │ 短線低(L) │ B: 拉回佈局  │ G: 頭部確立  │ H: 空頭確認  │
        └───────────┴─────────────┴─────────────┴─────────────┘
        
        Args:
            short_zone: 短線評分區間 ('High', 'Mid', 'Low')
            long_zone: 長線評分區間 ('High', 'Mid', 'Low')
        
        Returns:
            str: 場景代碼 ('A' ~ 'I')
        """
        # 九大場景對照表
        scenario_matrix = {
            ('High', 'High'): 'A',   # 雙強共振 - 強力進攻型
            ('Low', 'High'): 'B',    # 拉回佈局 - 價值投資型
            ('High', 'Low'): 'C',    # 投機反彈 - 短線價差型
            ('Mid', 'High'): 'D',    # 高檔震盪 - 獲利守成型
            ('Mid', 'Mid'): 'E',     # 多空不明 - 雞肋觀望型
            ('Mid', 'Low'): 'F',     # 弱勢盤整 - 陰跌抵抗型
            ('Low', 'Mid'): 'G',     # 頭部確立 - 獲利了結型
            ('Low', 'Low'): 'H',     # 空頭確認 - 逃命避險型
            ('High', 'Mid'): 'I',    # 蓄勢待發 - 轉強觀察型
        }
        
        return scenario_matrix.get((short_zone, long_zone), 'E')  # 預設為觀望
    
    @staticmethod
    def get_investment_advice(short_score, long_score):
        """
        根據短線和長線評分，產生投資建議
        
        =====================================================
        九大投資場景決策邏輯：
        =====================================================
        
        此方法是雙軌評分系統的核心輸出，根據短線（技術面、形態、量能）
        與長線（趨勢、基本面、籌碼）的分數交叉比對，產生 9 種標準化的
        投資建議場景。
        
        每個場景包含：
        1. 建議動作（英文/中文）
        2. 場景標題與描述
        3. 風險等級
        4. 部位建議
        5. 停損建議
        
        加權總分計算：短線 40% + 長線 60%
        （長線權重較高，因為趨勢是投資的根本）
        
        Args:
            short_score: 短線波段評分（0-100）
            long_score: 中長線投資評分（0-100）
        
        Returns:
            dict: 結構化的投資建議
            {
                'action': 'Strong Buy',           # 英文動作
                'action_zh': '強力買進',          # 中文動作
                'scenario_code': 'A',             # 場景代碼
                'title': '【雙強共振 - 強力進攻型】',  # 場景標題
                'description': '...',             # 詳細說明
                'short_score': 85,                # 短線分數
                'long_score': 75,                 # 長線分數
                'weighted_score': 79,             # 加權總分
                'short_zone': 'High',             # 短線區間
                'long_zone': 'High',              # 長線區間
                'risk_level': 'Low',              # 風險等級
                'position_advice': '...',         # 部位建議
                'stop_loss_advice': '...',        # 停損建議
                'emoji': '🚀'                     # 情境表情符號
            }
        """
        # v2.0: 根據三層引擎的分級輸出，而非九宮格查表
        # short_score 對應時機分（A=85 / B=65 / C=45 / X=35）
        # long_score  對應方向分（0-100 均線排列）

        weighted_score = int(short_score * 0.35 + long_score * 0.65)

        if short_score >= 80 and long_score >= 60:
            action, action_zh = 'Strong Buy', 'A 級主攻'
            scenario_code = 'A'
            title = '【方向+位置+時機三者到位】'
            risk_level = 'Low'
            position_advice = '積極進場，部位 50-80%'
            stop_loss_advice = '停損設前低，RR ≥ 2'
        elif short_score >= 60 and long_score >= 50:
            action, action_zh = 'Buy', 'B 級追蹤'
            scenario_code = 'B'
            title = '【方向對，等待時機確認】'
            risk_level = 'Medium'
            position_advice = '輕倉試單，等確認再加碼'
            stop_loss_advice = '停損設近期支撐，RR ≥ 1.5'
        elif long_score >= 60 and short_score < 60:
            action, action_zh = 'Wait', 'C 級觀察'
            scenario_code = 'C'
            title = '【方向對，時機未到】'
            risk_level = 'Medium'
            position_advice = '列入追蹤，等待觸發訊號'
            stop_loss_advice = '尚未進場，持續觀察'
        elif long_score < 45:
            action, action_zh = 'Skip', '方向不對，跳過'
            scenario_code = 'H'
            title = '【趨勢不佳，不宜進場】'
            risk_level = 'High'
            position_advice = '不建議進場'
            stop_loss_advice = '不適用'
        else:
            action, action_zh = 'Hold', '觀望'
            scenario_code = 'E'
            title = '【混合訊號，等待明確方向】'
            risk_level = 'Medium'
            position_advice = '觀望為主'
            stop_loss_advice = '不適用'

        return {
            'action':           action,
            'action_zh':        action_zh,
            'scenario_code':    scenario_code,
            'title':            title,
            'description':      f'時機分 {short_score}，方向分 {long_score}，加權 {weighted_score}',
            'short_score':      short_score,
            'long_score':       long_score,
            'weighted_score':   weighted_score,
            'short_zone':       'High' if short_score >= 70 else 'Mid' if short_score >= 50 else 'Low',
            'long_zone':        'High' if long_score  >= 65 else 'Mid' if long_score  >= 45 else 'Low',
            'risk_level':       risk_level,
            'position_advice':  position_advice,
            'stop_loss_advice': stop_loss_advice,
            'emoji':            '' ,
        }
    
    @staticmethod
    def calculate_weighted_score(short_score, long_score, 
                                  short_weight=0.4, long_weight=0.6):
        """
        計算加權總分
        
        預設權重：短線 40% + 長線 60%
        長線權重較高的原因：
        1. 趨勢是投資的根本，順勢操作勝率較高
        2. 長線因子（基本面、籌碼）具有較高的預測效度
        3. 短線波動容易產生假訊號
        
        Args:
            short_score: 短線評分（0-100）
            long_score: 長線評分（0-100）
            short_weight: 短線權重（預設 0.4）
            long_weight: 長線權重（預設 0.6）
        
        Returns:
            int: 加權總分（0-100）
        """
        return int(short_score * short_weight + long_score * long_weight)
    
    @staticmethod
    def get_full_investment_report(result):
        """
        產生完整的投資建議報告
        
        整合雙軌評分系統與九大投資場景，產生結構化的完整報告。
        
        Args:
            result: QuickAnalyzer.analyze_stock() 的回傳結果
        
        Returns:
            dict: 完整的投資建議報告
        """
        # 1. 計算短線和長線評分
        short_term = DecisionMatrix.calculate_short_term_score(result)
        long_term = DecisionMatrix.calculate_long_term_score(result)
        
        short_score = short_term['score']
        long_score = long_term['score']
        
        # 2. 產生投資建議
        advice = DecisionMatrix.get_investment_advice(short_score, long_score)
        
        # 3. 構建完整報告
        return {
            'available': True,
            'symbol': result.get('symbol', ''),
            'name': result.get('name', ''),
            'current_price': result.get('current_price', 0),
            'price_change_pct': result.get('price_change_pct', 0),
            
            # 雙軌評分
            'scoring': {
                'short_term': short_term,
                'long_term': long_term,
                'weighted_score': advice['weighted_score']
            },
            
            # 投資建議
            'investment_advice': advice,
            
            # 場景資訊
            'scenario': {
                'code': advice['scenario_code'],
                'title': advice['title'],
                'action': advice['action'],
                'action_zh': advice['action_zh'],
                'description': advice['description'],
                'risk_level': advice['risk_level']
            },
            
            # 操作建議
            'trading_advice': {
                'position': advice['position_advice'],
                'stop_loss': advice['stop_loss_advice']
            }
        }
    
    @staticmethod
    def generate_investment_report_text(result):
        """
        產生投資建議文字報告（華爾街基金經理人風格）
        
        Args:
            result: QuickAnalyzer.analyze_stock() 的回傳結果
        
        Returns:
            str: 格式化的文字報告
        """
        report = DecisionMatrix.get_full_investment_report(result)
        
        if not report.get('available'):
            return "投資建議報告不可用"
        
        advice = report['investment_advice']
        scoring = report['scoring']
        short_term = scoring['short_term']
        long_term = scoring['long_term']
        
        lines = []
        lines.append("╔" + "═" * 62 + "╗")
        lines.append("║" + " " * 15 + "📊 雙軌評分投資建議報告 v2.0" + " " * 15 + "║")
        lines.append("╚" + "═" * 62 + "╝")
        
        # 基本資訊
        lines.append(f"\n📌 標的資訊")
        lines.append(f"   股票代碼：{report['symbol']}")
        if report.get('name'):
            lines.append(f"   股票名稱：{report['name']}")
        lines.append(f"   現價：${report['current_price']:.2f}")
        if report.get('price_change_pct'):
            pct = report['price_change_pct']
            sign = '+' if pct > 0 else ''
            lines.append(f"   漲跌幅：{sign}{pct:.2f}%")
        
        # 評分摘要
        lines.append(f"\n{'─' * 64}")
        lines.append(f"📈 評分摘要")
        lines.append(f"   ┌──────────────┬────────┬────────────┬──────────┐")
        lines.append(f"   │     類別     │  分數  │    區間    │   信心度 │")
        lines.append(f"   ├──────────────┼────────┼────────────┼──────────┤")
        lines.append(f"   │ 短線波段評分 │  {short_term['score']:3d}   │  {advice['short_zone']:^8s}  │  {short_term['confidence']:^6s}  │")
        lines.append(f"   │ 長線投資評分 │  {long_term['score']:3d}   │  {advice['long_zone']:^8s}  │  {long_term['confidence']:^6s}  │")
        lines.append(f"   ├──────────────┼────────┼────────────┴──────────┤")
        lines.append(f"   │ 加權總分     │  {advice['weighted_score']:3d}   │ (短線40% + 長線60%)  │")
        lines.append(f"   └──────────────┴────────┴───────────────────────┘")
        
        # 投資場景
        lines.append(f"\n{'─' * 64}")
        lines.append(f"{advice['emoji']} 投資場景：{advice['title']}")
        lines.append(f"   場景代碼：{advice['scenario_code']}")
        lines.append(f"   建議動作：{advice['action_zh']} ({advice['action']})")
        lines.append(f"   風險等級：{advice['risk_level']}")
        
        # 詳細說明
        lines.append(f"\n{'─' * 64}")
        lines.append(f"📝 詳細說明")
        # 將描述拆成多行（每行約 55 字）
        desc = advice['description']
        while len(desc) > 55:
            lines.append(f"   {desc[:55]}")
            desc = desc[55:]
        if desc:
            lines.append(f"   {desc}")
        
        # 操作建議
        lines.append(f"\n{'─' * 64}")
        lines.append(f"💼 操作建議")
        lines.append(f"   部位建議：{advice['position_advice']}")
        lines.append(f"   停損建議：{advice['stop_loss_advice']}")
        
        # 評分明細
        lines.append(f"\n{'─' * 64}")
        lines.append(f"📊 短線評分明細")
        for comp in short_term['components']:
            sign = '+' if comp['score'] > 0 else ''
            lines.append(f"   • [{comp['category']}] {comp['name']}: {sign}{comp['score']}")
        
        lines.append(f"\n📊 長線評分明細")
        for comp in long_term['components']:
            sign = '+' if comp['score'] > 0 else ''
            lines.append(f"   • [{comp['category']}] {comp['name']}: {sign}{comp['score']}")
        
        # 結尾
        lines.append(f"\n{'═' * 64}")
        lines.append(f"⚠️ 免責聲明：本報告僅供參考，不構成投資建議。")
        lines.append(f"   投資有風險，入市需謹慎。")
        lines.append(f"{'═' * 64}")
        
        return "\n".join(lines)
    
    @staticmethod
    def get_scenario_summary_table():
        """
        取得九大場景摘要表（用於文檔或 UI 顯示）
        
        Returns:
            list: 九大場景的摘要資訊列表
        """
        return [
            {
                'code': code,
                'title': scenario['title'],
                'action': scenario['action'],
                'action_zh': scenario['action_zh'],
                'short_zone': scenario['short_zone'],
                'long_zone': scenario['long_zone'],
                'risk_level': scenario['risk_level'],
                'emoji': scenario['emoji']
            }
            for code, scenario in DecisionMatrix.INVESTMENT_SCENARIOS.items()
        ]
    
    # ========================================================================
    # v4.5.0 新增：儀表板風格報告生成器 (Dashboard Report Generator)
    # ========================================================================
    
    @staticmethod
    def generate_dashboard_report(result):
        """
        生成儀表板風格的投資分析報告
        
        特點：
        1. 視覺區塊化 - 使用框線創造卡片效果
        2. 重點置頂 - 綜合評價和今日建議放最上方
        3. 評分透明化 - 表格呈現加減分細節
        4. 自動對齊 - 確保不同長度的內容都能正確顯示
        
        Args:
            result: 完整的分析結果字典
        
        Returns:
            str: 格式化的儀表板報告文字
        """
        import datetime
        
        # ════════════════════════════════════════════════════════════════════
        # 輔助函數：字串對齊（處理中文字元寬度）
        # ════════════════════════════════════════════════════════════════════
        def get_display_width(s):
            """計算字串顯示寬度（中文字元算2，英文算1）"""
            width = 0
            for char in s:
                if '\u4e00' <= char <= '\u9fff' or '\u3000' <= char <= '\u303f':
                    width += 2
                elif '\uff00' <= char <= '\uffef':
                    width += 2
                else:
                    width += 1
            return width
        
        def pad_to_width(s, width, align='left'):
            """將字串填充到指定寬度"""
            current_width = get_display_width(s)
            padding = width - current_width
            if padding <= 0:
                return s
            if align == 'left':
                return s + ' ' * padding
            elif align == 'right':
                return ' ' * padding + s
            else:  # center
                left_pad = padding // 2
                right_pad = padding - left_pad
                return ' ' * left_pad + s + ' ' * right_pad
        
        def create_box_line(width, left='│', right='│', fill=' '):
            """創建框線內容行"""
            return left + fill * width + right
        
        # ════════════════════════════════════════════════════════════════════
        # 取得基本資訊
        # ════════════════════════════════════════════════════════════════════
        symbol = result.get('symbol', 'N/A')
        name = result.get('name', '')
        current_price = result.get('current_price', 0)
        price_change = result.get('price_change', 0)
        price_change_pct = result.get('price_change_pct', 0)
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # 計算雙軌評分
        short_term = DecisionMatrix.calculate_short_term_score(result)
        long_term = DecisionMatrix.calculate_long_term_score(result)
        advice = DecisionMatrix.get_investment_advice(short_term['score'], long_term['score'])
        weighted_score = advice['weighted_score']
        
        # 取得技術指標
        tech = result.get('technical', {})
        ma5 = tech.get('ma5', 0)
        ma20 = tech.get('ma20', 0)
        ma60 = tech.get('ma60', 0)
        rsi = tech.get('rsi', 0)
        
        # 取得乖離率
        mr = result.get('mean_reversion', {})
        bias_20 = mr.get('bias_analysis', {}).get('bias_20', 0) if mr.get('available') else 0
        
        # 取得支撐壓力
        sr = result.get('support_resistance', {})
        resistance1 = sr.get('resistance1', 0)
        support1 = sr.get('support1', 0)
        
        # 取得推薦建議
        rec = result.get('recommendation', {})
        
        # 定義報告寬度
        BOX_WIDTH = 68
        INNER_WIDTH = BOX_WIDTH - 2
        
        lines = []
        
        # ════════════════════════════════════════════════════════════════════
        # 1. 頂部核心區 (Header & Summary) - 最醒目
        # ════════════════════════════════════════════════════════════════════
        
        # 股票名稱和報告時間
        display_name = f"{symbol} {name}" if name else symbol
        header_title = f"🚀 {display_name} 分析報告"
        header_time = now
        
        # 綜合評價
        score_level = advice.get('action_zh', '觀望')
        score_action = advice.get('action', 'Neutral')
        scenario_emoji = advice.get('emoji', '🤷')
        scenario_title = advice.get('title', '')
        
        # 生成星級評分
        star_count = min(5, max(1, (weighted_score - 20) // 16 + 1))
        stars = '★' * star_count + '☆' * (5 - star_count)
        
        # 頂部框
        lines.append("╔" + "═" * BOX_WIDTH + "╗")
        
        # 標題行
        title_line = f"  {header_title}"
        time_line = f"{header_time}  "
        title_content = pad_to_width(title_line, INNER_WIDTH - get_display_width(time_line)) + time_line
        lines.append("║" + title_content + "║")
        
        lines.append("╠" + "═" * BOX_WIDTH + "╣")
        lines.append("║" + " " * BOX_WIDTH + "║")
        
        # 綜合評價行
        eval_label = "【綜合評價】"
        eval_content = f"{score_level} ({score_action})"
        score_display = f"{stars} 總分: {weighted_score} 分"
        eval_line = f"  {eval_label}  {eval_content}"
        eval_full = pad_to_width(eval_line, INNER_WIDTH - get_display_width(score_display) - 2) + score_display + "  "
        lines.append("║" + eval_full + "║")
        
        lines.append("║" + " " * BOX_WIDTH + "║")
        
        # 今日建議行
        today_label = "👉 今日建議："
        today_scenario = f"[{scenario_title}]" if scenario_title else ""
        raw_desc = advice.get('description')
        today_desc = str(raw_desc)[:35] if raw_desc else "建議觀望"
        if len(today_desc) > 35:
            today_desc = today_desc[:32] + "..."
        today_line = f"  {today_label}{today_scenario}"
        lines.append("║" + pad_to_width(today_line, BOX_WIDTH) + "║")
        
        if today_desc:
            desc_line = f"     {today_desc}"
            lines.append("║" + pad_to_width(desc_line, BOX_WIDTH) + "║")
        
        lines.append("║" + " " * BOX_WIDTH + "║")
        lines.append("╚" + "═" * BOX_WIDTH + "╝")
        lines.append("")
        
        # ════════════════════════════════════════════════════════════════════
        # 2. 操作建議區 (Action Plan) - 清楚指引
        # ════════════════════════════════════════════════════════════════════
        
        lines.append("┌" + "─" * BOX_WIDTH + "┐")
        lines.append("│" + pad_to_width("  ⚡ 操作策略指引 (Action Plan)", BOX_WIDTH) + "│")
        lines.append("├" + "─" * BOX_WIDTH + "┤")
        
        # 短線操作
        short_rec = rec.get('short_term', {}) if isinstance(rec, dict) else {}
        if not isinstance(short_rec, dict):
            short_rec = {}
        short_action = short_rec.get('action') or '觀望'
        short_reason = short_rec.get('reason') or '技術面中性'
        
        short_tag = "買進 (Buy)" if any(x in str(short_action) for x in ["買進", "進場", "試單"]) else \
                    "賣出 (Sell)" if any(x in str(short_action) for x in ["賣出", "減碼"]) else \
                    "觀望 (Hold)"
        
        lines.append("│" + pad_to_width(f"  ● 短線操作 (1-5日)： {short_tag}", BOX_WIDTH) + "│")
        lines.append("│" + pad_to_width(f"    └─ 理由：{str(short_reason)[:45]}", BOX_WIDTH) + "│")
        lines.append("│" + " " * BOX_WIDTH + "│")
        
        # 中長線操作
        long_rec = rec.get('long_term', {}) if isinstance(rec, dict) else {}
        if not isinstance(long_rec, dict):
            long_rec = {}
        long_action = long_rec.get('action') or '觀望'
        long_reason = long_rec.get('reason') or '基本面中性'
        
        long_tag = "買進 (Buy)" if any(x in str(long_action) for x in ["買進", "看好", "偏多"]) else \
                   "賣出 (Sell)" if any(x in str(long_action) for x in ["賣出", "偏空"]) else \
                   "續抱 (Hold)"
        
        lines.append("│" + pad_to_width(f"  ● 中長線操作 (週/月)： {long_tag}", BOX_WIDTH) + "│")
        lines.append("│" + pad_to_width(f"    └─ 理由：{str(long_reason)[:45]}", BOX_WIDTH) + "│")
        lines.append("└" + "─" * BOX_WIDTH + "┘")
        lines.append("")
        
        # ════════════════════════════════════════════════════════════════════
        # 3. 評分細節表 (Scoring Breakdown) - 表格呈現
        # ════════════════════════════════════════════════════════════════════
        
        lines.append("┌" + "─" * BOX_WIDTH + "┐")
        lines.append("│" + pad_to_width("  📊 評分細節表 (Scoring Details)", BOX_WIDTH) + "│")
        
        # 表頭
        col1_width = 34
        col2_width = 14
        col3_width = 14
        
        lines.append("├" + "─" * col1_width + "┬" + "─" * col2_width + "┬" + "─" * col3_width + "┤")
        
        header1 = pad_to_width("  指標項目 (Factor)", col1_width)
        header2 = pad_to_width(" 評分 (Score)", col2_width)
        header3 = pad_to_width(" 狀態 (Status)", col3_width)
        lines.append("│" + header1 + "│" + header2 + "│" + header3 + "│")
        
        lines.append("├" + "─" * col1_width + "┼" + "─" * col2_width + "┼" + "─" * col3_width + "┤")
        
        # 短線評分項目（使用 components 列表，不是 breakdown 字典）
        short_components = short_term.get('components') or []
        for item in short_components[:5]:  # 最多顯示 5 項
            factor = (item.get('name') or '')[:16]
            points = item.get('score', 0)
            
            if points > 0:
                status = "✅ 加分"
                points_str = f"+{points}"
            elif points < 0:
                status = "⚠️ 扣分"
                points_str = str(points)
            else:
                status = "➖ 無影響"
                points_str = "0"
            
            row1 = pad_to_width(f"  [短線] {factor}", col1_width)
            row2 = pad_to_width(f"   {points_str}", col2_width)
            row3 = pad_to_width(f"  {status}", col3_width)
            lines.append("│" + row1 + "│" + row2 + "│" + row3 + "│")
        
        # 長線評分項目（使用 components 列表）
        long_components = long_term.get('components') or []
        for item in long_components[:3]:  # 最多顯示 3 項
            factor = (item.get('name') or '')[:16]
            points = item.get('score', 0)
            
            if points > 0:
                status = "✅ 加分"
                points_str = f"+{points}"
            elif points < 0:
                status = "⚠️ 扣分"
                points_str = str(points)
            else:
                status = "➖ 無影響"
                points_str = "0"
            
            row1 = pad_to_width(f"  [長線] {factor}", col1_width)
            row2 = pad_to_width(f"   {points_str}", col2_width)
            row3 = pad_to_width(f"  {status}", col3_width)
            lines.append("│" + row1 + "│" + row2 + "│" + row3 + "│")
        
        # 總計行
        lines.append("├" + "─" * col1_width + "┼" + "─" * col2_width + "┼" + "─" * col3_width + "┤")
        
        total_label = pad_to_width("  總計 (Total)", col1_width)
        short_total = pad_to_width(f"  短:{short_term.get('score', 50)}", col2_width)
        long_total = pad_to_width(f"  長:{long_term.get('score', 50)}", col3_width)
        lines.append("│" + total_label + "│" + short_total + "│" + long_total + "│")
        
        lines.append("└" + "─" * col1_width + "┴" + "─" * col2_width + "┴" + "─" * col3_width + "┘")
        lines.append("")
        
        # ════════════════════════════════════════════════════════════════════
        # 4. 九大場景矩陣 (Scenario Matrix)
        # ════════════════════════════════════════════════════════════════════
        
        scenario_code = advice.get('scenario_code', 'E')
        
        lines.append("┌" + "─" * BOX_WIDTH + "┐")
        lines.append("│" + pad_to_width("  🎯 九大投資場景判定 (Investment Scenario)", BOX_WIDTH) + "│")
        lines.append("├" + "─" * BOX_WIDTH + "┤")
        
        # 場景矩陣顯示
        matrix_header = "     短線＼長線 │ 長線高(H) │ 長線中(M) │ 長線低(L) "
        lines.append("│" + pad_to_width(matrix_header, BOX_WIDTH) + "│")
        lines.append("│" + pad_to_width("    ─────────┼───────────┼───────────┼───────────", BOX_WIDTH) + "│")
        
        # 高亮當前場景
        def highlight(code, current):
            return f"[{code}]" if code == current else f" {code} "
        
        row_h = f"     短線高(H)  │  {highlight('A', scenario_code)}雙強   │  {highlight('I', scenario_code)}蓄勢   │  {highlight('C', scenario_code)}投機   "
        row_m = f"     短線中(M)  │  {highlight('D', scenario_code)}高檔   │  {highlight('E', scenario_code)}觀望   │  {highlight('F', scenario_code)}弱勢   "
        row_l = f"     短線低(L)  │  {highlight('B', scenario_code)}佈局   │  {highlight('G', scenario_code)}頭部   │  {highlight('H', scenario_code)}空頭   "
        
        lines.append("│" + pad_to_width(row_h, BOX_WIDTH) + "│")
        lines.append("│" + pad_to_width(row_m, BOX_WIDTH) + "│")
        lines.append("│" + pad_to_width(row_l, BOX_WIDTH) + "│")
        
        lines.append("├" + "─" * BOX_WIDTH + "┤")
        
        # 當前場景說明
        current_scenario = f"  ➤ 當前場景：{scenario_emoji} 場景 {scenario_code} {scenario_title}"
        lines.append("│" + pad_to_width(current_scenario, BOX_WIDTH) + "│")
        lines.append("│" + pad_to_width(f"    風險等級：{advice.get('risk_level') or 'Medium'}", BOX_WIDTH) + "│")
        position_advice = advice.get('position_advice') or 'N/A'
        lines.append("│" + pad_to_width(f"    部位建議：{str(position_advice)[:40]}", BOX_WIDTH) + "│")
        
        lines.append("└" + "─" * BOX_WIDTH + "┘")
        lines.append("")
        
        # ════════════════════════════════════════════════════════════════════
        # 5. 關鍵技術數據區 (Key Metrics)
        # ════════════════════════════════════════════════════════════════════
        
        lines.append("┌" + "─" * BOX_WIDTH + "┐")
        lines.append("│" + pad_to_width("  📉 關鍵技術數據 (Technical Metrics)", BOX_WIDTH) + "│")
        lines.append("├" + "─" * BOX_WIDTH + "┤")
        
        # 股價資訊
        price_sign = "▲" if price_change > 0 else "▼" if price_change < 0 else "─"
        price_color = "+" if price_change > 0 else "" if price_change < 0 else ""
        price_line = f"  股價：${current_price:.2f}  {price_sign} {price_color}{price_change:.2f} ({price_color}{price_change_pct:.2f}%)"
        lines.append("│" + pad_to_width(price_line, BOX_WIDTH) + "│")
        
        # 均線資訊
        ma_trend = "多頭排列" if (ma5 > ma20 > ma60 and ma5 > 0) else "空頭排列" if (ma5 < ma20 < ma60 and ma5 > 0) else "糾結整理"
        ma_line = f"  MA5: {ma5:.1f}  |  MA20: {ma20:.1f}  |  MA60: {ma60:.1f}  ({ma_trend})"
        lines.append("│" + pad_to_width(ma_line, BOX_WIDTH) + "│")
        
        # RSI 和乖離率
        rsi_status = "超買" if rsi > 80 else "偏熱" if rsi > 70 else "超賣" if rsi < 20 else "偏冷" if rsi < 30 else "中性"
        rsi_line = f"  RSI: {rsi:.0f} ({rsi_status})  |  乖離率: {bias_20:+.2f}%"
        lines.append("│" + pad_to_width(rsi_line, BOX_WIDTH) + "│")
        
        # 支撐壓力
        if resistance1 > 0 and support1 > 0:
            sr_line = f"  壓力位: ${resistance1:.2f}  |  支撐位: ${support1:.2f}"
            lines.append("│" + pad_to_width(sr_line, BOX_WIDTH) + "│")
        
        lines.append("└" + "─" * BOX_WIDTH + "┘")
        lines.append("")
        
        # ════════════════════════════════════════════════════════════════════
        # 6. 風險提示 (Risk Disclaimer)
        # ════════════════════════════════════════════════════════════════════
        
        lines.append("─" * (BOX_WIDTH + 2))
        lines.append("⚠️ 風險提示：投資有風險，本分析僅供參考，請嚴格執行個人止損計劃。")
        lines.append(f"📊 報告版本：DecisionMatrix v4.5.0 | 生成時間：{now}")
        
        return "\n".join(lines)
    
    @staticmethod
    def generate_professional_report(result, include_details=True):
        """
        生成專業投資分析報告
        
        整合雙軌評分系統、九大投資場景、技術分析等所有模塊，
        產出類似華爾街基金經理人風格的完整報告。
        
        Args:
            result: 完整的分析結果字典（包含所有模塊的分析資料）
            include_details: 是否包含詳細評分明細（預設 True）
        
        Returns:
            str: 格式化的專業報告文字
        """
        import datetime
        
        # 取得基本資訊
        symbol = result.get('symbol', 'N/A')
        name = result.get('name', '')
        current_price = result.get('current_price', 0)
        price_change = result.get('price_change', 0)
        price_change_pct = result.get('price_change_pct', 0)
        
        # 計算雙軌評分
        short_term = DecisionMatrix.calculate_short_term_score(result)
        long_term = DecisionMatrix.calculate_long_term_score(result)
        
        # 取得投資建議
        advice = DecisionMatrix.get_investment_advice(short_term['score'], long_term['score'])
        
        # 計算加權總分
        weighted_score = advice['weighted_score']
        
        # 取得技術指標
        tech = result.get('technical', {})
        rsi = tech.get('rsi', 0)
        k_value = tech.get('k', 0)
        d_value = tech.get('d', 0)
        macd = tech.get('macd', 0)
        macd_signal = tech.get('macd_signal', 0)
        adx = tech.get('adx', 0)
        
        # 取得均線資訊
        ma5 = tech.get('ma5', 0)
        ma10 = tech.get('ma10', 0)
        ma20 = tech.get('ma20', 0)
        ma60 = tech.get('ma60', 0)
        ma120 = tech.get('ma120', 0)
        ma240 = tech.get('ma240', 0)
        
        # 取得乖離率
        mr = result.get('mean_reversion', {})
        bias_5 = mr.get('bias_analysis', {}).get('bias_5', 0) if mr.get('available') else 0
        bias_20 = mr.get('bias_analysis', {}).get('bias_20', 0) if mr.get('available') else 0
        bias_60 = mr.get('bias_analysis', {}).get('bias_60', 0) if mr.get('available') else 0
        
        # 取得形態分析
        pattern = result.get('pattern_analysis', {})
        pattern_detected = pattern.get('detected', False)
        pattern_name = pattern.get('pattern_name', '')
        pattern_status = pattern.get('status', '')
        pattern_target = pattern.get('target_price', 0)
        
        # 取得波段分析
        wave = result.get('wave_analysis', {})
        wave_available = wave.get('available', False)
        is_bullish_env = wave.get('is_bullish_env', False)
        breakout_detected = wave.get('breakout_signal', {}).get('detected', False) if wave_available else False
        breakdown_detected = wave.get('breakdown_signal', {}).get('detected', False) if wave_available else False
        
        # 取得支撐壓力
        sr = result.get('support_resistance', {})
        resistance1 = sr.get('resistance1', 0)
        support1 = sr.get('support1', 0)
        
        # 取得成交量分析
        vol = result.get('volume_analysis', {})
        volume_ratio = vol.get('volume_ratio', 1.0)
        volume_trend = vol.get('volume_trend', '')
        
        # 開始構建報告
        lines = []
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
        
        # ════════════════════════════════════════════════════════════════
        # 報告標題
        # ════════════════════════════════════════════════════════════════
        lines.append("┏" + "━" * 68 + "┓")
        lines.append("┃" + " " * 20 + "📊 專業投資分析報告" + " " * 21 + "┃")
        lines.append("┃" + " " * 18 + f"Professional Investment Report" + " " * 18 + "┃")
        lines.append("┗" + "━" * 68 + "┛")
        lines.append(f"  報告日期：{now}")
        lines.append("")
        
        # ════════════════════════════════════════════════════════════════
        # 第一部分：標的摘要
        # ════════════════════════════════════════════════════════════════
        lines.append("╔" + "═" * 68 + "╗")
        lines.append("║  【第一部分】標的摘要 Executive Summary" + " " * 24 + "║")
        lines.append("╚" + "═" * 68 + "╝")
        lines.append("")
        
        # 基本資訊表格
        display_name = f"{symbol} {name}" if name else symbol
        pct_sign = '+' if price_change_pct > 0 else ''
        price_sign = '+' if price_change > 0 else ''
        
        lines.append(f"  標的代碼：{display_name}")
        lines.append(f"  現價：${current_price:.2f}｜漲跌：{price_sign}{price_change:.2f} ({pct_sign}{price_change_pct:.2f}%)")
        lines.append("")
        
        # 核心評分摘要框
        lines.append("  ┌─────────────────────────────────────────────────────────────────┐")
        lines.append(f"  │  🎯 投資評級：{advice['action_zh']} ({advice['action']})".ljust(66) + "│")
        lines.append(f"  │  📈 場景判定：{advice['title']}".ljust(62) + "│")
        lines.append("  │" + "─" * 65 + "│")
        lines.append(f"  │  短線評分：{short_term['score']:3d} 分 ({advice['short_zone']})　　長線評分：{long_term['score']:3d} 分 ({advice['long_zone']})".ljust(56) + "│")
        lines.append(f"  │  加權總分：{weighted_score:3d} 分　　　　　　　風險等級：{advice['risk_level']}".ljust(58) + "│")
        lines.append("  └─────────────────────────────────────────────────────────────────┘")
        lines.append("")
        
        # v4.5.12 新增：機構儀表板 (Institutional Dashboard)
        risk_mgr = result.get('risk_manager', {})
        beta = risk_mgr.get('beta', 1.0) if risk_mgr else 1.0
        atr = risk_mgr.get('atr', 0) if risk_mgr else 0
        atr_pct = risk_mgr.get('atr_pct', 0) if risk_mgr else 0
        
        # 計算風險等級文字
        if beta > 1.5:
            beta_desc = "⚠️高波動"
        elif beta > 1.2:
            beta_desc = "📈偏高"
        elif beta < 0.8:
            beta_desc = "🛡️防禦"
        else:
            beta_desc = "📊正常"
        
        lines.append("  【機構儀表板 Institutional Dashboard】")
        lines.append(f"    • 風險係數 (Beta): {beta:.2f} {beta_desc}")
        lines.append(f"    • 真實波幅 (ATR): ${atr:.2f}" + (f" ({atr_pct:.1f}%)" if atr_pct > 0 else ""))
        
        # 計算相對強度 (RS) - 如果有大盤數據可用
        rs_score = result.get('relative_strength', {}).get('rs_score', None)
        if rs_score is not None:
            rs_desc = "強於大盤" if rs_score > 0 else "弱於大盤"
            lines.append(f"    • 相對強度 (RS): {rs_score:+.1f}% ({rs_desc})")
        
        # 顯示停損建議
        if atr > 0 and current_price > 0:
            stop_price = current_price - (2.5 * atr)
            lines.append(f"    • 建議停損 (2.5*ATR): ${stop_price:.2f}")
        
        lines.append("")
        
        # ════════════════════════════════════════════════════════════════
        # 第二部分：投資建議
        # ════════════════════════════════════════════════════════════════
        lines.append("╔" + "═" * 68 + "╗")
        lines.append("║  【第二部分】投資建議 Investment Recommendation" + " " * 17 + "║")
        lines.append("╚" + "═" * 68 + "╝")
        lines.append("")
        
        # 場景描述
        lines.append(f"  {advice['emoji']} {advice['title']}")
        lines.append("")
        
        # 將描述拆成多行
        desc = advice['description']
        lines.append("  【詳細說明】")
        while len(desc) > 60:
            lines.append(f"    {desc[:60]}")
            desc = desc[60:]
        if desc:
            lines.append(f"    {desc}")
        lines.append("")
        
        # 操作建議
        lines.append("  【操作建議】")
        lines.append(f"    • 部位控制：{advice['position_advice']}")
        lines.append(f"    • 停損策略：{advice['stop_loss_advice']}")
        lines.append("")
        
        # ════════════════════════════════════════════════════════════════
        # 第三部分：技術分析儀表板
        # ════════════════════════════════════════════════════════════════
        lines.append("╔" + "═" * 68 + "╗")
        lines.append("║  【第三部分】技術分析儀表板 Technical Dashboard" + " " * 17 + "║")
        lines.append("╚" + "═" * 68 + "╝")
        lines.append("")
        
        # 均線狀態
        lines.append("  【均線位階】")
        ma_status = []
        if ma5 > 0 and current_price > 0:
            ma_status.append(f"MA5=${ma5:.1f}" + ("↑" if current_price > ma5 else "↓"))
        if ma20 > 0:
            ma_status.append(f"MA20=${ma20:.1f}" + ("↑" if current_price > ma20 else "↓"))
        if ma60 > 0:
            ma_status.append(f"MA60=${ma60:.1f}" + ("↑" if current_price > ma60 else "↓"))
        if ma240 > 0:
            ma_status.append(f"MA240=${ma240:.1f}" + ("↑" if current_price > ma240 else "↓"))
        lines.append(f"    {' ｜ '.join(ma_status) if ma_status else '資料不足'}")
        lines.append("")
        
        # 技術指標表格
        lines.append("  【技術指標】")
        lines.append("    ┌────────────┬────────────┬────────────┬────────────┐")
        lines.append(f"    │  RSI      │  KD        │  MACD      │  ADX       │")
        lines.append("    ├────────────┼────────────┼────────────┼────────────┤")
        
        # RSI 狀態
        if rsi > 80:
            rsi_status = "超買"
        elif rsi < 20:
            rsi_status = "超賣"
        elif rsi > 50:
            rsi_status = "偏多"
        else:
            rsi_status = "偏空"
        
        # KD 狀態
        if k_value > d_value:
            kd_status = "金叉"
        else:
            kd_status = "死叉"
        
        # MACD 狀態
        if macd > macd_signal:
            macd_status = "多頭"
        else:
            macd_status = "空頭"
        
        # ADX 狀態
        if adx > 25:
            adx_status = "趨勢強"
        else:
            adx_status = "盤整"
        
        lines.append(f"    │  {rsi:5.1f}    │  K:{k_value:4.0f}    │  {macd:+7.2f}  │  {adx:5.1f}    │")
        lines.append(f"    │  ({rsi_status})    │  D:{d_value:4.0f}    │  ({macd_status})    │  ({adx_status})  │")
        lines.append(f"    │           │  ({kd_status})    │           │           │")
        lines.append("    └────────────┴────────────┴────────────┴────────────┘")
        lines.append("")
        
        # 乖離率
        lines.append("  【乖離率分析】")
        bias_5_status = "過熱" if bias_5 > 5 else ("超跌" if bias_5 < -5 else "正常")
        bias_20_status = "過熱" if bias_20 > 8 else ("超跌" if bias_20 < -8 else "正常")
        bias_60_status = "過熱" if bias_60 > 12 else ("超跌" if bias_60 < -12 else "正常")
        lines.append(f"    5日乖離：{bias_5:+.2f}% ({bias_5_status}) ｜ 20日乖離：{bias_20:+.2f}% ({bias_20_status}) ｜ 60日乖離：{bias_60:+.2f}% ({bias_60_status})")
        lines.append("")
        
        # ════════════════════════════════════════════════════════════════
        # 第四部分：形態與波段分析
        # ════════════════════════════════════════════════════════════════
        lines.append("╔" + "═" * 68 + "╗")
        lines.append("║  【第四部分】形態與波段分析 Pattern & Wave Analysis" + " " * 13 + "║")
        lines.append("╚" + "═" * 68 + "╝")
        lines.append("")
        
        # 形態分析
        lines.append("  【形態辨識】")
        if pattern_detected:
            status_zh = "確立" if "CONFIRMED" in pattern_status else "形成中"
            lines.append(f"    ✓ 偵測到：{pattern_name}（{status_zh}）")
            if pattern_target > 0:
                target_pct = ((pattern_target - current_price) / current_price * 100) if current_price > 0 else 0
                lines.append(f"    ✓ 測量目標價：${pattern_target:.2f}（距現價 {target_pct:+.1f}%）")
        else:
            lines.append("    ✗ 目前無明確形態")
        lines.append("")
        
        # 波段分析
        lines.append("  【波段狀態】")
        if wave_available:
            if breakout_detected:
                lines.append("    🟢 三盤突破訊號出現 - 短線做多訊號")
            elif breakdown_detected:
                lines.append("    🔴 三盤跌破訊號出現 - 短線做空訊號")
            elif is_bullish_env:
                lines.append("    🟡 多頭環境（站上55MA且均線上揚）- 等待突破")
            else:
                lines.append("    ⚪ 空頭或盤整環境 - 觀望")
        else:
            lines.append("    資料不足，無法判斷波段")
        lines.append("")
        
        # ════════════════════════════════════════════════════════════════
        # 第五部分：量能分析
        # ════════════════════════════════════════════════════════════════
        lines.append("╔" + "═" * 68 + "╗")
        lines.append("║  【第五部分】量能分析 Volume Analysis" + " " * 26 + "║")
        lines.append("╚" + "═" * 68 + "╝")
        lines.append("")
        
        lines.append("  【成交量狀態】")
        vol_status = "爆量" if volume_ratio > 1.5 else ("量增" if volume_ratio > 1.0 else "量縮")
        lines.append(f"    今日成交量：均量的 {volume_ratio:.1%}（{vol_status}）")
        lines.append(f"    量能趨勢：{volume_trend if volume_trend else '無明顯趨勢'}")
        lines.append("")
        
        # ════════════════════════════════════════════════════════════════
        # 第六部分：支撐壓力與目標價
        # ════════════════════════════════════════════════════════════════
        lines.append("╔" + "═" * 68 + "╗")
        lines.append("║  【第六部分】支撐壓力與目標價 Support & Resistance" + " " * 14 + "║")
        lines.append("╚" + "═" * 68 + "╝")
        lines.append("")
        
        lines.append("  【關鍵價位】")
        if resistance1 > 0:
            r1_pct = ((resistance1 - current_price) / current_price * 100) if current_price > 0 else 0
            lines.append(f"    壓力位：${resistance1:.2f}（距現價 {r1_pct:+.1f}%）")
        if support1 > 0:
            s1_pct = ((support1 - current_price) / current_price * 100) if current_price > 0 else 0
            lines.append(f"    支撐位：${support1:.2f}（距現價 {s1_pct:+.1f}%）")
        lines.append("")
        
        # ════════════════════════════════════════════════════════════════
        # 第七部分：評分明細（可選）
        # ════════════════════════════════════════════════════════════════
        if include_details:
            lines.append("╔" + "═" * 68 + "╗")
            lines.append("║  【第七部分】評分明細 Score Breakdown" + " " * 27 + "║")
            lines.append("╚" + "═" * 68 + "╝")
            lines.append("")
            
            # 短線評分明細
            lines.append("  【短線波段評分】")
            lines.append(f"    基準分：{short_term['base_score']} 分")
            for comp in short_term['components']:
                sign = '+' if comp['score'] > 0 else ''
                lines.append(f"    {sign}{comp['score']:3d} │ [{comp['category']:8s}] {comp['name']}")
            lines.append(f"    {'─' * 50}")
            total_adj = short_term.get('total_adjustment', sum(c['score'] for c in short_term['components']))
            raw = short_term.get('raw_score', short_term['score'])
            sign = '+' if total_adj > 0 else ''
            lines.append(f"    總計：{short_term['base_score']} {sign}{total_adj} = {raw}")
            if raw != short_term['score']:
                lines.append(f"    截斷後：{short_term['score']} 分")
            else:
                lines.append(f"    最終分數：{short_term['score']} 分")
            lines.append("")
            
            # 長線評分明細
            lines.append("  【長線投資評分】")
            lines.append(f"    基準分：{long_term['base_score']} 分")
            for comp in long_term['components']:
                sign = '+' if comp['score'] > 0 else ''
                lines.append(f"    {sign}{comp['score']:3d} │ [{comp['category']:12s}] {comp['name']}")
            lines.append(f"    {'─' * 50}")
            total_adj = long_term.get('total_adjustment', sum(c['score'] for c in long_term['components']))
            raw = long_term.get('raw_score', long_term['score'])
            sign = '+' if total_adj > 0 else ''
            lines.append(f"    總計：{long_term['base_score']} {sign}{total_adj} = {raw}")
            if raw != long_term['score']:
                lines.append(f"    截斷後：{long_term['score']} 分")
            else:
                lines.append(f"    最終分數：{long_term['score']} 分")
            lines.append("")
        
        # ════════════════════════════════════════════════════════════════
        # 報告結尾
        # ════════════════════════════════════════════════════════════════
        lines.append("┏" + "━" * 68 + "┓")
        lines.append("┃  ⚠️ 免責聲明                                                        ┃")
        lines.append("┃  本報告由系統自動生成，僅供參考，不構成任何投資建議。                ┃")
        lines.append("┃  投資有風險，入市需謹慎。請依據個人風險承受能力做出投資決策。        ┃")
        lines.append("┗" + "━" * 68 + "┛")
        
        return "\n".join(lines)
    
    @staticmethod
    def generate_quick_summary(result):
        """
        生成快速摘要報告（簡潔版）
        
        適合在 watchlist 或快速瀏覽時使用。
        
        Args:
            result: 分析結果字典
        
        Returns:
            str: 簡潔的摘要報告
        """
        symbol = result.get('symbol', 'N/A')
        current_price = result.get('current_price', 0)
        price_change_pct = result.get('price_change_pct', 0)
        
        # 計算評分
        short_term = DecisionMatrix.calculate_short_term_score(result)
        long_term = DecisionMatrix.calculate_long_term_score(result)
        advice = DecisionMatrix.get_investment_advice(short_term['score'], long_term['score'])
        
        pct_sign = '+' if price_change_pct > 0 else ''
        
        lines = []
        lines.append(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"📊 {symbol} │ ${current_price:.2f} ({pct_sign}{price_change_pct:.2f}%)")
        lines.append(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"{advice['emoji']} {advice['title']}")
        lines.append(f"🎯 建議：{advice['action_zh']} │ 風險：{advice['risk_level']}")
        lines.append(f"📈 短線：{short_term['score']}分 │ 📉 長線：{long_term['score']}分 │ 總分：{advice['weighted_score']}分")
        lines.append(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        
        return "\n".join(lines)


# ============================================================================
# v4.1 新增：波段分析器（三盤突破/跌破邏輯）
# ============================================================================

class WaveAnalyzer:
    """
    波段分析器 v4.1
    實現「三盤突破」進場邏輯與「三盤跌破」出場邏輯
    
    核心邏輯：
    1. 環境篩選：K線 > 55MA 且 55MA 上揚
    2. 進場訊號：收盤價 > 前兩日最高價（三盤突破）
    3. 出場訊號：收盤價 < 前兩日最低價（三盤跌破）
    4. 爆量K線守則：爆量K線低點不可被收破
    """
    
    @staticmethod
    def analyze_wave(df):
        """
        完整波段分析
        
        Args:
            df: 包含 OHLCV 的 DataFrame（至少需要 60 天數據）
        
        Returns:
            dict: 波段分析結果
        """
        try:
            if len(df) < 60:
                return {
                    'available': False,
                    'message': '數據不足，需要至少60天'
                }
            
            # 1. 計算 55 日均線
            ma55 = df['Close'].rolling(window=QuantConfig.WAVE_MA_PERIOD).mean()
            
            # 2. 判斷均線趨勢（用近5日均線斜率判斷上揚/下彎）
            ma55_slope = WaveAnalyzer._calculate_ma_slope(ma55)
            
            # 3. 當前價格與均線關係
            current_price = df['Close'].iloc[-1]
            current_ma55 = ma55.iloc[-1]
            price_above_ma = current_price > current_ma55
            
            # 4. 多頭環境判斷
            is_bullish_env = price_above_ma and ma55_slope > 0
            
            # 5. 三盤突破偵測（進場訊號）
            breakout_signal = WaveAnalyzer._detect_three_bar_breakout(df)
            
            # 6. 三盤跌破偵測（出場訊號）
            breakdown_signal = WaveAnalyzer._detect_three_bar_breakdown(df)
            
            # 7. 爆量K線分析
            volume_bar_analysis = WaveAnalyzer._analyze_key_volume_bar(df)
            
            # 8. 量價共振判斷
            volume_price_sync = WaveAnalyzer._check_volume_price_sync(df, ma55)
            
            # 9. 綜合波段狀態判斷
            wave_status, status_detail, action_advice = WaveAnalyzer._determine_wave_status(
                is_bullish_env, ma55_slope, breakout_signal, breakdown_signal, 
                volume_bar_analysis, volume_price_sync, current_price, current_ma55
            )
            
            return {
                'available': True,
                'wave_status': wave_status,  # 波段狀態
                'status_detail': status_detail,  # 波段狀態說明
                'action_advice': action_advice,  # 進場/出場建議
                
                # 環境分析
                'is_bullish_env': is_bullish_env,
                'price_above_ma55': price_above_ma,
                'ma55_trend': '上揚' if ma55_slope > 0 else '下彎' if ma55_slope < 0 else '走平',
                'ma55_value': round(current_ma55, 2),
                'ma55_distance': round((current_price / current_ma55 - 1) * 100, 2),  # 距離均線百分比
                
                # 進出場訊號
                'breakout_signal': breakout_signal,
                'breakdown_signal': breakdown_signal,
                
                # 爆量K線
                'volume_bar': volume_bar_analysis,
                
                # 量價共振
                'volume_price_sync': volume_price_sync
            }
            
        except Exception as e:
            print(f"波段分析錯誤: {e}")
            import traceback
            traceback.print_exc()
            return {
                'available': False,
                'message': f'分析錯誤: {str(e)}'
            }
    
    @staticmethod
    def _calculate_ma_slope(ma_series, lookback=5):
        """計算均線斜率（判斷上揚/下彎）"""
        try:
            recent_ma = ma_series.dropna().tail(lookback)
            if len(recent_ma) < lookback:
                return 0
            
            # 使用線性迴歸計算斜率
            x = np.arange(len(recent_ma))
            slope, _, _, _, _ = linregress(x, recent_ma.values)
            
            # 標準化斜率（相對於均線值）
            normalized_slope = slope / recent_ma.mean() * 100
            return normalized_slope
        except:
            return 0
    
    @staticmethod
    def _detect_three_bar_breakout(df):
        """
        偵測三盤突破（進場訊號）
        定義：當前K線收盤價 > 前兩根K線的最高價
        """
        try:
            if len(df) < 3:
                return {'detected': False}
            
            current_close = df['Close'].iloc[-1]
            prev_high_1 = df['High'].iloc[-2]  # 前一日最高
            prev_high_2 = df['High'].iloc[-3]  # 前兩日最高
            max_prev_high = max(prev_high_1, prev_high_2)
            
            detected = current_close > max_prev_high
            
            # 檢查成交量是否放大
            current_volume = df['Volume'].iloc[-1]
            avg_volume = df['Volume'].rolling(window=20).mean().iloc[-1]
            volume_expanded = current_volume > avg_volume * 1.2
            
            # 檢查是否維持在開盤價之上（強勢特徵）
            current_open = df['Open'].iloc[-1]
            above_open = current_close > current_open
            
            return {
                'detected': detected,
                'current_close': round(current_close, 2),
                'breakout_level': round(max_prev_high, 2),
                'volume_confirmed': volume_expanded,
                'above_open': above_open,
                'strength': 'strong' if (detected and volume_expanded and above_open) else 
                           'moderate' if detected else 'none'
            }
        except:
            return {'detected': False}
    
    @staticmethod
    def _detect_three_bar_breakdown(df):
        """
        偵測三盤跌破（出場訊號）
        定義：當前K線收盤價 < 前兩根K線的最低價
        """
        try:
            if len(df) < 3:
                return {'detected': False}
            
            current_close = df['Close'].iloc[-1]
            prev_low_1 = df['Low'].iloc[-2]  # 前一日最低
            prev_low_2 = df['Low'].iloc[-3]  # 前兩日最低
            min_prev_low = min(prev_low_1, prev_low_2)
            
            detected = current_close < min_prev_low
            
            # 跌破幅度
            breakdown_pct = (min_prev_low - current_close) / min_prev_low * 100 if detected else 0
            
            return {
                'detected': detected,
                'current_close': round(current_close, 2),
                'breakdown_level': round(min_prev_low, 2),
                'breakdown_pct': round(breakdown_pct, 2),
                'severity': 'severe' if breakdown_pct > 3 else 'moderate' if breakdown_pct > 1 else 'mild'
            }
        except:
            return {'detected': False}
    
    @staticmethod
    def _analyze_key_volume_bar(df, lookback=20):
        """
        分析爆量K線
        爆量定義：成交量 > 20日均量 * 2
        爆量K線低點不可被收破
        """
        try:
            avg_volume = df['Volume'].rolling(window=20).mean()
            
            # 找出近期爆量K線
            volume_bars = []
            for i in range(-lookback, 0):
                if i >= -len(df):
                    vol = df['Volume'].iloc[i]
                    avg = avg_volume.iloc[i]
                    if vol > avg * QuantConfig.VOLUME_SPIKE_WAVE:
                        volume_bars.append({
                            'date': df.index[i].strftime('%Y-%m-%d') if hasattr(df.index[i], 'strftime') else str(df.index[i]),
                            'low': df['Low'].iloc[i],
                            'high': df['High'].iloc[i],
                            'volume_ratio': round(vol / avg, 2)
                        })
            
            if not volume_bars:
                return {
                    'has_key_bar': False,
                    'message': '近期無爆量K線'
                }
            
            # 取最近的爆量K線
            latest_volume_bar = volume_bars[-1]
            key_bar_low = latest_volume_bar['low']
            current_close = df['Close'].iloc[-1]
            
            # 檢查是否跌破爆量K線低點
            broken_key_bar = current_close < key_bar_low
            
            return {
                'has_key_bar': True,
                'key_bar_date': latest_volume_bar['date'],
                'key_bar_low': round(key_bar_low, 2),
                'key_bar_high': round(latest_volume_bar['high'], 2),
                'volume_ratio': latest_volume_bar['volume_ratio'],
                'broken': broken_key_bar,
                'warning': '⚠️ 已跌破爆量K線低點！' if broken_key_bar else '✓ 守住爆量K線低點',
                'all_volume_bars': volume_bars
            }
        except:
            return {'has_key_bar': False}
    
    @staticmethod
    def _check_volume_price_sync(df, ma55, lookback=5):
        """
        檢查量價共振
        量價共振 = 量、價、均線同時翻揚
        """
        try:
            recent = df.tail(lookback)
            
            # 價格趨勢
            price_up = recent['Close'].iloc[-1] > recent['Close'].iloc[0]
            
            # 均線趨勢
            recent_ma = ma55.tail(lookback)
            ma_up = recent_ma.iloc[-1] > recent_ma.iloc[0]
            
            # 成交量趨勢（比較近期均量與前期）
            recent_avg_vol = recent['Volume'].mean()
            prev_avg_vol = df['Volume'].iloc[-lookback*2:-lookback].mean()
            vol_up = recent_avg_vol > prev_avg_vol
            
            sync = price_up and ma_up and vol_up
            
            return {
                'sync': sync,
                'price_up': price_up,
                'ma_up': ma_up,
                'volume_up': vol_up,
                'description': '量價均線同步翻揚 ✓' if sync else 
                              '量價未完全共振' + 
                              ('' if price_up else '（價跌）') +
                              ('' if ma_up else '（均線下彎）') +
                              ('' if vol_up else '（量縮）')
            }
        except:
            return {'sync': False, 'description': '無法判斷'}
    
    @staticmethod
    def _determine_wave_status(is_bullish_env, ma55_slope, breakout, breakdown, 
                               volume_bar, volume_price_sync, current_price, ma55_value):
        """
        綜合判斷波段狀態、說明與建議
        """
        
        # 狀態判斷邏輯
        if breakdown.get('detected'):
            # 三盤跌破 - 最優先判斷
            if volume_bar.get('broken'):
                status = "🔴 波段結束（雙重跌破）"
                detail = f"三盤跌破 + 跌破爆量K線低點，波段確認結束"
                advice = "立即出場，波段已破壞"
            else:
                status = "🔴 波段結束"
                detail = f"收盤價 {breakdown['current_close']} 跌破前兩日低點 {breakdown['breakdown_level']}，三盤跌破確立"
                advice = "建議出場，等待下一個波段機會"
        
        elif not is_bullish_env:
            # 非多頭環境
            if current_price < ma55_value:
                status = "⚫ 空頭格局"
                detail = f"K線位於55MA下方（55MA={ma55_value}），均線{'下彎' if ma55_slope < 0 else '走平'}，不符合多頭篩選條件"
                advice = "不建議進場，等待站上55MA且均線翻揚"
            else:
                status = "🟡 均線糾結"
                detail = f"雖站上55MA，但均線{'下彎' if ma55_slope < 0 else '走平'}，趨勢不明確"
                advice = "觀望為主，等待均線翻揚確認"
        
        elif breakout.get('detected'):
            # 三盤突破
            if breakout.get('strength') == 'strong':
                status = "🟢 三盤突破（強勢）"
                detail = f"收盤價 {breakout['current_close']} 突破前兩日最高 {breakout['breakout_level']}，量增價揚，維持在開盤價上方"
                advice = "可考慮進場，或等拉回開盤價不破再介入"
            else:
                status = "🟢 三盤突破"
                detail = f"收盤價 {breakout['current_close']} 突破前兩日最高 {breakout['breakout_level']}"
                advice = "出現進場訊號，建議等縮量拉回確認後介入"
        
        elif is_bullish_env and volume_price_sync.get('sync'):
            # 多頭環境 + 量價共振
            status = "🟡 上漲波段中"
            detail = f"K線站穩55MA上方，量價均線同步翻揚，波段持續中"
            advice = "持股續抱，等待三盤突破加碼或三盤跌破出場"
        
        elif is_bullish_env:
            # 多頭環境但無明確訊號
            status = "🟡 多頭整理"
            detail = f"K線站穩55MA上方（距離{round((current_price/ma55_value-1)*100, 1)}%），均線上揚，但尚未出現三盤突破"
            advice = "觀望等待，留意三盤突破訊號"
        
        else:
            status = "⚪ 待觀察"
            detail = "目前無明確波段訊號"
            advice = "持續觀察，等待明確方向"
        
        # 補充爆量K線警告
        if volume_bar.get('broken') and '跌破' not in status:
            advice += "（⚠️ 注意：已跌破爆量K線低點）"
        
        return status, detail, advice


# ============================================================================
# v4.2 新增：均值回歸與乖離模組 (Mean Reversion & Bias Module)
# ============================================================================


# ============================================================================
# v4.2 新增：均值回歸與乖離模組 (Mean Reversion & Bias Module)
# ============================================================================

class MeanReversionAnalyzer:
    """
    均值回歸與乖離分析器 v4.2
    
    實現左側交易邏輯：
    1. 左側買進訊號：超跌反彈偵測（負乖離 + 超賣 + 止跌跡象）
    2. 左側賣出訊號：漲多預判拉回（正乖離 + 背離 + 高檔爆量不漲）
    3. 雙軌出場策略：趨勢出場 vs 積極停利
    """
    
    @staticmethod
    def analyze(df, current_price=None):
        """
        完整均值回歸分析
        
        Args:
            df: 包含 OHLCV 的 DataFrame（至少需要 60 天數據）
            current_price: 當前價格（選填，預設使用最後收盤價）
        
        Returns:
            dict: 均值回歸分析結果
        """
        try:
            if len(df) < 60:
                return {
                    'available': False,
                    'message': '數據不足，需要至少60天'
                }
            
            if current_price is None:
                current_price = df['Close'].iloc[-1]
            
            # 1. 計算乖離率
            bias_analysis = MeanReversionAnalyzer._calculate_bias(df, current_price)
            
            # 2. 左側買進訊號偵測
            left_buy_signal = MeanReversionAnalyzer._detect_left_buy_signal(df, bias_analysis)
            
            # 3. 左側賣出訊號偵測
            left_sell_signal = MeanReversionAnalyzer._detect_left_sell_signal(df, bias_analysis)
            
            # 4. 雙軌出場策略
            exit_strategy = MeanReversionAnalyzer._analyze_exit_strategy(df, bias_analysis, left_sell_signal)
            
            # 5. 綜合操作建議
            operation_advice = MeanReversionAnalyzer._generate_operation_advice(
                df, bias_analysis, left_buy_signal, left_sell_signal
            )
            
            return {
                'available': True,
                'bias_analysis': bias_analysis,
                'left_buy_signal': left_buy_signal,
                'left_sell_signal': left_sell_signal,
                'exit_strategy': exit_strategy,
                'operation_advice': operation_advice
            }
            
        except Exception as e:
            print(f"均值回歸分析錯誤: {e}")
            import traceback
            traceback.print_exc()
            return {
                'available': False,
                'message': f'分析錯誤: {str(e)}'
            }
    
    @staticmethod
    def _calculate_bias(df, current_price):
        """計算乖離率分析"""
        try:
            # 計算各均線
            ma5 = df['Close'].rolling(window=5).mean().iloc[-1]
            ma20 = df['Close'].rolling(window=20).mean().iloc[-1]
            ma60 = df['Close'].rolling(window=60).mean().iloc[-1]
            
            # 計算乖離率
            bias_5 = (current_price - ma5) / ma5 * 100
            bias_20 = (current_price - ma20) / ma20 * 100
            bias_60 = (current_price - ma60) / ma60 * 100
            
            # 判斷乖離狀態
            bias_20_status = "正常"
            bias_20_alert = False
            
            if bias_20 > QuantConfig.BIAS_OVERBOUGHT_THRESHOLD:
                bias_20_status = "嚴重正乖離（過熱）"
                bias_20_alert = True
            elif bias_20 > 10:
                bias_20_status = "正乖離偏高"
            elif bias_20 < QuantConfig.BIAS_OVERSOLD_THRESHOLD:
                bias_20_status = "嚴重負乖離（超跌）"
                bias_20_alert = True
            elif bias_20 < -5:
                bias_20_status = "負乖離偏大"
            
            bias_60_status = "正常"
            if bias_60 > 20:
                bias_60_status = "長期正乖離過大"
            elif bias_60 < -15:
                bias_60_status = "長期負乖離過大"
            
            return {
                'ma5': round(ma5, 2),
                'ma20': round(ma20, 2),
                'ma60': round(ma60, 2),
                'bias_5': round(bias_5, 2),
                'bias_20': round(bias_20, 2),
                'bias_60': round(bias_60, 2),
                'bias_20_status': bias_20_status,
                'bias_60_status': bias_60_status,
                'bias_20_alert': bias_20_alert,
                'is_overbought': bias_20 > QuantConfig.BIAS_OVERBOUGHT_THRESHOLD,
                'is_oversold': bias_20 < QuantConfig.BIAS_OVERSOLD_THRESHOLD
            }
        except Exception as e:
            print(f"乖離率計算錯誤: {e}")
            return {}
    
    @staticmethod
    def _detect_left_buy_signal(df, bias_analysis):
        """
        左側買進訊號偵測（超跌反彈）
        
        判斷邏輯 (AND 條件)：
        1. 負乖離過大：bias_20 < -10%
        2. 超賣指標：RSI < 25 或 觸及布林下軌
        3. 止跌跡象（選用）：長下影線 或 爆量
        """
        try:
            signal = {
                'triggered': False,
                'conditions_met': [],
                'conditions_failed': [],
                'strength': 'none',
                'message': ''
            }
            
            current_close = df['Close'].iloc[-1]
            current_low = df['Low'].iloc[-1]
            current_high = df['High'].iloc[-1]
            current_open = df['Open'].iloc[-1]
            current_volume = df['Volume'].iloc[-1]
            
            # 條件1：負乖離過大
            bias_20 = bias_analysis.get('bias_20', 0)
            cond1_met = bias_20 < QuantConfig.BIAS_OVERSOLD_THRESHOLD
            if cond1_met:
                signal['conditions_met'].append(f"負乖離 {bias_20:.1f}% < {QuantConfig.BIAS_OVERSOLD_THRESHOLD}%")
            else:
                signal['conditions_failed'].append(f"乖離率 {bias_20:.1f}% 未達超跌標準")
            
            # 條件2：RSI 超賣 或 觸及布林下軌
            rsi = MeanReversionAnalyzer._calculate_rsi(df, 14)
            ma20 = df['Close'].rolling(window=20).mean()
            std20 = df['Close'].rolling(window=20).std()
            lower_band = (ma20 - 2 * std20).iloc[-1]
            
            rsi_oversold = rsi < QuantConfig.RSI_OVERSOLD_LEVEL
            touch_lower_band = current_low <= lower_band
            cond2_met = rsi_oversold or touch_lower_band
            
            if rsi_oversold:
                signal['conditions_met'].append(f"RSI={rsi:.0f} < {QuantConfig.RSI_OVERSOLD_LEVEL}（超賣）")
            if touch_lower_band:
                signal['conditions_met'].append(f"觸及布林下軌 ${lower_band:.2f}")
            if not cond2_met:
                signal['conditions_failed'].append(f"RSI={rsi:.0f} 未達超賣，未觸及下軌")
            
            # 條件3：止跌跡象（加分項）
            body = abs(current_close - current_open)
            lower_shadow = min(current_open, current_close) - current_low
            vol_ma5 = df['Volume'].rolling(window=5).mean().iloc[-1]
            
            has_long_lower_shadow = lower_shadow > body * QuantConfig.LOWER_SHADOW_RATIO if body > 0 else False
            has_reversal_volume = current_volume > vol_ma5 * QuantConfig.VOLUME_REVERSAL_RATIO
            cond3_met = has_long_lower_shadow or has_reversal_volume
            
            if has_long_lower_shadow:
                signal['conditions_met'].append("出現長下影線（止跌跡象）")
            if has_reversal_volume:
                signal['conditions_met'].append(f"成交量放大至 {current_volume/vol_ma5:.1f}x 均量")
            
            # 綜合判斷
            if cond1_met and cond2_met:
                signal['triggered'] = True
                if cond3_met:
                    signal['strength'] = 'strong'
                    signal['message'] = "⚠️ 觸發左側買訊：股價嚴重負乖離，進入超賣區並出現止跌跡象，留意技術性反彈機會（風險較高，屬逆勢操作）。"
                else:
                    signal['strength'] = 'moderate'
                    signal['message'] = "⚠️ 觸發左側買訊：股價嚴重負乖離，進入超賣區，留意技術性反彈機會（風險較高，屬逆勢操作）。"
            
            signal['rsi'] = round(rsi, 1)
            signal['lower_band'] = round(lower_band, 2)
            
            return signal
            
        except Exception as e:
            print(f"左側買訊偵測錯誤: {e}")
            return {'triggered': False, 'message': '偵測錯誤'}
    
    @staticmethod
    def _detect_left_sell_signal(df, bias_analysis):
        """
        左側賣出訊號偵測（漲多預判拉回）
        
        判斷邏輯 (OR 條件)：
        1. 正乖離過大：bias_20 > 15%
        2. 指標背離：股價創 20 日新高，但 RSI 未創新高
        3. 高檔爆量不漲：成交量 > 5日均量 2 倍，但收黑或留長上影線
        """
        try:
            signal = {
                'triggered': False,
                'trigger_reasons': [],
                'strength': 'none',
                'message': ''
            }
            
            current_close = df['Close'].iloc[-1]
            current_open = df['Open'].iloc[-1]
            current_high = df['High'].iloc[-1]
            current_volume = df['Volume'].iloc[-1]
            
            trigger_count = 0
            
            # 條件1：正乖離過大
            bias_20 = bias_analysis.get('bias_20', 0)
            if bias_20 > QuantConfig.BIAS_OVERBOUGHT_THRESHOLD:
                trigger_count += 1
                signal['trigger_reasons'].append(f"正乖離 {bias_20:.1f}% > {QuantConfig.BIAS_OVERBOUGHT_THRESHOLD}%（過熱）")
            
            # 條件2：RSI 背離
            divergence = MeanReversionAnalyzer._detect_rsi_divergence(df)
            if divergence['bearish_divergence']:
                trigger_count += 1
                signal['trigger_reasons'].append(f"RSI 頂背離（價創新高但 RSI 下降）")
            
            # 條件3：高檔爆量不漲
            vol_ma5 = df['Volume'].rolling(window=5).mean().iloc[-1]
            body = abs(current_close - current_open)
            upper_shadow = current_high - max(current_close, current_open)
            
            high_volume = current_volume > vol_ma5 * QuantConfig.HIGH_VOL_NO_RISE_RATIO
            is_black_candle = current_close < current_open
            has_long_upper_shadow = upper_shadow > body * QuantConfig.UPPER_SHADOW_RATIO if body > 0 else False
            
            if high_volume and (is_black_candle or has_long_upper_shadow):
                trigger_count += 1
                if is_black_candle:
                    signal['trigger_reasons'].append(f"高檔爆量收黑（量比：{current_volume/vol_ma5:.1f}x）")
                else:
                    signal['trigger_reasons'].append(f"高檔爆量留長上影線（量比：{current_volume/vol_ma5:.1f}x）")
            
            # 綜合判斷（任一條件觸發即可）
            if trigger_count > 0:
                signal['triggered'] = True
                if trigger_count >= 2:
                    signal['strength'] = 'strong'
                    signal['message'] = "⚠️ 觸發左側賣訊：多項過熱指標同時出現，追高風險極高，強烈建議分批獲利了結。"
                else:
                    signal['strength'] = 'moderate'
                    signal['message'] = "⚠️ 觸發左側賣訊：短線正乖離過大或指標背離，追高風險較高，建議分批獲利了結。"
            
            signal['divergence'] = divergence
            signal['volume_ratio'] = round(current_volume / vol_ma5, 2) if vol_ma5 > 0 else 0
            
            return signal
            
        except Exception as e:
            print(f"左側賣訊偵測錯誤: {e}")
            return {'triggered': False, 'message': '偵測錯誤'}
    
    @staticmethod
    def _detect_rsi_divergence(df, lookback=20):
        """偵測 RSI 背離"""
        try:
            close = df['Close'].tail(lookback + 5)
            rsi_series = MeanReversionAnalyzer._calculate_rsi_series(df, 14).tail(lookback + 5)
            
            # 找最近的價格高點
            recent_high_idx = close.idxmax()
            recent_high_price = close[recent_high_idx]
            recent_high_rsi = rsi_series[recent_high_idx]
            
            # 找前一個相對高點
            earlier_data = close[close.index < recent_high_idx]
            if len(earlier_data) < 5:
                return {'bearish_divergence': False, 'bullish_divergence': False}
            
            prev_high_idx = earlier_data.idxmax()
            prev_high_price = earlier_data[prev_high_idx]
            prev_high_rsi = rsi_series[prev_high_idx]
            
            # 頂背離：價創新高但 RSI 未創新高
            bearish_divergence = (recent_high_price > prev_high_price) and (recent_high_rsi < prev_high_rsi)
            
            # 找價格低點（底背離）
            recent_low_idx = close.idxmin()
            recent_low_price = close[recent_low_idx]
            recent_low_rsi = rsi_series[recent_low_idx]
            
            earlier_low_data = close[close.index < recent_low_idx]
            if len(earlier_low_data) < 5:
                return {'bearish_divergence': bearish_divergence, 'bullish_divergence': False}
            
            prev_low_idx = earlier_low_data.idxmin()
            prev_low_price = earlier_low_data[prev_low_idx]
            prev_low_rsi = rsi_series[prev_low_idx]
            
            # 底背離：價創新低但 RSI 未創新低
            bullish_divergence = (recent_low_price < prev_low_price) and (recent_low_rsi > prev_low_rsi)
            
            return {
                'bearish_divergence': bearish_divergence,
                'bullish_divergence': bullish_divergence,
                'recent_high_rsi': round(recent_high_rsi, 1) if not pd.isna(recent_high_rsi) else None,
                'prev_high_rsi': round(prev_high_rsi, 1) if not pd.isna(prev_high_rsi) else None
            }
            
        except Exception as e:
            print(f"RSI 背離偵測錯誤: {e}")
            return {'bearish_divergence': False, 'bullish_divergence': False}
    
    @staticmethod
    def _calculate_rsi(df, period=14):
        """計算當前 RSI"""
        try:
            delta = df['Close'].diff()
            gain = delta.where(delta > 0, 0).rolling(window=period).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
            rs = gain / loss
            rsi = 100 - (100 / (1 + rs))
            return rsi.iloc[-1]
        except:
            return 50
    
    @staticmethod
    def _calculate_rsi_series(df, period=14):
        """計算 RSI 序列"""
        try:
            delta = df['Close'].diff()
            gain = delta.where(delta > 0, 0).rolling(window=period).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
            rs = gain / loss
            rsi = 100 - (100 / (1 + rs))
            return rsi
        except:
            return pd.Series([50] * len(df), index=df.index)
    
    @staticmethod
    def _analyze_exit_strategy(df, bias_analysis, left_sell_signal):
        """
        雙軌出場策略分析
        
        A. 趨勢賣點（防守型出場）：三盤跌破 或 跌破 20MA
        B. 積極停利（積極型停利）：左側賣訊 或 達到風險回報比
        """
        try:
            current_close = df['Close'].iloc[-1]
            ma20 = bias_analysis.get('ma20', current_close)
            ma60 = bias_analysis.get('ma60', current_close)
            
            # 趨勢賣點條件
            trend_exit = {
                'type': '🛡️ 防守型出場',
                'triggered': False,
                'conditions': [],
                'description': '適合波段持有者，吃完整個波段'
            }
            
            # 條件1：三盤跌破
            if len(df) >= 3:
                prev_low_1 = df['Low'].iloc[-2]
                prev_low_2 = df['Low'].iloc[-3]
                min_prev_low = min(prev_low_1, prev_low_2)
                three_bar_break = current_close < min_prev_low
                if three_bar_break:
                    trend_exit['triggered'] = True
                    trend_exit['conditions'].append(f"三盤跌破（收盤 {current_close:.2f} < 前兩日低點 {min_prev_low:.2f}）")
            
            # 條件2：跌破 20MA
            if current_close < ma20:
                trend_exit['triggered'] = True
                trend_exit['conditions'].append(f"跌破 20MA（{ma20:.2f}）")
            
            # 積極停利條件
            target_exit = {
                'type': '💰 積極型停利',
                'triggered': False,
                'conditions': [],
                'description': '適合短線/資金效率派，鎖住獲利避開修正'
            }
            
            # 條件1：左側賣訊觸發
            if left_sell_signal.get('triggered'):
                target_exit['triggered'] = True
                target_exit['conditions'].extend(left_sell_signal.get('trigger_reasons', []))
            
            # 條件2：乖離過大（即使沒有其他訊號）
            bias_20 = bias_analysis.get('bias_20', 0)
            if bias_20 > 12:  # 略低於正乖離閾值，提前警示
                target_exit['triggered'] = True
                target_exit['conditions'].append(f"乖離率偏高 {bias_20:.1f}%，獲利空間已大")
            
            # 建議出場價位
            exit_prices = {
                'trend_exit_level': round(ma20, 2),  # 趨勢出場參考 20MA
                'aggressive_exit_level': round(current_close * 0.97, 2),  # 積極停利設 -3%
                'trailing_stop': round(current_close * 0.95, 2)  # 追蹤停損 -5%
            }
            
            return {
                'trend_exit': trend_exit,
                'target_exit': target_exit,
                'exit_prices': exit_prices,
                'recommended': '積極型停利' if target_exit['triggered'] and not trend_exit['triggered'] else 
                              '防守型出場' if trend_exit['triggered'] else '持續持有'
            }
            
        except Exception as e:
            print(f"出場策略分析錯誤: {e}")
            return {}
    
    @staticmethod
    def _generate_operation_advice(df, bias_analysis, left_buy_signal, left_sell_signal):
        """
        生成綜合操作建議
        
        考慮趨勢方向與乖離狀態的組合
        """
        try:
            current_close = df['Close'].iloc[-1]
            ma20 = bias_analysis.get('ma20', current_close)
            ma60 = bias_analysis.get('ma60', current_close)
            bias_20 = bias_analysis.get('bias_20', 0)
            
            # 判斷趨勢方向
            trend_up = current_close > ma20 and ma20 > ma60
            trend_down = current_close < ma20 and ma20 < ma60
            
            # 生成操作建議
            advice = {
                'trend_status': '',
                'bias_status': '',
                'combined_advice': '',
                'risk_level': '',
                'suitable_action': ''
            }
            
            if trend_up:
                advice['trend_status'] = '多頭趨勢'
                if bias_20 > QuantConfig.BIAS_OVERBOUGHT_THRESHOLD:
                    advice['bias_status'] = '短線過熱'
                    advice['combined_advice'] = "多頭趨勢中，但短線嚴重過熱，切勿追高，建議積極停利或等待拉回再進場"
                    advice['risk_level'] = '追高風險極高'
                    advice['suitable_action'] = '持股者積極停利，空手者勿追'
                elif bias_20 > 10:
                    advice['bias_status'] = '乖離偏高'
                    advice['combined_advice'] = "多頭趨勢中，乖離偏高，不宜追高，可持股但設好停利"
                    advice['risk_level'] = '追高風險中等'
                    advice['suitable_action'] = '持股續抱但準備停利'
                else:
                    advice['bias_status'] = '乖離正常'
                    advice['combined_advice'] = "多頭趨勢中，乖離正常，可順勢操作"
                    advice['risk_level'] = '風險可控'
                    advice['suitable_action'] = '可進場或持續持有'
            
            elif trend_down:
                advice['trend_status'] = '空頭趨勢'
                if bias_20 < QuantConfig.BIAS_OVERSOLD_THRESHOLD:
                    advice['bias_status'] = '嚴重超跌'
                    advice['combined_advice'] = "空頭趨勢中，但負乖離過大，不建議殺低，空手者可嘗試搶短反彈（高風險）"
                    advice['risk_level'] = '殺低風險極高'
                    advice['suitable_action'] = '持股者停損觀望，激進者可搶反彈'
                elif bias_20 < -5:
                    advice['bias_status'] = '負乖離偏大'
                    advice['combined_advice'] = "空頭趨勢中，負乖離偏大，短線可能反彈但趨勢未變"
                    advice['risk_level'] = '反彈操作風險高'
                    advice['suitable_action'] = '建議觀望或小量試單'
                else:
                    advice['bias_status'] = '乖離正常'
                    advice['combined_advice'] = "空頭趨勢中，乖離正常，不宜抄底"
                    advice['risk_level'] = '下跌風險持續'
                    advice['suitable_action'] = '建議空手觀望'
            
            else:
                advice['trend_status'] = '盤整格局'
                if left_buy_signal.get('triggered'):
                    advice['combined_advice'] = "盤整中出現超跌訊號，可能有反彈機會"
                    advice['suitable_action'] = '可小量佈局反彈'
                elif left_sell_signal.get('triggered'):
                    advice['combined_advice'] = "盤整中出現過熱訊號，短線可能回檔"
                    advice['suitable_action'] = '建議減碼或觀望'
                else:
                    advice['combined_advice'] = "盤整格局，等待方向明確"
                    advice['suitable_action'] = '觀望為主'
                advice['bias_status'] = '正常'
                advice['risk_level'] = '方向不明'
            
            return advice
            
        except Exception as e:
            print(f"操作建議生成錯誤: {e}")
            return {}


# ============================================================================
# v4.0 改進：增強版資料庫管理（含籌碼緩存）
# ============================================================================


# ============================================================================
# v4.0 新增：市場環境分析器
# ============================================================================

class MarketRegimeAnalyzer:
    """市場環境分析器 - 判斷趨勢盤/震盪盤"""
    
    @staticmethod
    def calculate_adx(df, period=14):
        """計算 ADX 指標（Average Directional Index）"""
        try:
            high = df['High']
            low = df['Low']
            close = df['Close']
            
            # 計算 +DM 和 -DM
            plus_dm = high.diff()
            minus_dm = low.diff().abs() * -1
            
            plus_dm[plus_dm < 0] = 0
            minus_dm[minus_dm > 0] = 0
            minus_dm = minus_dm.abs()
            
            # 當 +DM < -DM 時，+DM = 0
            plus_dm[(plus_dm < minus_dm)] = 0
            minus_dm[(minus_dm < plus_dm)] = 0
            
            # 計算 True Range
            tr1 = high - low
            tr2 = abs(high - close.shift(1))
            tr3 = abs(low - close.shift(1))
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            
            # 計算平滑移動平均
            atr = tr.rolling(window=period).mean()
            plus_di = 100 * (plus_dm.rolling(window=period).mean() / atr)
            minus_di = 100 * (minus_dm.rolling(window=period).mean() / atr)
            
            # 計算 DX 和 ADX
            dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
            adx = dx.rolling(window=period).mean()
            
            return adx, plus_di, minus_di
        except Exception as e:
            print(f"ADX 計算錯誤: {e}")
            return pd.Series([np.nan] * len(df)), pd.Series([np.nan] * len(df)), pd.Series([np.nan] * len(df))
    
    @staticmethod
    def get_market_regime(market="台股"):
        """取得市場環境判斷"""
        try:
            # 選擇對應的大盤指數
            if market == "台股":
                index_symbol = QuantConfig.MARKET_INDEX_TW
            else:
                index_symbol = QuantConfig.MARKET_INDEX_US
            
            # 抓取大盤數據（6個月）
            index_data = yf.Ticker(index_symbol)
            hist = index_data.history(period="6mo")
            
            if hist.empty or len(hist) < 30:
                return {
                    'available': False,
                    'message': '無法取得大盤數據'
                }
            
            # 計算 ADX
            adx, plus_di, minus_di = MarketRegimeAnalyzer.calculate_adx(hist)
            current_adx = adx.iloc[-1] if not pd.isna(adx.iloc[-1]) else 20
            
            # 計算大盤趨勢
            ma20 = hist['Close'].rolling(window=20).mean()
            ma60 = hist['Close'].rolling(window=60).mean()
            current_price = hist['Close'].iloc[-1]
            
            # 判斷市場環境
            if current_adx > QuantConfig.ADX_STRONG_TREND:
                regime = "強趨勢盤"
                regime_score = 1.0
            elif current_adx > QuantConfig.ADX_TREND_THRESHOLD:
                regime = "趨勢盤"
                regime_score = 0.7
            else:
                regime = "震盪盤"
                regime_score = 0.3
            
            # v4.4.3 新增：布林通道帶寬計算 (Bollinger Bandwidth)
            # 帶寬 = (上軌 - 下軌) / 中軌 * 100
            bb_window = 20
            bb_std = 2
            bb_middle = hist['Close'].rolling(bb_window).mean()
            bb_std_val = hist['Close'].rolling(bb_window).std()
            bb_upper = bb_middle + (bb_std_val * bb_std)
            bb_lower = bb_middle - (bb_std_val * bb_std)
            bb_bandwidth = ((bb_upper - bb_lower) / bb_middle * 100).fillna(0)
            
            current_bandwidth = bb_bandwidth.iloc[-1] if len(bb_bandwidth) > 0 else 0
            
            # 計算帶寬的歷史百分位
            lookback = min(len(bb_bandwidth), QuantConfig.BB_SQUEEZE_LOOKBACK)
            if lookback > bb_window:
                bandwidth_percentile = (bb_bandwidth.iloc[-lookback:] < current_bandwidth).mean() * 100
            else:
                bandwidth_percentile = 50.0
            
            # 波動率壓縮判斷 (Volatility Squeeze)
            is_squeeze = bandwidth_percentile < QuantConfig.BB_SQUEEZE_PERCENTILE
            squeeze_warning = ""
            if is_squeeze:
                squeeze_warning = "⚠️ 波動率壓縮極致，即將變盤 (Volatility Squeeze - Potential Breakout Imminent)"
            
            # 判斷趨勢方向
            if current_price > ma20.iloc[-1] and ma20.iloc[-1] > ma60.iloc[-1]:
                trend_direction = "多頭"
                trend_score = 1.0
            elif current_price < ma20.iloc[-1] and ma20.iloc[-1] < ma60.iloc[-1]:
                trend_direction = "空頭"
                trend_score = -1.0
            else:
                trend_direction = "盤整"
                trend_score = 0.0
            
            # 計算大盤近期漲跌幅
            pct_change_5d = (hist['Close'].iloc[-1] / hist['Close'].iloc[-6] - 1) * 100
            pct_change_20d = (hist['Close'].iloc[-1] / hist['Close'].iloc[-21] - 1) * 100
            
            return {
                'available': True,
                'regime': regime,
                'regime_score': regime_score,
                'adx': round(current_adx, 2),
                'trend_direction': trend_direction,
                'trend_score': trend_score,
                'market_price': round(current_price, 2),
                'ma20': round(ma20.iloc[-1], 2),
                'ma60': round(ma60.iloc[-1], 2) if not pd.isna(ma60.iloc[-1]) else None,
                'pct_change_5d': round(pct_change_5d, 2),
                'pct_change_20d': round(pct_change_20d, 2),
                # v4.4.3 新增：布林帶寬資訊
                'bb_bandwidth': round(current_bandwidth, 2),
                'bb_bandwidth_percentile': round(bandwidth_percentile, 1),
                'is_squeeze': is_squeeze,
                'squeeze_warning': squeeze_warning,
                'strategy_adjustment': MarketRegimeAnalyzer._get_strategy_adjustment(regime, trend_direction)
            }
            
        except Exception as e:
            print(f"市場環境分析錯誤: {e}")
            return {
                'available': False,
                'message': f'分析錯誤: {str(e)}'
            }
    
    @staticmethod
    def _get_strategy_adjustment(regime, trend_direction):
        """根據市場環境給出策略調整建議"""
        adjustments = {
            '趨勢策略': {'weight': 1.0, 'recommendation': ''},
            '動能策略': {'weight': 1.0, 'recommendation': ''},
            '通道策略': {'weight': 1.0, 'recommendation': ''},
            '均值回歸策略': {'weight': 1.0, 'recommendation': ''}
        }
        
        if regime in ["強趨勢盤", "趨勢盤"]:
            # 趨勢盤：趨勢策略加分，均值回歸減分
            adjustments['趨勢策略'] = {'weight': 1.3, 'recommendation': '適合當前趨勢盤'}
            adjustments['動能策略'] = {'weight': 1.1, 'recommendation': '趨勢盤中動能較明確'}
            adjustments['均值回歸策略'] = {'weight': 0.6, 'recommendation': '⚠️ 趨勢盤中慎用均值回歸'}
            adjustments['通道策略'] = {'weight': 0.8, 'recommendation': '可能被趨勢突破'}
        else:
            # 震盪盤：均值回歸和通道策略加分，趨勢策略減分
            adjustments['趨勢策略'] = {'weight': 0.6, 'recommendation': '⚠️ 震盪盤中假訊號多'}
            adjustments['動能策略'] = {'weight': 0.8, 'recommendation': '震盪盤中動能較弱'}
            adjustments['均值回歸策略'] = {'weight': 1.3, 'recommendation': '適合當前震盪盤'}
            adjustments['通道策略'] = {'weight': 1.2, 'recommendation': '適合區間操作'}
        
        # 根據趨勢方向調整
        if trend_direction == "空頭":
            for strategy in adjustments:
                if "買進" in adjustments[strategy].get('recommendation', ''):
                    adjustments[strategy]['weight'] *= 0.8
                adjustments[strategy]['recommendation'] += " (大盤偏空，買入訊號下修)"
        
        return adjustments
    
    @staticmethod
    def get_market_regime_historical(market="台股", analysis_date=None):
        """v4.3 新增：取得歷史日期的市場環境判斷"""
        try:
            if market == "台股":
                index_symbol = QuantConfig.MARKET_INDEX_TW
            else:
                index_symbol = QuantConfig.MARKET_INDEX_US
            
            # 計算日期範圍
            end_date = analysis_date
            start_date = end_date - datetime.timedelta(days=200)
            
            # 抓取歷史大盤數據
            index_data = yf.Ticker(index_symbol)
            hist = index_data.history(start=start_date.strftime('%Y-%m-%d'),
                                     end=(end_date + datetime.timedelta(days=1)).strftime('%Y-%m-%d'))
            
            # 截取到分析日期（使用日期比較避免時區問題）
            target_date = analysis_date.date()
            hist = hist[hist.index.date <= target_date]
            
            if hist.empty or len(hist) < 30:
                return {
                    'available': False,
                    'message': '無法取得歷史大盤數據'
                }
            
            # 計算 ADX
            adx, plus_di, minus_di = MarketRegimeAnalyzer.calculate_adx(hist)
            current_adx = adx.iloc[-1] if not pd.isna(adx.iloc[-1]) else 20
            
            # 計算趨勢
            ma20 = hist['Close'].rolling(window=20).mean()
            ma60 = hist['Close'].rolling(window=60).mean()
            current_price = hist['Close'].iloc[-1]
            
            # 判斷市場環境
            if current_adx > QuantConfig.ADX_STRONG_TREND:
                regime = "強趨勢盤"
            elif current_adx > QuantConfig.ADX_TREND_THRESHOLD:
                regime = "趨勢盤"
            else:
                regime = "震盪盤"
            
            # 判斷趨勢方向
            if current_price > ma20.iloc[-1] and ma20.iloc[-1] > ma60.iloc[-1]:
                trend_direction = "多頭"
            elif current_price < ma20.iloc[-1] and ma20.iloc[-1] < ma60.iloc[-1]:
                trend_direction = "空頭"
            else:
                trend_direction = "盤整"
            
            # 計算漲跌幅
            pct_change_5d = (hist['Close'].iloc[-1] / hist['Close'].iloc[-6] - 1) * 100 if len(hist) >= 6 else 0
            pct_change_20d = (hist['Close'].iloc[-1] / hist['Close'].iloc[-21] - 1) * 100 if len(hist) >= 21 else 0
            
            return {
                'available': True,
                'regime': regime,
                'adx': round(current_adx, 1),
                'trend_direction': trend_direction,
                'market_price': round(current_price, 2),
                'ma20': round(ma20.iloc[-1], 2),
                'pct_change_5d': round(pct_change_5d, 2),
                'pct_change_20d': round(pct_change_20d, 2),
                'is_historical': True,
                'analysis_date': analysis_date.strftime('%Y-%m-%d'),
                'strategy_adjustment': MarketRegimeAnalyzer._get_strategy_adjustment(regime, trend_direction)
            }
            
        except Exception as e:
            print(f"歷史市場環境分析錯誤: {e}")
            return {
                'available': False,
                'message': f'分析錯誤: {str(e)}'
            }


# ============================================================================
# v4.0 改進：增強版回測引擎
# ============================================================================


# ============================================================================
# v4.0 新增：相關性分析器
# ============================================================================

class CorrelationAnalyzer:
    """自選股相關性分析器"""
    
    @staticmethod
    def calculate_correlation_matrix(symbols, market="台股", period="1y"):
        """計算自選股之間的相關性矩陣"""
        try:
            price_data = {}
            
            for symbol in symbols:
                if market == "台股":
                    ticker_symbol = f"{symbol}.TW"
                else:
                    ticker_symbol = symbol
                
                stock = yf.Ticker(ticker_symbol)
                hist = stock.history(period=period)
                
                if not hist.empty:
                    price_data[symbol] = hist['Close']
            
            if len(price_data) < 2:
                return None, "至少需要2檔股票才能計算相關性"
            
            # 建立 DataFrame
            df = pd.DataFrame(price_data)
            
            # 計算日報酬率
            returns = df.pct_change(fill_method=None).dropna()
            
            # 計算相關性矩陣
            corr_matrix = returns.corr()
            
            # 分析結果
            analysis = CorrelationAnalyzer._analyze_correlation(corr_matrix)
            
            return {
                'matrix': corr_matrix,
                'analysis': analysis,
                'returns': returns
            }, None
            
        except Exception as e:
            return None, f"相關性計算錯誤: {str(e)}"
    
    @staticmethod
    def _analyze_correlation(corr_matrix):
        """分析相關性矩陣"""
        symbols = corr_matrix.columns.tolist()
        n = len(symbols)
        
        high_corr_pairs = []
        low_corr_pairs = []
        
        for i in range(n):
            for j in range(i+1, n):
                corr = corr_matrix.iloc[i, j]
                pair = (symbols[i], symbols[j], corr)
                
                if abs(corr) > 0.7:
                    high_corr_pairs.append(pair)
                elif abs(corr) < 0.3:
                    low_corr_pairs.append(pair)
        
        # 計算平均相關性
        upper_triangle = corr_matrix.values[np.triu_indices(n, k=1)]
        avg_corr = np.mean(upper_triangle)
        
        # 分散化程度評估
        if avg_corr > 0.7:
            diversification = "差（高度相關）"
            diversification_advice = "⚠️ 您的持股高度相關，風險集中，建議增加不同產業的股票"
        elif avg_corr > 0.5:
            diversification = "中等"
            diversification_advice = "持股有一定相關性，可考慮增加負相關或低相關的股票"
        else:
            diversification = "良好"
            diversification_advice = "✓ 持股分散程度良好，風險分散效果佳"
        
        return {
            'high_corr_pairs': high_corr_pairs,
            'low_corr_pairs': low_corr_pairs,
            'avg_correlation': round(avg_corr, 3),
            'diversification': diversification,
            'diversification_advice': diversification_advice
        }


# ============================================================================
# 技術指標計算函數
# ============================================================================

def calculate_sma(data, window=5):
    return data.rolling(window).mean()

def calculate_bollinger_bands(data, window=20, num_std=2):
    sma = data.rolling(window).mean()
    std = data.rolling(window).std()
    upper_band = sma + (num_std * std)
    lower_band = sma - (num_std * std)
    return sma, upper_band, lower_band

def calculate_macd(data, short=12, long=26, signal=9):
    ema_short = data.ewm(span=short, adjust=False).mean()
    ema_long = data.ewm(span=long, adjust=False).mean()
    macd_line = ema_short - ema_long
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist

def calculate_rsi(data, period=14):
    delta = data.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def calculate_kd(data, n=9, k_period=3, d_period=3):
    low_min = data['Low'].rolling(n).min()
    high_max = data['High'].rolling(n).max()
    rsv = (data['Close'] - low_min) / (high_max - low_min) * 100
    k = rsv.ewm(alpha=1/k_period, adjust=False).mean()
    d = k.ewm(alpha=1/d_period, adjust=False).mean()
    return k, d

def analyze_volume_price_relation(df):
    """量價分析"""
    if len(df) < 2:
        return "資料不足"
    
    today = df.iloc[-1]
    yesterday = df.iloc[-2]
    
    price_relation = "價平"
    volume_relation = "量平"
    
    if today['Close'] > yesterday['Close']:
        price_relation = "價漲"
    elif today['Close'] < yesterday['Close']:
        price_relation = "價跌"
    
    if today['Volume'] > yesterday['Volume']:
        volume_relation = "量增"
    elif today['Volume'] < yesterday['Volume']:
        volume_relation = "量縮"
    
    return f"{volume_relation}{price_relation}"


# ============================================================================
# v4.4 新增：量價分析情境庫 (Volume-Price Analyzer)
# ============================================================================

class VolumePriceAnalyzer:
    """
    量價分析情境庫 v4.4
    
    涵蓋 12 種核心量價情境，每個情境包含：
    - 明確判斷條件（使用近 N 日資料，避免 lookahead bias）
    - 決策建議（可執行的 BUY/SELL/HOLD/WAIT + 條件式）
    - 風險提示
    """
    
    # 情境代碼
    TREND_CONFIRM_UP = "VP01"       # 價漲量增
    WEAK_UP = "VP02"                # 價漲量縮
    BEAR_CONFIRM = "VP03"           # 價跌量增
    SELLING_EASING = "VP04"         # 價跌量縮
    VALID_BREAKOUT = "VP05"         # 帶量突破
    SUSPECT_BREAKOUT = "VP06"       # 無量突破
    SUPPLY_OVERHANG = "VP07"        # 放量不漲
    BREAKDOWN_CONFIRM = "VP08"      # 放量跌破
    ACCUMULATION = "VP09"           # 吸籌跡象
    DISTRIBUTION = "VP10"           # 出貨跡象
    GAP_UP_VOLUME = "VP11"          # 跳空上漲帶量
    GAP_DOWN_VOLUME = "VP12"        # 跳空下跌帶量
    
    @staticmethod
    def analyze(df, current_price=None):
        """
        執行完整量價分析
        
        Args:
            df: DataFrame，包含 OHLCV 資料
            current_price: 當前價格（可選）
        
        Returns:
            dict: 量價分析結果
        """
        if not QuantConfig.ENABLE_VOLUME_PRICE_ANALYSIS:
            return {'available': False, 'reason': '量價分析已停用'}
        
        if df is None or len(df) < 20:
            return {'available': False, 'reason': '資料不足（需至少20天）'}
        
        try:
            signals = []
            
            # 計算基礎指標
            df = df.copy()
            df['MA20_Vol'] = df['Volume'].rolling(20).mean()
            df['MA5_Vol'] = df['Volume'].rolling(5).mean()
            df['Vol_Ratio'] = df['Volume'] / df['MA20_Vol']
            
            # v4.4.3 新增：Z-Score 動態成交量閾值
            # 使用 Z-Score 可以自動適應不同股票的成交量特性
            vol_window = min(len(df), QuantConfig.VOLUME_ZSCORE_WINDOW)
            df['Vol_Mean'] = df['Volume'].rolling(vol_window).mean()
            df['Vol_Std'] = df['Volume'].rolling(vol_window).std()
            df['Vol_ZScore'] = (df['Volume'] - df['Vol_Mean']) / df['Vol_Std']
            df['Vol_ZScore'] = df['Vol_ZScore'].fillna(0)
            
            # 價格變化
            df['Price_Change'] = df['Close'].pct_change()
            df['Price_Change_3D'] = df['Close'].pct_change(3)
            
            # 近期高低點
            df['High_20'] = df['High'].rolling(20).max()
            df['Low_20'] = df['Low'].rolling(20).min()
            
            # 取最近資料
            today = df.iloc[-1]
            yesterday = df.iloc[-2] if len(df) > 1 else today
            recent_5d = df.iloc[-5:] if len(df) >= 5 else df
            recent_3d = df.iloc[-3:] if len(df) >= 3 else df
            
            vol_ratio = today['Vol_Ratio'] if pd.notna(today['Vol_Ratio']) else 1.0
            vol_zscore = today['Vol_ZScore'] if pd.notna(today['Vol_ZScore']) else 0.0
            price_change = today['Price_Change'] if pd.notna(today['Price_Change']) else 0
            
            # v4.4.3 新增：使用 Z-Score 判斷爆量（更適合大型權值股）
            is_volume_spike = vol_zscore > QuantConfig.VOLUME_ZSCORE_SPIKE
            is_heavy_volume = vol_zscore > QuantConfig.VOLUME_ZSCORE_HEAVY
            
            # 判斷各情境
            
            # VP01: 價漲量增（趨勢確認）
            if (recent_3d['Close'].iloc[-1] > recent_3d['Close'].iloc[0] and 
                vol_ratio > QuantConfig.VP_VOLUME_UP_THRESHOLD):
                signals.append({
                    'code': VolumePriceAnalyzer.TREND_CONFIRM_UP,
                    'name': '價漲量增（趨勢確認）',
                    'severity': 3,
                    'direction': 'bullish',
                    'evidence': f"近3日上漲，量比={vol_ratio:.2f}x",
                    'decision_hint': 'BUY｜回踩不破關鍵均線可加碼',
                    'risk_notes': '停損設於近期支撐或 ATR 止損位'
                })
            
            # VP02: 價漲量縮（弱勢上漲）
            elif (price_change > 0 and vol_ratio < QuantConfig.VP_VOLUME_DOWN_THRESHOLD):
                signals.append({
                    'code': VolumePriceAnalyzer.WEAK_UP,
                    'name': '價漲量縮（弱勢上漲）',
                    'severity': 2,
                    'direction': 'neutral',
                    'evidence': f"價格上漲但量縮，量比={vol_ratio:.2f}x",
                    'decision_hint': 'HOLD｜不追價，等量能補上或回測',
                    'risk_notes': '接近壓力位時偏減碼觀望'
                })
            
            # VP03: 價跌量增（空頭確認）
            if (price_change < -0.01 and vol_ratio > QuantConfig.VP_VOLUME_UP_THRESHOLD):
                signals.append({
                    'code': VolumePriceAnalyzer.BEAR_CONFIRM,
                    'name': '價跌量增（空頭確認）',
                    'severity': 4,
                    'direction': 'bearish',
                    'evidence': f"下跌放量，量比={vol_ratio:.2f}x",
                    'decision_hint': 'SELL｜風控優先，停損或降曝險',
                    'risk_notes': '跌深止跌型態出現前避免抄底'
                })
            
            # VP04: 價跌量縮（賣壓減緩）
            elif (price_change < 0 and vol_ratio < QuantConfig.VP_VOLUME_DOWN_THRESHOLD):
                signals.append({
                    'code': VolumePriceAnalyzer.SELLING_EASING,
                    'name': '價跌量縮（賣壓減緩）',
                    'severity': 2,
                    'direction': 'neutral',
                    'evidence': f"下跌但量縮，量比={vol_ratio:.2f}x",
                    'decision_hint': 'WAIT｜可能止跌前兆，等確認訊號',
                    'risk_notes': '等待轉折K棒或量能回升再進場'
                })
            
            # VP05: 帶量突破
            if (today['Close'] > yesterday['High_20'] and 
                vol_ratio > QuantConfig.VP_BREAKOUT_VOLUME_THRESHOLD):
                signals.append({
                    'code': VolumePriceAnalyzer.VALID_BREAKOUT,
                    'name': '帶量突破區間',
                    'severity': 4,
                    'direction': 'bullish',
                    'evidence': f"突破20日高點，量比={vol_ratio:.2f}x",
                    'decision_hint': 'BUY｜突破回踩不破為進場條件',
                    'risk_notes': '停損設於突破位下方或 ATR'
                })
            
            # VP06: 無量突破（疑似假突破）
            elif (today['Close'] > yesterday['High_20'] and 
                  vol_ratio <= 1.1):
                signals.append({
                    'code': VolumePriceAnalyzer.SUSPECT_BREAKOUT,
                    'name': '無量突破（疑似假突破）',
                    'severity': 3,
                    'direction': 'neutral',
                    'evidence': f"突破但量未放大，量比={vol_ratio:.2f}x",
                    'decision_hint': 'WAIT｜直到回踩守住+量增才確認',
                    'risk_notes': '假突破機率高，避免追高'
                })
            
            # VP07: 放量不漲（派發）
            body = abs(today['Close'] - today['Open'])
            upper_shadow = today['High'] - max(today['Close'], today['Open'])
            if (vol_ratio > QuantConfig.VP_LARGE_VOLUME_THRESHOLD and 
                upper_shadow > body * 1.5 and 
                price_change < 0.01):
                signals.append({
                    'code': VolumePriceAnalyzer.SUPPLY_OVERHANG,
                    'name': '放量不漲（派發跡象）',
                    'severity': 4,
                    'direction': 'bearish',
                    'evidence': f"爆量但上影長、收盤不創高，量比={vol_ratio:.2f}x",
                    'decision_hint': 'SELL｜高位更需警惕，考慮減碼',
                    'risk_notes': '主力可能出貨，避免追高'
                })
            
            # VP08: 放量跌破
            if (today['Close'] < yesterday['Low_20'] and 
                vol_ratio > QuantConfig.VP_BREAKOUT_VOLUME_THRESHOLD):
                signals.append({
                    'code': VolumePriceAnalyzer.BREAKDOWN_CONFIRM,
                    'name': '放量跌破支撐',
                    'severity': 5,
                    'direction': 'bearish',
                    'evidence': f"跌破20日低點，量比={vol_ratio:.2f}x",
                    'decision_hint': 'SELL｜持倉停損，不接刀',
                    'risk_notes': '等重新站回關鍵位再考慮進場'
                })
            
            # VP09: 吸籌跡象（底部量能抬升）
            if len(df) >= 10:
                recent_10d = df.iloc[-10:]
                low_area = today['Close'] < df['Close'].rolling(60).mean().iloc[-1] if len(df) >= 60 else False
                vol_rising = recent_10d['Volume'].iloc[-3:].mean() > recent_10d['Volume'].iloc[:3].mean()
                price_stable = recent_10d['Low'].min() >= recent_10d['Low'].iloc[0] * 0.97
                
                if low_area and vol_rising and price_stable:
                    signals.append({
                        'code': VolumePriceAnalyzer.ACCUMULATION,
                        'name': '吸籌跡象',
                        'severity': 3,
                        'direction': 'bullish',
                        'evidence': "低位區，量能逐步抬升，低點不破",
                        'decision_hint': 'BUY｜分批觀察型布局，等確認突破',
                        'risk_notes': '需搭配趨勢翻揚或突破確認'
                    })
            
            # VP10: 出貨跡象（高位震盪放量）
            if len(df) >= 20:
                high_area = today['Close'] > df['Close'].rolling(60).mean().iloc[-1] * 1.1 if len(df) >= 60 else False
                recent_vol_high = recent_5d['Vol_Ratio'].mean() > 1.5
                price_stagnant = recent_5d['Close'].max() / recent_5d['Close'].min() < 1.03
                
                if high_area and recent_vol_high and price_stagnant:
                    signals.append({
                        'code': VolumePriceAnalyzer.DISTRIBUTION,
                        'name': '出貨跡象（高位震盪放量）',
                        'severity': 4,
                        'direction': 'bearish',
                        'evidence': "高位橫盤但量偏大，漲不上去",
                        'decision_hint': 'SELL｜逢高減碼，等待方向選擇',
                        'risk_notes': '主力可能派發，保守操作'
                    })
            
            # VP11: 跳空上漲帶量
            gap_up = today['Low'] > yesterday['High']
            if gap_up and vol_ratio > QuantConfig.VP_BREAKOUT_VOLUME_THRESHOLD:
                signals.append({
                    'code': VolumePriceAnalyzer.GAP_UP_VOLUME,
                    'name': '跳空上漲帶量',
                    'severity': 3,
                    'direction': 'bullish',
                    'evidence': f"跳空缺口，量比={vol_ratio:.2f}x",
                    'decision_hint': 'BUY｜追隨但縮倉，等回踩缺口不補再加',
                    'risk_notes': '缺口可能回補，分段操作'
                })
            
            # VP12: 跳空下跌帶量
            gap_down = today['High'] < yesterday['Low']
            if gap_down and vol_ratio > QuantConfig.VP_BREAKOUT_VOLUME_THRESHOLD:
                signals.append({
                    'code': VolumePriceAnalyzer.GAP_DOWN_VOLUME,
                    'name': '跳空下跌帶量',
                    'severity': 5,
                    'direction': 'bearish',
                    'evidence': f"向下跳空缺口，量比={vol_ratio:.2f}x",
                    'decision_hint': 'SELL｜最高風險，撤退停損',
                    'risk_notes': '等待止跌訊號再談進場'
                })
            
            # 計算量價綜合分數
            vp_score = VolumePriceAnalyzer._calculate_score(signals)
            
            # 生成摘要
            summary = VolumePriceAnalyzer._generate_summary(signals)
            
            # 決策提示
            decision_hint = VolumePriceAnalyzer._get_decision_hint(signals)
            
            # 風險提示
            risk_notes = VolumePriceAnalyzer._get_risk_notes(signals)
            
            return {
                'available': True,
                'signals': signals,
                'signal_count': len(signals),
                'vp_score': vp_score,
                'summary': summary,
                'decision_hint': decision_hint,
                'risk_notes': risk_notes,
                'vol_ratio': vol_ratio,
                'vol_zscore': round(vol_zscore, 2),  # v4.4.3 新增
                'is_volume_spike': is_volume_spike,   # v4.4.3 新增
                'is_heavy_volume': is_heavy_volume,   # v4.4.3 新增
                'price_change_pct': price_change * 100 if pd.notna(price_change) else 0
            }
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {'available': False, 'reason': f'分析錯誤: {str(e)}'}
    
    @staticmethod
    def _calculate_score(signals):
        """計算量價綜合分數（-100 到 +100）"""
        if not signals:
            return 0
        
        score = 0
        for sig in signals:
            severity = sig.get('severity', 3)
            direction = sig.get('direction', 'neutral')
            
            if direction == 'bullish':
                score += severity * 10
            elif direction == 'bearish':
                score -= severity * 10
        
        return max(-100, min(100, score))
    
    @staticmethod
    def _generate_summary(signals):
        """生成量價分析摘要"""
        if not signals:
            return "量價關係正常，無明顯異常訊號"
        
        bullish = [s for s in signals if s['direction'] == 'bullish']
        bearish = [s for s in signals if s['direction'] == 'bearish']
        
        if len(bearish) > len(bullish):
            return f"量價偏空，發現 {len(bearish)} 個空方訊號：" + "、".join([s['name'] for s in bearish[:2]])
        elif len(bullish) > len(bearish):
            return f"量價偏多，發現 {len(bullish)} 個多方訊號：" + "、".join([s['name'] for s in bullish[:2]])
        else:
            return f"量價中性，多空訊號均衡"
    
    @staticmethod
    def _get_decision_hint(signals):
        """獲取決策提示"""
        if not signals:
            return "HOLD｜維持現狀，等待明確訊號"
        
        # 找出最高嚴重度的訊號
        max_severity = max(s['severity'] for s in signals)
        critical_signals = [s for s in signals if s['severity'] == max_severity]
        
        if critical_signals:
            return critical_signals[0]['decision_hint']
        return "HOLD｜維持現狀"
    
    @staticmethod
    def _get_risk_notes(signals):
        """獲取風險提示"""
        if not signals:
            return "無特殊風險提示"
        
        # 收集所有高風險訊號的提示
        high_risk = [s for s in signals if s['severity'] >= 4]
        if high_risk:
            return "⚠️ " + "；".join([s['risk_notes'] for s in high_risk[:2]])
        return signals[0]['risk_notes'] if signals else "無特殊風險提示"


# ============================================================================
# v4.4 新增：風險管理模組 (Risk Manager)
# ============================================================================

class RiskManager:
    """
    風險管理模組 v4.4
    
    功能：
    - 止損/停利計算（結構位 + ATR 雙軌）
    - 風險回報比計算
    - 倉位建議（固定風險法）
    - 流動性評估
    - 跳空風險標記
    """
    
    @staticmethod
    def analyze(df, entry_price=None, capital=1000000):
        """
        執行風險管理分析
        
        Args:
            df: DataFrame，包含 OHLCV 資料
            entry_price: 進場價格（預設為當前價）
            capital: 可用資金
        
        Returns:
            dict: 風險管理分析結果
        """
        if not QuantConfig.ENABLE_RISK_MANAGER:
            return {'available': False, 'reason': '風險管理模組已停用'}
        
        if df is None or len(df) < QuantConfig.SWING_LOOKBACK:
            return {'available': False, 'reason': '資料不足'}
        
        try:
            df = df.copy()
            current_price = entry_price or df['Close'].iloc[-1]
            
            # 計算 ATR
            atr = RiskManager._calculate_atr(df, QuantConfig.ATR_PERIOD)
            atr_pct = (atr / current_price) * 100 if current_price > 0 else 0
            
            # 計算結構止損（波段低點）
            structure_stop = RiskManager._calculate_structure_stop(df, current_price)
            
            # 計算 ATR 止損
            atr_stop = current_price - (QuantConfig.ATR_K_STOP * atr)
            
            # 最終止損：取較保守者（多單取較高的止損）
            stop_loss = max(structure_stop, atr_stop)
            
            # 計算風險
            risk_per_share = current_price - stop_loss
            risk_pct = (risk_per_share / current_price) * 100 if current_price > 0 else 0
            
            # 計算停利（R 倍數）
            take_profit = current_price + (QuantConfig.R_MULTIPLE_TARGET * risk_per_share)
            
            # 風險回報比
            rr_ratio = QuantConfig.R_MULTIPLE_TARGET if risk_per_share > 0 else 0
            
            # 倉位建議（固定風險法）
            risk_amount = capital * QuantConfig.RISK_PER_TRADE
            position_size = int(risk_amount / risk_per_share) if risk_per_share > 0 else 0
            position_value = position_size * current_price
            position_pct = (position_value / capital) * 100 if capital > 0 else 0
            
            # 流動性評估
            liquidity = RiskManager._assess_liquidity(df)
            
            # 跳空風險評估
            gap_risk = RiskManager._assess_gap_risk(df, atr_pct)
            
            # v4.4.3 新增：時間停損評估
            time_stop = RiskManager._assess_time_stop(df, current_price)
            
            # tradable 判斷
            tradable = liquidity['tradable'] and rr_ratio >= QuantConfig.MIN_RR_RATIO
            
            # v4.5.12 新增：Beta 近似計算（使用波動率比率）
            # 真正的 Beta 需要大盤數據做回歸分析
            # 這裡使用 ATR% 與市場平均 ATR% (約 1.5%) 的比率作為近似
            market_avg_atr_pct = 1.5  # 大盤平均日波動率約 1.5%
            beta_approx = atr_pct / market_avg_atr_pct if market_avg_atr_pct > 0 else 1.0
            beta_approx = round(max(0.3, min(3.0, beta_approx)), 2)  # 限制在 0.3-3.0 之間
            
            return {
                'available': True,
                'entry_price': current_price,
                'stop_loss': round(stop_loss, 2),
                'take_profit': round(take_profit, 2),
                'structure_stop': round(structure_stop, 2),
                'atr_stop': round(atr_stop, 2),
                'atr': round(atr, 2),
                'atr_pct': round(atr_pct, 2),
                'beta': beta_approx,  # v4.5.12 新增
                'risk_per_share': round(risk_per_share, 2),
                'risk_pct': round(risk_pct, 2),
                'rr_ratio': round(rr_ratio, 2),
                'position_size_suggestion': position_size,
                'position_value': round(position_value, 0),
                'position_pct': round(position_pct, 2),
                'liquidity': liquidity,
                'gap_risk': gap_risk,
                'time_stop': time_stop,  # v4.4.3 新增
                'tradable': tradable,
                'tradable_reason': RiskManager._get_tradable_reason(liquidity, rr_ratio)
            }
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {'available': False, 'reason': f'分析錯誤: {str(e)}'}
    
    @staticmethod
    def _calculate_atr(df, period=14):
        """計算 ATR"""
        high = df['High']
        low = df['Low']
        close = df['Close'].shift(1)
        
        tr1 = high - low
        tr2 = abs(high - close)
        tr3 = abs(low - close)
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(period).mean().iloc[-1]
        
        return atr if pd.notna(atr) else 0
    
    @staticmethod
    def _calculate_structure_stop(df, current_price):
        """計算結構止損（近期波段低點）"""
        lookback = QuantConfig.SWING_LOOKBACK
        recent_lows = df['Low'].iloc[-lookback:]
        
        # 找出近期的波段低點（局部最小值）
        swing_lows = []
        for i in range(2, len(recent_lows) - 2):
            if (recent_lows.iloc[i] < recent_lows.iloc[i-1] and 
                recent_lows.iloc[i] < recent_lows.iloc[i-2] and
                recent_lows.iloc[i] < recent_lows.iloc[i+1] and 
                recent_lows.iloc[i] < recent_lows.iloc[i+2]):
                swing_lows.append(recent_lows.iloc[i])
        
        if swing_lows:
            # 取最近的波段低點
            return max(swing_lows[-1], recent_lows.min())
        
        # 如果沒有明顯波段低點，取近期最低價
        return recent_lows.min()
    
    @staticmethod
    def _assess_liquidity(df):
        """評估流動性"""
        avg_volume_20 = df['Volume'].iloc[-20:].mean() if len(df) >= 20 else df['Volume'].mean()
        avg_turnover_20 = (df['Close'] * df['Volume']).iloc[-20:].mean() if len(df) >= 20 else 0
        
        # 轉換為張數（假設1張=1000股）
        avg_volume_lots = avg_volume_20 / 1000 if avg_volume_20 else 0
        
        volume_ok = avg_volume_lots >= QuantConfig.MIN_AVG_VOLUME_20
        turnover_ok = avg_turnover_20 >= QuantConfig.MIN_TURNOVER_20
        
        tradable = volume_ok and turnover_ok if QuantConfig.ENABLE_LIQUIDITY_GATE else True
        
        return {
            'avg_volume_20': round(avg_volume_20, 0),
            'avg_volume_lots': round(avg_volume_lots, 0),
            'avg_turnover_20': round(avg_turnover_20, 0),
            'volume_ok': volume_ok,
            'turnover_ok': turnover_ok,
            'tradable': tradable,
            'liquidity_flag': not tradable
        }
    
    @staticmethod
    def _assess_gap_risk(df, atr_pct):
        """評估跳空風險"""
        if len(df) < 20:
            return {'gap_risk_flag': False, 'gap_count': 0, 'reason': '資料不足'}
        
        # 計算近期跳空次數
        recent = df.iloc[-20:]
        gaps = []
        for i in range(1, len(recent)):
            gap = recent['Low'].iloc[i] - recent['High'].iloc[i-1]
            if abs(gap) > recent['Close'].iloc[i-1] * 0.02:  # 2% 以上視為跳空
                gaps.append(gap)
        
        gap_count = len(gaps)
        high_volatility = atr_pct > 3.0  # ATR% > 3% 視為高波動
        
        gap_risk_flag = gap_count >= 3 or (gap_count >= 1 and high_volatility)
        
        return {
            'gap_risk_flag': gap_risk_flag,
            'gap_count': gap_count,
            'high_volatility': high_volatility,
            'reason': f"近20日跳空{gap_count}次，ATR%={atr_pct:.1f}%"
        }
    
    @staticmethod
    def _assess_time_stop(df, entry_price):
        """
        v4.4.3 新增：時間停損評估
        
        規則：若進場後 N 個交易日內股價未脫離成本區（未獲利），
        建議考慮出場以提高資金效率。
        
        Args:
            df: DataFrame，包含歷史價格資料
            entry_price: 進場價格（假設為當前價）
        
        Returns:
            dict: 時間停損評估結果
        """
        try:
            time_stop_days = QuantConfig.TIME_STOP_DAYS
            threshold = QuantConfig.TIME_STOP_THRESHOLD
            
            if len(df) < time_stop_days:
                return {
                    'time_stop_triggered': False,
                    'days_since_entry': len(df),
                    'profit_pct': 0,
                    'reason': '資料不足'
                }
            
            # 模擬從 time_stop_days 天前進場
            # 檢查這段時間是否有明顯獲利
            entry_date_idx = -time_stop_days - 1
            if abs(entry_date_idx) > len(df):
                entry_date_idx = 0
            
            simulated_entry = df['Close'].iloc[entry_date_idx]
            current_close = df['Close'].iloc[-1]
            profit_pct = ((current_close - simulated_entry) / simulated_entry) * 100
            
            # 判斷是否觸發時間停損
            # 如果獲利 < 閾值（預設 0%），表示未脫離成本區
            time_stop_triggered = profit_pct < threshold
            
            # 生成建議
            if time_stop_triggered:
                advice = f"⏰ 時間停損提醒：進場{time_stop_days}日，報酬{profit_pct:+.2f}%未達標，建議檢視是否出場"
            else:
                advice = f"✓ 進場{time_stop_days}日，報酬{profit_pct:+.2f}%，持股正常"
            
            return {
                'time_stop_triggered': time_stop_triggered,
                'days_since_entry': time_stop_days,
                'profit_pct': round(profit_pct, 2),
                'threshold': threshold,
                'advice': advice
            }
            
        except Exception as e:
            return {
                'time_stop_triggered': False,
                'days_since_entry': 0,
                'profit_pct': 0,
                'reason': f'計算錯誤: {str(e)}'
            }
    
    @staticmethod
    def _get_tradable_reason(liquidity, rr_ratio):
        """獲取可交易性原因"""
        reasons = []
        
        if not liquidity['volume_ok']:
            reasons.append(f"成交量不足（需>{QuantConfig.MIN_AVG_VOLUME_20}張）")
        if not liquidity['turnover_ok']:
            reasons.append(f"成交金額不足（需>{QuantConfig.MIN_TURNOVER_20/10000:.0f}萬）")
        if rr_ratio < QuantConfig.MIN_RR_RATIO:
            reasons.append(f"風險回報比不足（{rr_ratio:.2f}<{QuantConfig.MIN_RR_RATIO}）")
        
        if reasons:
            return "不建議交易：" + "；".join(reasons)
        return "符合交易條件"


# ============================================================================
# PatternAnalyzer - 形態識別核心 (v4.4.6 新增)
# ============================================================================

"""
PatternAnalyzer v2.0 - 嚴格形態識別分析器

重大改進：
1. 前置趨勢檢查：確保形態出現在正確的趨勢背景下
2. 嚴格的幾何特徵驗證：時間間隔、高度一致性、形態深度
3. V型反轉動能驗證：急跌定義、關鍵K棒、快速反彈
4. 頭肩形態對稱性檢查：頭部突出度、時間對稱性

作者：Stock Analysis System
版本：v2.0 (2026-01-22)
"""

import pandas as pd
import numpy as np
from typing import Tuple, List
from dataclasses import dataclass
from enum import Enum


class PatternType(Enum):
    """形態類型枚舉"""
    DOUBLE_TOP = 'M頭'
    DOUBLE_BOTTOM = 'W底'
    HEAD_SHOULDERS_TOP = '頭肩頂'
    HEAD_SHOULDERS_BOTTOM = '頭肩底'
    V_REVERSAL = 'V型反轉'


class PatternStatus(Enum):
    """形態狀態枚舉"""
    FORMING = 'FORMING'                      # 形成中
    CONFIRMED_BREAKOUT = 'CONFIRMED_BREAKOUT'  # 確立突破（多頭）
    CONFIRMED_BREAKDOWN = 'CONFIRMED_BREAKDOWN'  # 確立跌破（空頭）
    FAILED = 'FAILED'                        # 形態失敗


@dataclass
class PatternConfig:
    """
    形態識別參數配置
    
    這些參數經過嚴格設計，用於過濾掉雜訊和假形態
    """
    # ========================================
    # 通用參數
    # ========================================
    lookback_days: int = 60                  # 回看天數
    peak_window: int = 5                     # 極值點識別窗口（前後各 N 天）
    min_point_distance: int = 3              # 極值點最小間隔
    volume_confirm_ratio: float = 1.0        # 量能確認倍數（相對於5日均量）
    
    # ========================================
    # 前置趨勢檢查參數
    # ========================================
    prior_trend_lookback: int = 20           # 前置趨勢回看天數
    prior_trend_min_change: float = 0.10     # 前置趨勢最小變化幅度 (10%)
    
    # ========================================
    # 雙重頂/底 (M頭/W底) 參數
    # ========================================
    double_min_spacing: int = 15             # 兩峰/谷最小間隔天數
    double_height_tolerance: float = 0.03    # 高度一致性容許誤差 (±3%)
    double_min_depth_pct: float = 0.05       # 最小形態深度 (頸線到峰/谷 >= 5%)
    
    # ========================================
    # V型反轉參數
    # ========================================
    v_drop_lookback: int = 5                 # 急跌判斷回看天數
    v_min_drop_pct: float = 0.05             # 急跌最小幅度 (5%)
    v_lower_shadow_ratio: float = 2.0        # 長下影線判斷（下影長 > 實體 * 2）
    v_rebound_lookback: int = 5              # 反彈判斷天數
    v_min_rebound_recovery: float = 0.50     # 最小反彈收復比例 (收復跌幅的 50%)
    
    # ========================================
    # 頭肩頂/底參數
    # ========================================
    hs_min_head_prominence: float = 0.03     # 頭部最小突出度 (比肩部高/低 3%)
    hs_max_time_asymmetry: float = 0.50      # 最大時間不對稱度 (50%)
    hs_max_shoulder_diff: float = 0.05       # 左右肩最大高度差 (5%)
    
    # ========================================
    # v4.5.11 新增：時效性濾網 (Recency Filter)
    # ========================================
    # 解決問題：股價已從高點回落，但仍高於頸線，系統誤報為「形態確立」
    max_distance_from_neckline: float = 0.08  # 距頸線最大允許幅度 (8%)
    # 超過此幅度視為「已漲完」或「已跌完」，訊號降級
    breakout_validity_days: int = 10          # 突破訊號有效天數（可選）
    
    # ========================================
    # v4.5.16 新增：形態失效檢查 (Pattern Invalidation)
    # ========================================
    # 解決問題：形態形成中但已被價格突破破壞，應標記為 FAILED
    max_formation_days: int = 30              # 關鍵點形成後，最長等待突破天數
    # 超過此天數視為「形態拖太久」，效力減弱


class PatternAnalyzer:
    """
    嚴格形態識別分析器 v2.0
    
    =====================================================
    核心設計原則：
    =====================================================
    1. 寧可漏報，不可誤報
       - 每個形態都必須通過多重嚴格檢查
       - 模糊不清的形態一律不報
    
    2. 前置趨勢是形態的必要條件
       - 底部形態必須出現在下跌趨勢之後
       - 頂部形態必須出現在上漲趨勢之後
       - 沒有前置趨勢的「形態」只是盤整區間的雜訊
    
    3. 幾何特徵必須嚴格符合教科書定義
       - M頭/W底：兩峰/谷必須等高、有足夠間隔、有足夠深度
       - V型反轉：必須有急跌、關鍵K棒、快速反彈
       - 頭肩形態：頭部必須顯著突出、左右必須對稱
    
    =====================================================
    識別的形態：
    =====================================================
    頭部形態（賣出訊號）：
    1. M頭 (Double Top) - 雙重頂
    2. 頭肩頂 (Head and Shoulders Top)
    
    底部形態（買進訊號）：
    3. W底 (Double Bottom) - 雙重底
    4. 頭肩底 (Head and Shoulders Bottom)
    5. V型反轉 (V-Reversal)
    """
    
    def __init__(self, config: PatternConfig = None):
        """
        初始化分析器
        
        Args:
            config: 參數配置，若為 None 則使用預設值
        """
        self.config = config or PatternConfig()
    
    @staticmethod
    def analyze(df: pd.DataFrame, lookback: int = None, config: PatternConfig = None) -> dict:
        """
        執行完整形態分析（靜態方法，保持向後兼容）
        
        Args:
            df: DataFrame，需包含 Open, High, Low, Close, Volume
            lookback: 回看天數（預設 60 天）
            config: 參數配置
        
        Returns:
            dict: 形態分析結果
        """
        analyzer = PatternAnalyzer(config)
        return analyzer._analyze(df, lookback)
    
    def _analyze(self, df: pd.DataFrame, lookback: int = None) -> dict:
        """
        執行完整形態分析
        
        分析流程：
        1. 數據預處理與驗證
        2. 識別顯著高低點
        3. 依序檢測各種形態（每種形態都包含嚴格的幾何檢查）
        4. 選擇最可靠的形態返回
        """
        # ========================================
        # Step 1: 數據驗證與預處理
        # ========================================
        if df is None or len(df) < 30:
            return {
                'available': False,
                'message': '數據不足，需要至少 30 天數據才能進行形態分析'
            }
        
        lookback = lookback or self.config.lookback_days
        
        # 取得最近 N 天數據並重設索引
        df_recent = df.tail(lookback).copy()
        df_recent = df_recent.reset_index(drop=True)
        
        if len(df_recent) < 30:
            return {
                'available': False,
                'message': '數據不足'
            }
        
        try:
            # ========================================
            # Step 2: 計算輔助指標
            # ========================================
            df_recent['MA5_Vol'] = df_recent['Volume'].rolling(5).mean()
            df_recent['MA10'] = df_recent['Close'].rolling(10).mean()
            df_recent['MA20'] = df_recent['Close'].rolling(20).mean()
            
            # ========================================
            # Step 3: 識別顯著高低點
            # ========================================
            peaks, valleys = self._find_significant_points(df_recent)
            
            # ========================================
            # Step 4: 依序檢測各種形態
            # ========================================
            patterns_detected = []
            
            # 4.1 檢測 M頭（雙重頂）
            m_top = self._detect_double_top(df_recent, peaks, valleys)
            if m_top['detected']:
                patterns_detected.append(m_top)
            
            # 4.2 檢測 W底（雙重底）
            w_bottom = self._detect_double_bottom(df_recent, peaks, valleys)
            if w_bottom['detected']:
                patterns_detected.append(w_bottom)
            
            # 4.3 檢測頭肩頂
            hs_top = self._detect_head_shoulders_top(df_recent, peaks, valleys)
            if hs_top['detected']:
                patterns_detected.append(hs_top)
            
            # 4.4 檢測頭肩底
            hs_bottom = self._detect_head_shoulders_bottom(df_recent, peaks, valleys)
            if hs_bottom['detected']:
                patterns_detected.append(hs_bottom)
            
            # 4.5 檢測 V型反轉
            v_reversal = self._detect_v_reversal(df_recent)
            if v_reversal['detected']:
                patterns_detected.append(v_reversal)
            
            # ========================================
            # Step 5: 選擇最可靠的形態（優先級 + 信心度）
            # ========================================
            #
            # 優先級設計（底部信號可信度排序）：
            #   頭肩底 (3) > W底 (2) > V型反轉 (1)
            #   頭肩頂 (2) > M頭 (1)
            #
            # 選擇規則：
            #   1. CONFIRMED > FORMING（已確立優先）
            #   2. 同狀態時：優先級高者勝出
            #   3. 同優先級時：信心度高者勝出
            # ========================================
            _PATTERN_PRIORITY = {
                '頭肩底':   3,
                'W底':      2,
                'V型反轉':  1,
                '頭肩頂':   2,
                'M頭':      1,
            }

            def _pattern_sort_key(p):
                priority  = _PATTERN_PRIORITY.get(p.get('pattern_name', ''), 0)
                is_confirmed = 1 if 'CONFIRMED' in p.get('status', '') else 0
                confidence = p.get('confidence', 0)
                return (is_confirmed, priority, confidence)

            if patterns_detected:
                # 優先選擇已確立的形態（CONFIRMED）
                confirmed = [p for p in patterns_detected if 'CONFIRMED' in p.get('status', '')]

                if confirmed:
                    # 在已確立的形態中：優先級優先，相同優先級時取信心度最高
                    best_pattern = max(confirmed, key=_pattern_sort_key)
                else:
                    # 若沒有確立的形態，選擇優先級/信心度最高的
                    best_pattern = max(patterns_detected, key=_pattern_sort_key)
                
                return {
                    'available': True,
                    'detected': True,
                    'pattern_name': best_pattern['pattern_name'],
                    'pattern_type': best_pattern['pattern_type'],
                    'status': best_pattern['status'],
                    'neckline_price': best_pattern.get('neckline_price', 0),
                    'target_price': best_pattern.get('target_price', 0),
                    'stop_loss': best_pattern.get('stop_loss', 0),
                    'confidence': best_pattern.get('confidence', 50),
                    'volume_confirmed': best_pattern.get('volume_confirmed', False),
                    'description': best_pattern.get('description', ''),
                    'all_patterns': patterns_detected,
                    'signal': best_pattern.get('signal', 'neutral'),
                    'score_impact': best_pattern.get('score_impact', 0),
                    'geometry_checks': best_pattern.get('geometry_checks', {})  # v2.0 新增：幾何檢查詳情
                }
            else:
                return {
                    'available': True,
                    'detected': False,
                    'pattern_name': None,
                    'status': None,
                    'description': '未偵測到符合嚴格條件的形態',
                    'signal': 'neutral',
                    'score_impact': 0
                }
        
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {
                'available': False,
                'message': f'形態分析錯誤: {str(e)}'
            }
    
    # ========================================================================
    # 輔助方法：極值點識別
    # ========================================================================
    
    def _find_significant_points(self, df: pd.DataFrame) -> Tuple[List[Tuple], List[Tuple]]:
        """
        識別顯著高低點
        
        數學原理：
        使用局部極值法：某點 i 若為 [i-window, i+window] 範圍內的最高/最低點，
        則視為顯著的峰/谷。
        
        這種方法可以過濾掉日內波動的小高低點，只保留具有結構意義的極值點。
        
        v4.5.15 效能優化：使用 scipy.signal.argrelextrema 取代 Python 迴圈
        原本 O(N*W) 的複雜度降低為 O(N)，速度提升 10-50 倍
        
        Args:
            df: DataFrame，包含 High, Low, Close 欄位
        
        Returns:
            tuple: (peaks, valleys)
                peaks: [(index, price), ...] - 顯著高點列表
                valleys: [(index, price), ...] - 顯著低點列表
        """
        window = self.config.peak_window
        highs = df['High'].values
        lows = df['Low'].values
        
        # ============================================================
        # v4.5.15 效能優化：使用 scipy 向量化運算
        # ============================================================
        try:
            from scipy.signal import argrelextrema
            import numpy as np
            
            # 使用 scipy 直接找出極值索引（底層是 C 語言優化）
            # np.greater 找局部高點, np.less 找局部低點
            high_idx = argrelextrema(highs, np.greater_equal, order=window)[0]
            low_idx = argrelextrema(lows, np.less_equal, order=window)[0]
            
            # 轉換格式
            peaks = [(int(i), float(highs[i])) for i in high_idx]
            valleys = [(int(i), float(lows[i])) for i in low_idx]
            
        except ImportError:
            # 如果沒有安裝 scipy，回退到原始方法
            peaks = []
            valleys = []
            
            # 遍歷有效範圍內的每個點
            for i in range(window, len(df) - window):
                # 取出以 i 為中心的局部窗口
                local_highs = highs[i - window:i + window + 1]
                local_lows = lows[i - window:i + window + 1]
                
                # 檢查是否為局部高點（該點是窗口內的最高點）
                if highs[i] == max(local_highs):
                    peaks.append((i, highs[i]))
                
                # 檢查是否為局部低點（該點是窗口內的最低點）
                if lows[i] == min(local_lows):
                    valleys.append((i, lows[i]))
        
        # 過濾太近的點（避免連續多天被重複計入）
        peaks = self._filter_close_points(peaks, is_peak=True)
        valleys = self._filter_close_points(valleys, is_peak=False)
        
        return peaks, valleys
    
    def _filter_close_points(self, points: List[Tuple], is_peak: bool) -> List[Tuple]:
        """
        過濾太近的極值點
        
        數學邏輯：
        當兩個極值點相距小於 min_point_distance 天時，
        保留更極端的那個點（高點取更高者，低點取更低者）。
        
        這可以避免在盤整區間內產生過多的假極值點。
        
        Args:
            points: 極值點列表 [(index, price), ...]
            is_peak: True 表示處理高點，False 表示處理低點
        
        Returns:
            list: 過濾後的極值點列表
        """
        if len(points) < 2:
            return points
        
        min_distance = self.config.min_point_distance
        filtered = [points[0]]
        
        for i in range(1, len(points)):
            current_idx, current_price = points[i]
            last_idx, last_price = filtered[-1]
            
            # 檢查與上一個保留點的距離
            if current_idx - last_idx >= min_distance:
                # 距離足夠，直接保留
                filtered.append(points[i])
            else:
                # 距離太近，保留更極端的點
                if is_peak:
                    # 對於高點，保留更高的
                    if current_price > last_price:
                        filtered[-1] = points[i]
                else:
                    # 對於低點，保留更低的
                    if current_price < last_price:
                        filtered[-1] = points[i]
        
        return filtered
    
    # ========================================================================
    # 通用檢查方法
    # ========================================================================
    
    def _check_prior_downtrend(self, df: pd.DataFrame, reference_idx: int, reference_price: float) -> Tuple[bool, str]:
        """
        檢查前置下跌趨勢（用於底部形態）
        
        數學定義：
        在 reference_idx 之前的 prior_trend_lookback 天內，
        最高點必須比 reference_price 高至少 prior_trend_min_change (10%)。
        
        這確保了底部形態確實出現在一段下跌之後，而不是盤整區間的隨機波動。
        
        Args:
            df: DataFrame
            reference_idx: 參考點索引（通常是左腳/左肩）
            reference_price: 參考價格
        
        Returns:
            tuple: (is_valid, description)
        """
        lookback = self.config.prior_trend_lookback
        min_change = self.config.prior_trend_min_change
        
        # 計算回看範圍
        start_idx = max(0, reference_idx - lookback)
        
        if start_idx >= reference_idx:
            return False, "數據不足以判斷前置趨勢"
        
        # 找出前置區間的最高價
        prior_high = df['High'].iloc[start_idx:reference_idx].max()
        
        # 計算下跌幅度
        # 下跌幅度 = (最高價 - 參考價) / 最高價
        decline_pct = (prior_high - reference_price) / prior_high
        
        if decline_pct >= min_change:
            return True, f"前置下跌趨勢確認：從${prior_high:.2f}下跌至${reference_price:.2f}（跌幅{decline_pct*100:.1f}%）"
        else:
            return False, f"前置下跌趨勢不足：跌幅僅{decline_pct*100:.1f}%，需>={min_change*100:.0f}%"
    
    def _check_prior_uptrend(self, df: pd.DataFrame, reference_idx: int, reference_price: float) -> Tuple[bool, str]:
        """
        檢查前置上漲趨勢（用於頂部形態）
        
        數學定義：
        在 reference_idx 之前的 prior_trend_lookback 天內，
        最低點必須比 reference_price 低至少 prior_trend_min_change (10%)。
        
        這確保了頂部形態確實出現在一段上漲之後，而不是盤整區間的隨機波動。
        
        Args:
            df: DataFrame
            reference_idx: 參考點索引（通常是左峰/左肩）
            reference_price: 參考價格
        
        Returns:
            tuple: (is_valid, description)
        """
        lookback = self.config.prior_trend_lookback
        min_change = self.config.prior_trend_min_change
        
        # 計算回看範圍
        start_idx = max(0, reference_idx - lookback)
        
        if start_idx >= reference_idx:
            return False, "數據不足以判斷前置趨勢"
        
        # 找出前置區間的最低價
        prior_low = df['Low'].iloc[start_idx:reference_idx].min()
        
        # 計算上漲幅度
        # 上漲幅度 = (參考價 - 最低價) / 最低價
        rally_pct = (reference_price - prior_low) / prior_low
        
        if rally_pct >= min_change:
            return True, f"前置上漲趨勢確認：從${prior_low:.2f}上漲至${reference_price:.2f}（漲幅{rally_pct*100:.1f}%）"
        else:
            return False, f"前置上漲趨勢不足：漲幅僅{rally_pct*100:.1f}%，需>={min_change*100:.0f}%"
    
    # ========================================================================
    # M頭（雙重頂）檢測
    # ========================================================================
    
    def _detect_double_top(self, df: pd.DataFrame, peaks: List[Tuple], valleys: List[Tuple]) -> dict:
        """
        偵測 M頭（雙重頂）
        
        =====================================================
        嚴格幾何條件（v2.0）：
        =====================================================
        
        1. 前置上漲趨勢 (Prior Uptrend)
           - 左峰前 20 天的最低價需比左峰低 >= 10%
           - 原理：頂部形態必須出現在上漲之後才有意義
        
        2. 時間間隔 (Spacing)
           - 兩峰的索引間隔 (idx2 - idx1) >= 15 天
           - 原理：間隔太短的「雙峰」可能只是盤整區的雜訊
        
        3. 高度一致性 (Level Check)
           - |P2 - P1| / P1 <= 3%
           - 原理：標準 M頭的兩個峰高度應該非常接近
        
        4. 形態深度 (Depth)
           - (峰值 - 頸線) / 峰值 >= 5%
           - 原理：扁平的盤整區不能算是有效的 M頭
        
        5. 頸線突破確認 (Neckline Break)
           - 收盤價 < 頸線價格
           - 配合量能確認（成交量 >= 5日均量）
        
        =====================================================
        目標價計算（測量法則）：
        =====================================================
        目標價 = 頸線 - (峰值 - 頸線)
        
        即：下跌幅度等於形態的垂直高度
        """
        result = {
            'detected': False,
            'pattern_name': PatternType.DOUBLE_TOP.value,
            'pattern_type': 'top',
            'signal': 'neutral',
            'geometry_checks': {}
        }
        
        # 基本條件：需要至少 2 個高點和 1 個低點
        if len(peaks) < 2 or len(valleys) < 1:
            return result
        
        # 取最近的高點進行檢測
        recent_peaks = peaks[-4:] if len(peaks) >= 4 else peaks
        
        for i in range(len(recent_peaks) - 1):
            p1_idx, p1_price = recent_peaks[i]
            p2_idx, p2_price = recent_peaks[i + 1]
            
            # ========================================
            # 幾何檢查 1: 時間間隔
            # ========================================
            spacing = p2_idx - p1_idx
            min_spacing = self.config.double_min_spacing
            
            if spacing < min_spacing:
                result['geometry_checks']['spacing'] = {
                    'passed': False,
                    'actual': spacing,
                    'required': min_spacing,
                    'description': f'時間間隔不足：{spacing}天 < {min_spacing}天'
                }
                continue
            
            # ========================================
            # 幾何檢查 2: 高度一致性
            # ========================================
            height_diff_pct = abs(p2_price - p1_price) / p1_price
            max_diff = self.config.double_height_tolerance
            
            if height_diff_pct > max_diff:
                result['geometry_checks']['height_consistency'] = {
                    'passed': False,
                    'actual': height_diff_pct,
                    'required': max_diff,
                    'description': f'高度差異過大：{height_diff_pct*100:.1f}% > {max_diff*100:.0f}%'
                }
                continue
            
            # ========================================
            # 找頸線（兩峰之間的最低谷）
            # ========================================
            middle_valleys = [v for v in valleys if p1_idx < v[0] < p2_idx]
            if not middle_valleys:
                continue
            
            neckline_idx, neckline_price = min(middle_valleys, key=lambda x: x[1])
            
            # ========================================
            # 幾何檢查 3: 形態深度
            # ========================================
            peak_avg = (p1_price + p2_price) / 2
            depth_pct = (peak_avg - neckline_price) / peak_avg
            min_depth = self.config.double_min_depth_pct
            
            if depth_pct < min_depth:
                result['geometry_checks']['depth'] = {
                    'passed': False,
                    'actual': depth_pct,
                    'required': min_depth,
                    'description': f'形態深度不足：{depth_pct*100:.1f}% < {min_depth*100:.0f}%'
                }
                continue
            
            # ========================================
            # 幾何檢查 4: 前置上漲趨勢
            # ========================================
            trend_valid, trend_desc = self._check_prior_uptrend(df, p1_idx, p1_price)
            
            if not trend_valid:
                result['geometry_checks']['prior_trend'] = {
                    'passed': False,
                    'description': trend_desc
                }
                continue
            
            # ========================================
            # 所有幾何檢查通過！
            # ========================================
            result['geometry_checks'] = {
                'spacing': {
                    'passed': True,
                    'actual': spacing,
                    'required': min_spacing,
                    'description': f'時間間隔：{spacing}天 ✓'
                },
                'height_consistency': {
                    'passed': True,
                    'actual': height_diff_pct,
                    'required': max_diff,
                    'description': f'高度差異：{height_diff_pct*100:.1f}% ✓'
                },
                'depth': {
                    'passed': True,
                    'actual': depth_pct,
                    'required': min_depth,
                    'description': f'形態深度：{depth_pct*100:.1f}% ✓'
                },
                'prior_trend': {
                    'passed': True,
                    'description': trend_desc
                }
            }
            
            # ========================================
            # 【v4.5.14 新增】提取關鍵點日期
            # ========================================
            try:
                p1_date = df.index[p1_idx].strftime('%Y-%m-%d') if hasattr(df.index[p1_idx], 'strftime') else str(df.index[p1_idx])
                p2_date = df.index[p2_idx].strftime('%Y-%m-%d') if hasattr(df.index[p2_idx], 'strftime') else str(df.index[p2_idx])
                neckline_date = df.index[neckline_idx].strftime('%Y-%m-%d') if hasattr(df.index[neckline_idx], 'strftime') else str(df.index[neckline_idx])
            except:
                p1_date = f'Day {p1_idx}'
                p2_date = f'Day {p2_idx}'
                neckline_date = f'Day {neckline_idx}'
            
            # 關鍵點資訊
            key_points = {
                'left_peak': {
                    'date': p1_date,
                    'price': round(p1_price, 2),
                    'index': p1_idx
                },
                'right_peak': {
                    'date': p2_date,
                    'price': round(p2_price, 2),
                    'index': p2_idx
                },
                'neckline': {
                    'date': neckline_date,
                    'price': round(neckline_price, 2),
                    'index': neckline_idx
                }
            }
            
            # ========================================
            # 檢查頸線突破狀態
            # ========================================
            current_close = df['Close'].iloc[-1]
            current_volume = df['Volume'].iloc[-1]
            ma5_vol = df['MA5_Vol'].iloc[-1]
            
            # 計算目標價與停損
            pattern_height = peak_avg - neckline_price
            target_price = neckline_price - pattern_height
            stop_loss = max(p1_price, p2_price)
            
            # ========================================
            # 【v4.5.16 新增】形態失效檢查 (Pattern Invalidation)
            # ========================================
            # 如果收盤價漲破最高點 (P1 或 P2)，代表 M頭失敗，趨勢向上
            if current_close > stop_loss:
                # 形態已失效，跳過此形態
                continue
            
            # ========================================
            # 【v4.5.16 新增】形成超時檢查 (Formation Timeout)
            # ========================================
            days_since_last_point = len(df) - 1 - p2_idx
            if days_since_last_point > self.config.max_formation_days:
                # 形態拖太久（超過30天），效力大幅減弱，視為無效
                continue
            
            # 判斷狀態
            if current_close < neckline_price:
                # ========================================
                # 【v4.5.14 新增】陳舊突破檢查 (Stale Breakout Check)
                # ========================================
                # 檢查 P2 之後到昨天的最低價
                # 如果曾經跌破頸線很多（>5%），代表早就跌破過了
                
                is_stale_breakdown = False
                period_low = float('inf')
                stale_threshold = 0.05  # 5% 閾值
                
                # 檢查區間：從 P2 後一天到昨天（不含今天）
                check_start = p2_idx + 1
                check_end = len(df) - 1  # 不含今天
                
                if check_end > check_start:
                    # 取得這段期間的最低價
                    period_low = df['Low'].iloc[check_start:check_end].min()
                    
                    # 如果這段期間曾經跌破頸線很多，代表早就跌破過了
                    if period_low < neckline_price * (1 - stale_threshold):
                        is_stale_breakdown = True
                
                # ========================================
                # v4.5.11: 時效性濾網 (Distance Check)
                # ========================================
                distance_pct = (neckline_price - current_close) / neckline_price
                max_distance = self.config.max_distance_from_neckline
                
                volume_confirmed = current_volume >= ma5_vol * self.config.volume_confirm_ratio
                
                # ========================================
                # 判斷結果（優先順序：陳舊跌破 > 距離過遠 > 確認跌破）
                # ========================================
                
                if is_stale_breakdown:
                    # 【v4.5.14】這是「反彈」或「跌多反彈」，不是新鮮跌破
                    result = {
                        'detected': True,
                        'pattern_name': PatternType.DOUBLE_TOP.value,
                        'pattern_type': 'top',
                        'status': 'PULLBACK_TEST',  # 新狀態：反彈測試壓力
                        'neckline_price': round(neckline_price, 2),
                        'target_price': round(target_price, 2),
                        'stop_loss': round(stop_loss, 2),
                        'confidence': 35,  # 低信心度
                        'volume_confirmed': volume_confirmed,
                        'description': (
                            f'M頭頸線${neckline_price:.2f}反彈測試中（歷史最低曾達${period_low:.2f}），'
                            f'非新鮮跌破，觀察壓力是否有效。'
                            f'左峰${p1_price:.2f}({p1_date})，右峰${p2_price:.2f}({p2_date})'
                        ),
                        'signal': 'hold',  # 降級為持有/觀望
                        'score_impact': -3,  # 極低分數影響
                        'geometry_checks': result['geometry_checks'],
                        'distance_from_neckline': round(-distance_pct * 100, 1),
                        'key_points': key_points,
                        'period_low': round(period_low, 2),
                        'is_stale_breakdown': True
                    }
                    return result
                
                elif distance_pct > max_distance:
                    # 形態已跌破一段時間，訊號降級
                    result = {
                        'detected': True,
                        'pattern_name': PatternType.DOUBLE_TOP.value,
                        'pattern_type': 'top',
                        'status': 'TARGET_REACHED',
                        'neckline_price': round(neckline_price, 2),
                        'target_price': round(target_price, 2),
                        'stop_loss': round(stop_loss, 2),
                        'confidence': 40,
                        'volume_confirmed': volume_confirmed,
                        'description': (
                            f'M頭已跌破一段時間（距頸線-{distance_pct*100:.1f}%），不宜追空。'
                            f'頸線${neckline_price:.2f}，目前${current_close:.2f}。'
                            f'左峰${p1_price:.2f}({p1_date})，右峰${p2_price:.2f}({p2_date})'
                        ),
                        'signal': 'hold',
                        'score_impact': -5,
                        'geometry_checks': result['geometry_checks'],
                        'distance_from_neckline': round(-distance_pct * 100, 1),
                        'key_points': key_points,
                        'is_stale_breakdown': False
                    }
                    return result
                
                else:
                    # ========================================
                    # 形態確立：剛跌破頸線（距離在合理範圍內且非陳舊跌破）
                    # ========================================
                    result = {
                        'detected': True,
                        'pattern_name': PatternType.DOUBLE_TOP.value,
                        'pattern_type': 'top',
                        'status': PatternStatus.CONFIRMED_BREAKDOWN.value,
                        'neckline_price': round(neckline_price, 2),
                        'target_price': round(target_price, 2),
                        'stop_loss': round(stop_loss, 2),
                        'confidence': 85 if volume_confirmed else 65,
                        'volume_confirmed': volume_confirmed,
                        'description': (
                            f'M頭確立！收盤${current_close:.2f}跌破頸線${neckline_price:.2f}，'
                            f'距頸線-{distance_pct*100:.1f}%，間隔{spacing}天，深度{depth_pct*100:.1f}%'
                            + ('，量能確認' if volume_confirmed else '，量能不足')
                            + f'。左峰${p1_price:.2f}({p1_date})，右峰${p2_price:.2f}({p2_date})'
                        ),
                        'signal': 'sell',
                        'score_impact': -40 if volume_confirmed else -25,
                        'geometry_checks': result['geometry_checks'],
                        'distance_from_neckline': round(-distance_pct * 100, 1),
                        'key_points': key_points,
                        'is_stale_breakdown': False
                    }
                    return result
            else:
                # ========================================
                # 形態形成中
                # ========================================
                result = {
                    'detected': True,
                    'pattern_name': PatternType.DOUBLE_TOP.value,
                    'pattern_type': 'top',
                    'status': PatternStatus.FORMING.value,
                    'neckline_price': round(neckline_price, 2),
                    'target_price': round(target_price, 2),
                    'stop_loss': round(stop_loss, 2),
                    'confidence': 55,
                    'volume_confirmed': False,
                    'description': (
                        f'M頭形成中，頸線${neckline_price:.2f}，跌破則確立。'
                        f'間隔{spacing}天，深度{depth_pct*100:.1f}%。'
                        f'左峰${p1_price:.2f}({p1_date})，右峰${p2_price:.2f}({p2_date})'
                    ),
                    'signal': 'neutral',
                    'score_impact': -10,
                    'geometry_checks': result['geometry_checks'],
                    'key_points': key_points
                }
                return result
        
        return result
    
    # ========================================================================
    # W底（雙重底）檢測
    # ========================================================================
    
    def _detect_double_bottom(self, df: pd.DataFrame, peaks: List[Tuple], valleys: List[Tuple]) -> dict:
        """
        偵測 W底（雙重底）
        
        =====================================================
        嚴格幾何條件（v4.5.14）：
        =====================================================
        
        1. 前置下跌趨勢 (Prior Downtrend)
           - 左腳前 20 天的最高價需比左腳高 >= 10%
           - 原理：底部形態必須出現在下跌之後才有意義
        
        2. 時間間隔 (Spacing)
           - 兩谷的索引間隔 (idx2 - idx1) >= 15 天
           - 原理：間隔太短的「雙谷」可能只是盤整區的雜訊
        
        3. 高度一致性 (Level Check)
           - V2 >= V1 * (1 - 3%)，即右腳不能破左腳超過 3%
           - 標準 W底：右腳略高於左腳
           - 原理：標準 W底的兩個谷深度應該非常接近
        
        4. 形態深度 (Depth)
           - (頸線 - 谷值) / 谷值 >= 5%
           - 原理：扁平的盤整區不能算是有效的 W底
        
        5. 頸線突破確認 (Neckline Break)
           - 收盤價 > 頸線價格
           - 配合量能確認（成交量 >= 5日均量）
        
        6. 【v4.5.14 新增】陳舊突破檢查 (Stale Breakout Check)
           - 檢查 V2 之後到昨天的最高價
           - 如果曾經漲超過頸線很多（>5%），代表早就突破過了
           - 現在是「回測」而非「新鮮突破」
        
        =====================================================
        目標價計算（測量法則）：
        =====================================================
        目標價 = 頸線 + (頸線 - 谷值)
        
        即：上漲幅度等於形態的垂直高度
        """
        result = {
            'detected': False,
            'pattern_name': PatternType.DOUBLE_BOTTOM.value,
            'pattern_type': 'bottom',
            'signal': 'neutral',
            'geometry_checks': {}
        }
        
        # 基本條件：需要至少 2 個低點和 1 個高點
        if len(valleys) < 2 or len(peaks) < 1:
            return result
        
        # 取最近的低點進行檢測
        recent_valleys = valleys[-4:] if len(valleys) >= 4 else valleys
        
        for i in range(len(recent_valleys) - 1):
            v1_idx, v1_price = recent_valleys[i]
            v2_idx, v2_price = recent_valleys[i + 1]
            
            # ========================================
            # 幾何檢查 1: 時間間隔
            # ========================================
            spacing = v2_idx - v1_idx
            min_spacing = self.config.double_min_spacing
            
            if spacing < min_spacing:
                result['geometry_checks']['spacing'] = {
                    'passed': False,
                    'actual': spacing,
                    'required': min_spacing,
                    'description': f'時間間隔不足：{spacing}天 < {min_spacing}天'
                }
                continue
            
            # ========================================
            # 幾何檢查 2: 高度一致性（右腳不破左腳）
            # ========================================
            # 計算右腳相對左腳的位置
            # v2_price >= v1_price * (1 - tolerance) 表示右腳不能比左腳低太多
            max_diff = self.config.double_height_tolerance
            lower_bound = v1_price * (1 - max_diff)
            
            if v2_price < lower_bound:
                height_diff_pct = (v1_price - v2_price) / v1_price
                result['geometry_checks']['height_consistency'] = {
                    'passed': False,
                    'actual': height_diff_pct,
                    'required': max_diff,
                    'description': f'右腳破左腳過多：{height_diff_pct*100:.1f}% > {max_diff*100:.0f}%'
                }
                continue
            
            # ========================================
            # 找頸線（兩谷之間的最高峰）
            # ========================================
            middle_peaks = [p for p in peaks if v1_idx < p[0] < v2_idx]
            if not middle_peaks:
                continue
            
            neckline_idx, neckline_price = max(middle_peaks, key=lambda x: x[1])
            
            # ========================================
            # 幾何檢查 3: 形態深度
            # ========================================
            valley_avg = (v1_price + v2_price) / 2
            depth_pct = (neckline_price - valley_avg) / valley_avg
            min_depth = self.config.double_min_depth_pct
            
            if depth_pct < min_depth:
                result['geometry_checks']['depth'] = {
                    'passed': False,
                    'actual': depth_pct,
                    'required': min_depth,
                    'description': f'形態深度不足：{depth_pct*100:.1f}% < {min_depth*100:.0f}%'
                }
                continue
            
            # ========================================
            # 幾何檢查 4: 前置下跌趨勢
            # ========================================
            trend_valid, trend_desc = self._check_prior_downtrend(df, v1_idx, v1_price)
            
            if not trend_valid:
                result['geometry_checks']['prior_trend'] = {
                    'passed': False,
                    'description': trend_desc
                }
                continue
            
            # ========================================
            # 所有幾何檢查通過！
            # ========================================
            height_diff_pct = abs(v2_price - v1_price) / v1_price
            result['geometry_checks'] = {
                'spacing': {
                    'passed': True,
                    'actual': spacing,
                    'required': min_spacing,
                    'description': f'時間間隔：{spacing}天 ✓'
                },
                'height_consistency': {
                    'passed': True,
                    'actual': height_diff_pct,
                    'required': max_diff,
                    'description': f'高度差異：{height_diff_pct*100:.1f}% ✓'
                },
                'depth': {
                    'passed': True,
                    'actual': depth_pct,
                    'required': min_depth,
                    'description': f'形態深度：{depth_pct*100:.1f}% ✓'
                },
                'prior_trend': {
                    'passed': True,
                    'description': trend_desc
                }
            }
            
            # ========================================
            # 【v4.5.14 新增】提取關鍵點日期
            # ========================================
            try:
                v1_date = df.index[v1_idx].strftime('%Y-%m-%d') if hasattr(df.index[v1_idx], 'strftime') else str(df.index[v1_idx])
                v2_date = df.index[v2_idx].strftime('%Y-%m-%d') if hasattr(df.index[v2_idx], 'strftime') else str(df.index[v2_idx])
                neckline_date = df.index[neckline_idx].strftime('%Y-%m-%d') if hasattr(df.index[neckline_idx], 'strftime') else str(df.index[neckline_idx])
            except:
                v1_date = f'Day {v1_idx}'
                v2_date = f'Day {v2_idx}'
                neckline_date = f'Day {neckline_idx}'
            
            # 關鍵點資訊
            key_points = {
                'left_foot': {
                    'date': v1_date,
                    'price': round(v1_price, 2),
                    'index': v1_idx
                },
                'right_foot': {
                    'date': v2_date,
                    'price': round(v2_price, 2),
                    'index': v2_idx
                },
                'neckline': {
                    'date': neckline_date,
                    'price': round(neckline_price, 2),
                    'index': neckline_idx
                }
            }
            
            # ========================================
            # 檢查頸線突破狀態
            # ========================================
            current_close = df['Close'].iloc[-1]
            current_volume = df['Volume'].iloc[-1]
            ma5_vol = df['MA5_Vol'].iloc[-1]
            
            # 計算目標價與停損
            pattern_height = neckline_price - valley_avg
            target_price = neckline_price + pattern_height
            stop_loss = min(v1_price, v2_price)
            
            # ========================================
            # 【v4.5.16 新增】形態失效檢查 (Pattern Invalidation)
            # ========================================
            # 如果收盤價跌破最低點 (V1 或 V2)，代表 W底失敗，趨勢向下
            if current_close < stop_loss:
                # 形態已失效，跳過此形態
                continue
            
            # ========================================
            # 【v4.5.16 新增】形成超時檢查 (Formation Timeout)
            # ========================================
            days_since_last_point = len(df) - 1 - v2_idx
            if days_since_last_point > self.config.max_formation_days:
                # 形態拖太久（超過30天），效力大幅減弱，視為無效
                continue
            
            # 判斷狀態
            if current_close > neckline_price:
                # ========================================
                # 【v4.5.14 新增】陳舊突破檢查 (Stale Breakout Check)
                # ========================================
                # 檢查 V2 之後到昨天的最高價
                # 如果曾經漲超過頸線很多（>5%），代表早就突破過了
                
                is_stale_breakout = False
                period_high = 0
                stale_threshold = 0.05  # 5% 閾值
                
                # 檢查區間：從 V2 後一天到昨天（不含今天）
                check_start = v2_idx + 1
                check_end = len(df) - 1  # 不含今天
                
                if check_end > check_start:
                    # 取得這段期間的最高價
                    period_high = df['High'].iloc[check_start:check_end].max()
                    
                    # 如果這段期間曾經漲超過頸線很多，代表早就突破過了
                    if period_high > neckline_price * (1 + stale_threshold):
                        is_stale_breakout = True
                
                # ========================================
                # v4.5.11: 時效性濾網 (Distance Check)
                # ========================================
                distance_pct = (current_close - neckline_price) / neckline_price
                max_distance = self.config.max_distance_from_neckline
                
                volume_confirmed = current_volume >= ma5_vol * self.config.volume_confirm_ratio
                
                # ========================================
                # 判斷結果（優先順序：陳舊突破 > 距離過遠 > 確認突破）
                # ========================================
                
                if is_stale_breakout:
                    # 【v4.5.14】這是「回測」或「漲多拉回」，不是新鮮突破
                    result = {
                        'detected': True,
                        'pattern_name': PatternType.DOUBLE_BOTTOM.value,
                        'pattern_type': 'bottom',
                        'status': 'PULLBACK_TEST',  # 新狀態：回測支撐
                        'neckline_price': round(neckline_price, 2),
                        'target_price': round(target_price, 2),
                        'stop_loss': round(stop_loss, 2),
                        'confidence': 35,  # 低信心度
                        'volume_confirmed': volume_confirmed,
                        'description': (
                            f'W底頸線${neckline_price:.2f}回測中（歷史最高曾達${period_high:.2f}），'
                            f'非新鮮突破，觀察支撐是否有效。'
                            f'左腳${v1_price:.2f}({v1_date})，右腳${v2_price:.2f}({v2_date})'
                        ),
                        'signal': 'hold',  # 降級為持有/觀望
                        'score_impact': 3,  # 極低分數影響
                        'geometry_checks': result['geometry_checks'],
                        'distance_from_neckline': round(distance_pct * 100, 1),
                        'key_points': key_points,
                        'period_high': round(period_high, 2),
                        'is_stale_breakout': True
                    }
                    return result
                
                elif distance_pct > max_distance:
                    # 形態已突破一段時間，訊號降級
                    result = {
                        'detected': True,
                        'pattern_name': PatternType.DOUBLE_BOTTOM.value,
                        'pattern_type': 'bottom',
                        'status': 'TARGET_REACHED',  # 已達目標區
                        'neckline_price': round(neckline_price, 2),
                        'target_price': round(target_price, 2),
                        'stop_loss': round(stop_loss, 2),
                        'confidence': 40,  # 降低信心度
                        'volume_confirmed': volume_confirmed,
                        'description': (
                            f'W底已突破一段時間（距頸線+{distance_pct*100:.1f}%），不宜追價。'
                            f'頸線${neckline_price:.2f}，目前${current_close:.2f}。'
                            f'左腳${v1_price:.2f}({v1_date})，右腳${v2_price:.2f}({v2_date})'
                        ),
                        'signal': 'hold',  # 訊號降級：從 buy 降為 hold
                        'score_impact': 5,  # 降低分數影響
                        'geometry_checks': result['geometry_checks'],
                        'distance_from_neckline': round(distance_pct * 100, 1),
                        'key_points': key_points,
                        'is_stale_breakout': False
                    }
                    return result
                
                else:
                    # ========================================
                    # 形態確立：剛突破頸線（距離在合理範圍內且非陳舊突破）
                    # ========================================
                    result = {
                        'detected': True,
                        'pattern_name': PatternType.DOUBLE_BOTTOM.value,
                        'pattern_type': 'bottom',
                        'status': PatternStatus.CONFIRMED_BREAKOUT.value,
                        'neckline_price': round(neckline_price, 2),
                        'target_price': round(target_price, 2),
                        'stop_loss': round(stop_loss, 2),
                        'confidence': 85 if volume_confirmed else 65,
                        'volume_confirmed': volume_confirmed,
                        'description': (
                            f'W底確立！收盤${current_close:.2f}突破頸線${neckline_price:.2f}，'
                            f'距頸線+{distance_pct*100:.1f}%，間隔{spacing}天，深度{depth_pct*100:.1f}%'
                            + ('，量能確認' if volume_confirmed else '，量能不足')
                            + f'。左腳${v1_price:.2f}({v1_date})，右腳${v2_price:.2f}({v2_date})'
                        ),
                        'signal': 'buy',
                        'score_impact': 40 if volume_confirmed else 25,
                        'geometry_checks': result['geometry_checks'],
                        'distance_from_neckline': round(distance_pct * 100, 1),
                        'key_points': key_points,
                        'is_stale_breakout': False
                    }
                    return result
            else:
                # ========================================
                # 形態形成中
                # ========================================
                result = {
                    'detected': True,
                    'pattern_name': PatternType.DOUBLE_BOTTOM.value,
                    'pattern_type': 'bottom',
                    'status': PatternStatus.FORMING.value,
                    'neckline_price': round(neckline_price, 2),
                    'target_price': round(target_price, 2),
                    'stop_loss': round(stop_loss, 2),
                    'confidence': 55,
                    'volume_confirmed': False,
                    'description': (
                        f'W底形成中，頸線${neckline_price:.2f}，突破則確立。'
                        f'間隔{spacing}天，深度{depth_pct*100:.1f}%。'
                        f'左腳${v1_price:.2f}({v1_date})，右腳${v2_price:.2f}({v2_date})'
                    ),
                    'signal': 'neutral',
                    'score_impact': 10,
                    'geometry_checks': result['geometry_checks'],
                    'key_points': key_points
                }
                return result
        
        return result
    
    # ========================================================================
    # V型反轉檢測
    # ========================================================================
    
    def _detect_v_reversal(self, df: pd.DataFrame) -> dict:
        """
        偵測 V型反轉
        
        =====================================================
        嚴格動能條件（v2.0）：
        =====================================================
        
        V型反轉是一種快速、猛烈的反轉形態，與 W底 不同，
        它沒有「二次探底」的過程，而是直接急跌後急漲。
        
        1. 急跌定義 (Sharp Decline)
           - 最低點前 5 天內，跌幅 > 5%
           - 急跌的「斜率」是 V型反轉的關鍵特徵
        
        2. 關鍵 K 棒 (Reversal Candle)
           - 最低點當日或次日，必須出現以下之一：
             a) 長下影線：下影線長度 > 實體 * 2
             b) 吞噬紅K：當日收紅且實體覆蓋前一日
           - 這代表多頭在低點強力介入
        
        3. 快速反彈 (Swift Recovery)
           - 最低點後 3-5 天內
           - 反彈幅度 >= 前波跌幅的 50%
           - 反彈的「速度」是 V型反轉的另一關鍵特徵
        
        4. 前置下跌趨勢
           - 同樣需要確認形態出現在下跌之後
        
        =====================================================
        目標價計算：
        =====================================================
        目標價 = 前波起跌點（即急跌開始前的高點）
        """
        result = {
            'detected': False,
            'pattern_name': PatternType.V_REVERSAL.value,
            'pattern_type': 'bottom',
            'signal': 'neutral',
            'geometry_checks': {}
        }
        
        if len(df) < 20:
            return result
        
        # ========================================
        # Step 1: 尋找最近 20 天內的最低點
        # ========================================
        recent_data = df.tail(20).copy()
        recent_data = recent_data.reset_index(drop=True)
        
        min_idx = recent_data['Low'].idxmin()
        min_price = recent_data['Low'].iloc[min_idx]
        
        # 確保最低點不在邊界（需要前後都有足夠數據）
        if min_idx < 3 or min_idx > len(recent_data) - 3:
            return result
        
        # ========================================
        # 幾何檢查 1: 急跌動能
        # ========================================
        # 計算急跌前 N 天的最高價
        drop_lookback = self.config.v_drop_lookback
        pre_drop_start = max(0, min_idx - drop_lookback)
        pre_drop_high = recent_data['High'].iloc[pre_drop_start:min_idx].max()
        
        # 計算跌幅
        drop_pct = (min_price - pre_drop_high) / pre_drop_high
        min_drop = -self.config.v_min_drop_pct  # 注意：跌幅為負值
        
        if drop_pct > min_drop:  # 跌幅不夠（drop_pct 是負值，越小表示跌越多）
            result['geometry_checks']['sharp_decline'] = {
                'passed': False,
                'actual': abs(drop_pct),
                'required': self.config.v_min_drop_pct,
                'description': f'急跌幅度不足：{abs(drop_pct)*100:.1f}% < {self.config.v_min_drop_pct*100:.0f}%'
            }
            return result
        
        # ========================================
        # 幾何檢查 2: 關鍵 K 棒
        # ========================================
        has_reversal_candle = False
        reversal_candle_type = None
        
        # 檢查最低點當日或次日
        for check_idx in [min_idx, min(min_idx + 1, len(recent_data) - 1)]:
            candle = recent_data.iloc[check_idx]
            
            open_price = candle['Open']
            close_price = candle['Close']
            high_price = candle['High']
            low_price = candle['Low']
            
            body = abs(close_price - open_price)
            lower_shadow = min(open_price, close_price) - low_price
            upper_shadow = high_price - max(open_price, close_price)
            
            # 條件 2a: 長下影線
            min_shadow_ratio = self.config.v_lower_shadow_ratio
            if body > 0 and lower_shadow > body * min_shadow_ratio:
                has_reversal_candle = True
                reversal_candle_type = f'長下影線（下影/實體={lower_shadow/body:.1f}x）'
                break
            
            # 條件 2b: 吞噬紅K（當日收紅且覆蓋前一日）
            if check_idx > 0 and close_price > open_price:  # 收紅
                prev_candle = recent_data.iloc[check_idx - 1]
                # 當日實體完全覆蓋前一日實體
                if (close_price > max(prev_candle['Open'], prev_candle['Close']) and
                    open_price < min(prev_candle['Open'], prev_candle['Close'])):
                    has_reversal_candle = True
                    reversal_candle_type = '吞噬紅K'
                    break
        
        if not has_reversal_candle:
            result['geometry_checks']['reversal_candle'] = {
                'passed': False,
                'description': '未發現關鍵反轉K棒（長下影線或吞噬紅K）'
            }
            return result
        
        # ========================================
        # 幾何檢查 3: 快速反彈
        # ========================================
        # 計算反彈幅度（從最低點到最新收盤價）
        current_close = df['Close'].iloc[-1]
        rebound_pct = (current_close - min_price) / min_price
        
        # 需要收復的最小比例
        min_recovery = self.config.v_min_rebound_recovery
        required_rebound = abs(drop_pct) * min_recovery
        
        if rebound_pct < required_rebound:
            result['geometry_checks']['swift_recovery'] = {
                'passed': False,
                'actual': rebound_pct,
                'required': required_rebound,
                'description': f'反彈幅度不足：{rebound_pct*100:.1f}% < {required_rebound*100:.1f}%（需收復跌幅的{min_recovery*100:.0f}%）'
            }
            return result
        
        # ========================================
        # 幾何檢查 4: 前置下跌趨勢
        # ========================================
        # 使用原始 df 的索引位置來檢查前置趨勢
        original_min_idx = len(df) - len(recent_data) + min_idx
        trend_valid, trend_desc = self._check_prior_downtrend(df, original_min_idx, min_price)
        
        if not trend_valid:
            result['geometry_checks']['prior_trend'] = {
                'passed': False,
                'description': trend_desc
            }
            return result
        
        # ========================================
        # 所有檢查通過！
        # ========================================
        result['geometry_checks'] = {
            'sharp_decline': {
                'passed': True,
                'actual': abs(drop_pct),
                'required': self.config.v_min_drop_pct,
                'description': f'急跌幅度：{abs(drop_pct)*100:.1f}% ✓'
            },
            'reversal_candle': {
                'passed': True,
                'description': f'關鍵K棒：{reversal_candle_type} ✓'
            },
            'swift_recovery': {
                'passed': True,
                'actual': rebound_pct,
                'required': required_rebound,
                'description': f'反彈幅度：{rebound_pct*100:.1f}% ✓'
            },
            'prior_trend': {
                'passed': True,
                'description': trend_desc
            }
        }
        
        # ========================================
        # 計算目標價與停損
        # ========================================
        target_price = pre_drop_high  # 目標回到起跌點
        stop_loss = min_price
        
        # 計算均線（用於判斷確立條件）
        ma10 = df['Close'].rolling(10).mean().iloc[-1] if len(df) >= 10 else current_close
        
        current_volume = df['Volume'].iloc[-1]
        ma5_vol = df['MA5_Vol'].iloc[-1] if 'MA5_Vol' in df.columns else df['Volume'].rolling(5).mean().iloc[-1]
        volume_confirmed = current_volume >= ma5_vol * self.config.volume_confirm_ratio
        
        # 判斷狀態
        # 確立條件：站回起跌點的 80% 或站上 MA10
        recovery_threshold = pre_drop_high * 0.8
        above_ma = current_close > ma10 if pd.notna(ma10) else False
        
        if current_close >= recovery_threshold or (above_ma and rebound_pct >= abs(drop_pct) * 0.6):
            # ========================================
            # 形態確立 — 先檢查目標價是否已被超越
            # ========================================
            # 目標失效判斷：現價已超過起跌點（V型目標）超過 5%
            # 表示買進視窗已過，繼續追高風險極大
            target_already_exceeded = (current_close > target_price * 1.05)

            if target_already_exceeded:
                # 目標早已達成 → 形態失效，降為中性
                result = {
                    'detected': True,
                    'pattern_name': PatternType.V_REVERSAL.value,
                    'pattern_type': 'bottom',
                    'status': 'TARGET_REACHED',          # 自定義狀態：目標已達
                    'neckline_price': round(ma10 if pd.notna(ma10) else pre_drop_high, 2),
                    'target_price': round(target_price, 2),
                    'stop_loss': round(stop_loss, 2),
                    'confidence': 40,
                    'volume_confirmed': volume_confirmed,
                    'description': (
                        f'V型反轉目標已達成（起跌點 ${target_price:.2f}，現價 ${current_close:.2f}）。'
                        f'原始買進視窗已過，追高風險大，不建議進場。'
                    ),
                    'signal': 'neutral',     # 不再輸出買進信號
                    'score_impact': 0,        # 對評分無加分
                    'geometry_checks': result['geometry_checks']
                }
            else:
                result = {
                    'detected': True,
                    'pattern_name': PatternType.V_REVERSAL.value,
                    'pattern_type': 'bottom',
                    'status': PatternStatus.CONFIRMED_BREAKOUT.value,
                    'neckline_price': round(ma10 if pd.notna(ma10) else pre_drop_high, 2),
                    'target_price': round(target_price, 2),
                    'stop_loss': round(stop_loss, 2),
                    'confidence': 80 if volume_confirmed else 60,
                    'volume_confirmed': volume_confirmed,
                    'description': (
                        f'V型反轉確立！急跌{abs(drop_pct)*100:.1f}%後反彈{rebound_pct*100:.1f}%，'
                        f'{reversal_candle_type}'
                        + ('，量能確認' if volume_confirmed else '')
                    ),
                    'signal': 'buy',
                    'score_impact': 35 if volume_confirmed else 20,
                    'geometry_checks': result['geometry_checks']
                }
        else:
            # ========================================
            # 形態形成中
            # ========================================
            result = {
                'detected': True,
                'pattern_name': PatternType.V_REVERSAL.value,
                'pattern_type': 'bottom',
                'status': PatternStatus.FORMING.value,
                'neckline_price': round(ma10 if pd.notna(ma10) else pre_drop_high, 2),
                'target_price': round(target_price, 2),
                'stop_loss': round(stop_loss, 2),
                'confidence': 50,
                'volume_confirmed': False,
                'description': (
                    f'V型反轉形成中，急跌{abs(drop_pct)*100:.1f}%後反彈{rebound_pct*100:.1f}%，'
                    f'需站穩${recovery_threshold:.2f}或均線'
                ),
                'signal': 'neutral',
                'score_impact': 10,
                'geometry_checks': result['geometry_checks']
            }
        
        return result
    
    # ========================================================================
    # 頭肩頂檢測
    # ========================================================================
    
    def _detect_head_shoulders_top(self, df: pd.DataFrame, peaks: List[Tuple], valleys: List[Tuple]) -> dict:
        """
        偵測頭肩頂
        
        =====================================================
        嚴格結構條件（v2.0）：
        =====================================================
        
        頭肩頂是最經典的反轉形態之一，由三個峰組成：
        左肩 (LS) < 頭部 (H) > 右肩 (RS)
        
        1. 前置上漲趨勢 (Prior Uptrend)
           - 左肩前 20 天的最低價需比左肩低 >= 10%
        
        2. 頭部突出 (Head Prominence)
           - 頭部高度 > 左肩高度 * (1 + 3%)
           - 頭部高度 > 右肩高度 * (1 + 3%)
           - 原理：頭部必須明顯高於兩肩，才是有效的頭肩頂
        
        3. 對稱性 (Symmetry)
           - 左肩到頭部的時間距離 = T1
           - 頭部到右肩的時間距離 = T2
           - |T1 - T2| / max(T1, T2) <= 50%
           - 原理：過度歪斜的形態可靠性較低
        
        4. 兩肩高度一致性
           - |LS - RS| / LS <= 5%
           - 原理：標準頭肩頂的左右肩高度應該接近
        
        5. 頸線突破確認
           - 收盤價 < 頸線價格
        
        =====================================================
        頸線計算：
        =====================================================
        頸線 = (左頸線點 + 右頸線點) / 2
        左頸線點 = 左肩與頭部之間的最低谷
        右頸線點 = 頭部與右肩之間的最低谷
        
        =====================================================
        目標價計算（測量法則）：
        =====================================================
        目標價 = 頸線 - (頭部 - 頸線)
        """
        result = {
            'detected': False,
            'pattern_name': PatternType.HEAD_SHOULDERS_TOP.value,
            'pattern_type': 'top',
            'signal': 'neutral',
            'geometry_checks': {}
        }
        
        # 基本條件：需要至少 3 個高點和 2 個低點
        if len(peaks) < 3 or len(valleys) < 2:
            return result
        
        # 取最近的高點進行檢測
        recent_peaks = peaks[-5:] if len(peaks) >= 5 else peaks
        
        for i in range(len(recent_peaks) - 2):
            ls_idx, ls_price = recent_peaks[i]      # 左肩
            h_idx, h_price = recent_peaks[i + 1]    # 頭部
            rs_idx, rs_price = recent_peaks[i + 2]  # 右肩
            
            # ========================================
            # 幾何檢查 1: 頭部是否為最高
            # ========================================
            if not (h_price > ls_price and h_price > rs_price):
                continue
            
            # ========================================
            # 幾何檢查 2: 頭部突出度
            # ========================================
            min_prominence = self.config.hs_min_head_prominence
            
            ls_prominence = (h_price - ls_price) / ls_price
            rs_prominence = (h_price - rs_price) / rs_price
            
            if ls_prominence < min_prominence or rs_prominence < min_prominence:
                result['geometry_checks']['head_prominence'] = {
                    'passed': False,
                    'actual': min(ls_prominence, rs_prominence),
                    'required': min_prominence,
                    'description': f'頭部突出度不足：{min(ls_prominence, rs_prominence)*100:.1f}% < {min_prominence*100:.0f}%'
                }
                continue
            
            # ========================================
            # 幾何檢查 3: 對稱性（時間）
            # ========================================
            t1 = h_idx - ls_idx  # 左肩到頭部的時間
            t2 = rs_idx - h_idx  # 頭部到右肩的時間
            
            max_asymmetry = self.config.hs_max_time_asymmetry
            time_asymmetry = abs(t1 - t2) / max(t1, t2) if max(t1, t2) > 0 else 0
            
            if time_asymmetry > max_asymmetry:
                result['geometry_checks']['time_symmetry'] = {
                    'passed': False,
                    'actual': time_asymmetry,
                    'required': max_asymmetry,
                    'description': f'時間不對稱度過高：{time_asymmetry*100:.1f}% > {max_asymmetry*100:.0f}%（T1={t1}天, T2={t2}天）'
                }
                continue
            
            # ========================================
            # 幾何檢查 4: 兩肩高度一致性
            # ========================================
            max_shoulder_diff = self.config.hs_max_shoulder_diff
            shoulder_diff = abs(ls_price - rs_price) / ls_price
            
            if shoulder_diff > max_shoulder_diff:
                result['geometry_checks']['shoulder_consistency'] = {
                    'passed': False,
                    'actual': shoulder_diff,
                    'required': max_shoulder_diff,
                    'description': f'兩肩高度差異過大：{shoulder_diff*100:.1f}% > {max_shoulder_diff*100:.0f}%'
                }
                continue
            
            # ========================================
            # 找頸線
            # ========================================
            left_valleys = [v for v in valleys if ls_idx < v[0] < h_idx]
            right_valleys = [v for v in valleys if h_idx < v[0] < rs_idx]
            
            if not left_valleys or not right_valleys:
                continue
            
            left_neckline_idx, left_neckline_price = min(left_valleys, key=lambda x: x[1])
            right_neckline_idx, right_neckline_price = min(right_valleys, key=lambda x: x[1])
            
            # 頸線取兩點平均
            neckline_price = (left_neckline_price + right_neckline_price) / 2
            
            # ========================================
            # 幾何檢查 5: 前置上漲趨勢
            # ========================================
            trend_valid, trend_desc = self._check_prior_uptrend(df, ls_idx, ls_price)
            
            if not trend_valid:
                result['geometry_checks']['prior_trend'] = {
                    'passed': False,
                    'description': trend_desc
                }
                continue
            
            # ========================================
            # 所有幾何檢查通過！
            # ========================================
            result['geometry_checks'] = {
                'head_prominence': {
                    'passed': True,
                    'actual': min(ls_prominence, rs_prominence),
                    'required': min_prominence,
                    'description': f'頭部突出度：{min(ls_prominence, rs_prominence)*100:.1f}% ✓'
                },
                'time_symmetry': {
                    'passed': True,
                    'actual': time_asymmetry,
                    'required': max_asymmetry,
                    'description': f'時間對稱性：T1={t1}天, T2={t2}天, 差異{time_asymmetry*100:.1f}% ✓'
                },
                'shoulder_consistency': {
                    'passed': True,
                    'actual': shoulder_diff,
                    'required': max_shoulder_diff,
                    'description': f'兩肩高度差異：{shoulder_diff*100:.1f}% ✓'
                },
                'prior_trend': {
                    'passed': True,
                    'description': trend_desc
                }
            }
            
            # ========================================
            # 【v4.5.14 新增】提取關鍵點日期
            # ========================================
            try:
                ls_date = df.index[ls_idx].strftime('%Y-%m-%d') if hasattr(df.index[ls_idx], 'strftime') else str(df.index[ls_idx])
                h_date = df.index[h_idx].strftime('%Y-%m-%d') if hasattr(df.index[h_idx], 'strftime') else str(df.index[h_idx])
                rs_date = df.index[rs_idx].strftime('%Y-%m-%d') if hasattr(df.index[rs_idx], 'strftime') else str(df.index[rs_idx])
            except:
                ls_date = f'Day {ls_idx}'
                h_date = f'Day {h_idx}'
                rs_date = f'Day {rs_idx}'
            
            # 關鍵點資訊
            key_points = {
                'left_shoulder': {
                    'date': ls_date,
                    'price': round(ls_price, 2),
                    'index': ls_idx
                },
                'head': {
                    'date': h_date,
                    'price': round(h_price, 2),
                    'index': h_idx
                },
                'right_shoulder': {
                    'date': rs_date,
                    'price': round(rs_price, 2),
                    'index': rs_idx
                },
                'neckline': {
                    'price': round(neckline_price, 2)
                }
            }
            
            # ========================================
            # 檢查頸線突破狀態
            # ========================================
            current_close = df['Close'].iloc[-1]
            current_volume = df['Volume'].iloc[-1]
            ma5_vol = df['MA5_Vol'].iloc[-1]
            
            # 計算目標價與停損
            pattern_height = h_price - neckline_price
            target_price = neckline_price - pattern_height
            stop_loss = h_price
            
            # ========================================
            # 【v4.5.16 新增】形態失效檢查 (Pattern Invalidation)
            # ========================================
            # 如果收盤價漲破頭部最高點，代表頭肩頂失敗，趨勢向上
            if current_close > stop_loss:
                # 形態已失效，跳過此形態
                continue
            
            # ========================================
            # 【v4.5.16 新增】形成超時檢查 (Formation Timeout)
            # ========================================
            days_since_last_point = len(df) - 1 - rs_idx
            if days_since_last_point > self.config.max_formation_days:
                # 形態拖太久（超過30天），效力大幅減弱，視為無效
                continue
            
            # 判斷狀態
            if current_close < neckline_price:
                # ========================================
                # 【v4.5.14 新增】陳舊跌破檢查 (Stale Breakdown Check)
                # ========================================
                is_stale_breakdown = False
                period_low = float('inf')
                stale_threshold = 0.05
                
                check_start = rs_idx + 1
                check_end = len(df) - 1
                
                if check_end > check_start:
                    period_low = df['Low'].iloc[check_start:check_end].min()
                    if period_low < neckline_price * (1 - stale_threshold):
                        is_stale_breakdown = True
                
                # ========================================
                # v4.5.11: 時效性濾網 (Recency Filter)
                # ========================================
                distance_pct = (neckline_price - current_close) / neckline_price
                max_distance = self.config.max_distance_from_neckline
                
                volume_confirmed = current_volume >= ma5_vol * self.config.volume_confirm_ratio
                
                if is_stale_breakdown:
                    result = {
                        'detected': True,
                        'pattern_name': PatternType.HEAD_SHOULDERS_TOP.value,
                        'pattern_type': 'top',
                        'status': 'PULLBACK_TEST',
                        'neckline_price': round(neckline_price, 2),
                        'target_price': round(target_price, 2),
                        'stop_loss': round(stop_loss, 2),
                        'confidence': 35,
                        'volume_confirmed': volume_confirmed,
                        'description': (
                            f'頭肩頂頸線${neckline_price:.2f}反彈測試中（歷史最低曾達${period_low:.2f}），'
                            f'非新鮮跌破。左肩${ls_price:.2f}({ls_date})，頭${h_price:.2f}({h_date})，右肩${rs_price:.2f}({rs_date})'
                        ),
                        'signal': 'hold',
                        'score_impact': -3,
                        'geometry_checks': result['geometry_checks'],
                        'distance_from_neckline': round(-distance_pct * 100, 1),
                        'key_points': key_points,
                        'period_low': round(period_low, 2),
                        'is_stale_breakdown': True
                    }
                    return result
                
                elif distance_pct > max_distance:
                    result = {
                        'detected': True,
                        'pattern_name': PatternType.HEAD_SHOULDERS_TOP.value,
                        'pattern_type': 'top',
                        'status': 'TARGET_REACHED',
                        'neckline_price': round(neckline_price, 2),
                        'target_price': round(target_price, 2),
                        'stop_loss': round(stop_loss, 2),
                        'confidence': 40,
                        'volume_confirmed': volume_confirmed,
                        'description': (
                            f'頭肩頂已跌破一段時間（距頸線-{distance_pct*100:.1f}%），不宜追空。'
                            f'左肩${ls_price:.2f}({ls_date})，頭${h_price:.2f}({h_date})，右肩${rs_price:.2f}({rs_date})'
                        ),
                        'signal': 'hold',
                        'score_impact': -5,
                        'geometry_checks': result['geometry_checks'],
                        'distance_from_neckline': round(-distance_pct * 100, 1),
                        'key_points': key_points,
                        'is_stale_breakdown': False
                    }
                    return result
                
                else:
                    # ========================================
                    # 形態確立：剛跌破頸線
                    # ========================================
                    result = {
                        'detected': True,
                        'pattern_name': PatternType.HEAD_SHOULDERS_TOP.value,
                        'pattern_type': 'top',
                        'status': PatternStatus.CONFIRMED_BREAKDOWN.value,
                        'neckline_price': round(neckline_price, 2),
                        'target_price': round(target_price, 2),
                        'stop_loss': round(stop_loss, 2),
                        'confidence': 90 if volume_confirmed else 70,
                        'volume_confirmed': volume_confirmed,
                        'description': (
                            f'頭肩頂確立！跌破頸線${neckline_price:.2f}，'
                            f'距頸線-{distance_pct*100:.1f}%。'
                            f'左肩${ls_price:.2f}({ls_date})，頭${h_price:.2f}({h_date})，右肩${rs_price:.2f}({rs_date})'
                            + ('，量能確認' if volume_confirmed else '')
                        ),
                        'signal': 'sell',
                        'score_impact': -45 if volume_confirmed else -30,
                        'geometry_checks': result['geometry_checks'],
                        'distance_from_neckline': round(-distance_pct * 100, 1),
                        'key_points': key_points,
                        'is_stale_breakdown': False
                    }
                    return result
            else:
                # ========================================
                # 形態形成中
                # ========================================
                result = {
                    'detected': True,
                    'pattern_name': PatternType.HEAD_SHOULDERS_TOP.value,
                    'pattern_type': 'top',
                    'status': PatternStatus.FORMING.value,
                    'neckline_price': round(neckline_price, 2),
                    'target_price': round(target_price, 2),
                    'stop_loss': round(stop_loss, 2),
                    'confidence': 60,
                    'volume_confirmed': False,
                    'description': (
                        f'頭肩頂形成中，頸線${neckline_price:.2f}，跌破則確立。'
                        f'左肩${ls_price:.2f}({ls_date})，頭${h_price:.2f}({h_date})，右肩${rs_price:.2f}({rs_date})'
                    ),
                    'signal': 'neutral',
                    'score_impact': -15,
                    'geometry_checks': result['geometry_checks'],
                    'key_points': key_points
                }
                return result
        
        return result
    
    # ========================================================================
    # 頭肩底檢測
    # ========================================================================
    
    def _detect_head_shoulders_bottom(self, df: pd.DataFrame, peaks: List[Tuple], valleys: List[Tuple]) -> dict:
        """
        偵測頭肩底
        
        =====================================================
        嚴格結構條件（v2.0）：
        =====================================================
        
        頭肩底是頭肩頂的鏡像，由三個谷組成：
        左肩 (LS) > 頭部 (H) < 右肩 (RS)
        
        1. 前置下跌趨勢 (Prior Downtrend)
           - 左肩前 20 天的最高價需比左肩高 >= 10%
        
        2. 頭部突出 (Head Prominence)
           - 頭部低於左肩至少 3%
           - 頭部低於右肩至少 3%
           - 原理：頭部必須明顯低於兩肩
        
        3. 對稱性 (Symmetry)
           - 左肩到頭部的時間距離 = T1
           - 頭部到右肩的時間距離 = T2
           - |T1 - T2| / max(T1, T2) <= 50%
        
        4. 兩肩高度一致性
           - |LS - RS| / LS <= 5%
        
        5. 頸線突破確認
           - 收盤價 > 頸線價格
        
        =====================================================
        頸線計算：
        =====================================================
        頸線 = (左頸線點 + 右頸線點) / 2
        左頸線點 = 左肩與頭部之間的最高峰
        右頸線點 = 頭部與右肩之間的最高峰
        
        =====================================================
        目標價計算（測量法則）：
        =====================================================
        目標價 = 頸線 + (頸線 - 頭部)
        """
        result = {
            'detected': False,
            'pattern_name': PatternType.HEAD_SHOULDERS_BOTTOM.value,
            'pattern_type': 'bottom',
            'signal': 'neutral',
            'geometry_checks': {}
        }
        
        # 基本條件：需要至少 3 個低點和 2 個高點
        if len(valleys) < 3 or len(peaks) < 2:
            return result
        
        # 取最近的低點進行檢測
        recent_valleys = valleys[-5:] if len(valleys) >= 5 else valleys
        
        for i in range(len(recent_valleys) - 2):
            ls_idx, ls_price = recent_valleys[i]      # 左肩
            h_idx, h_price = recent_valleys[i + 1]    # 頭部
            rs_idx, rs_price = recent_valleys[i + 2]  # 右肩
            
            # ========================================
            # 幾何檢查 1: 頭部是否為最低
            # ========================================
            if not (h_price < ls_price and h_price < rs_price):
                continue
            
            # ========================================
            # 幾何檢查 2: 頭部突出度
            # ========================================
            min_prominence = self.config.hs_min_head_prominence
            
            # 對於底部形態，突出度 = (肩部 - 頭部) / 頭部
            ls_prominence = (ls_price - h_price) / h_price
            rs_prominence = (rs_price - h_price) / h_price
            
            if ls_prominence < min_prominence or rs_prominence < min_prominence:
                result['geometry_checks']['head_prominence'] = {
                    'passed': False,
                    'actual': min(ls_prominence, rs_prominence),
                    'required': min_prominence,
                    'description': f'頭部突出度不足：{min(ls_prominence, rs_prominence)*100:.1f}% < {min_prominence*100:.0f}%'
                }
                continue
            
            # ========================================
            # 幾何檢查 3: 對稱性（時間）
            # ========================================
            t1 = h_idx - ls_idx  # 左肩到頭部的時間
            t2 = rs_idx - h_idx  # 頭部到右肩的時間
            
            max_asymmetry = self.config.hs_max_time_asymmetry
            time_asymmetry = abs(t1 - t2) / max(t1, t2) if max(t1, t2) > 0 else 0
            
            if time_asymmetry > max_asymmetry:
                result['geometry_checks']['time_symmetry'] = {
                    'passed': False,
                    'actual': time_asymmetry,
                    'required': max_asymmetry,
                    'description': f'時間不對稱度過高：{time_asymmetry*100:.1f}% > {max_asymmetry*100:.0f}%（T1={t1}天, T2={t2}天）'
                }
                continue
            
            # ========================================
            # 幾何檢查 4: 兩肩高度一致性
            # ========================================
            max_shoulder_diff = self.config.hs_max_shoulder_diff
            shoulder_diff = abs(ls_price - rs_price) / ls_price
            
            if shoulder_diff > max_shoulder_diff:
                result['geometry_checks']['shoulder_consistency'] = {
                    'passed': False,
                    'actual': shoulder_diff,
                    'required': max_shoulder_diff,
                    'description': f'兩肩高度差異過大：{shoulder_diff*100:.1f}% > {max_shoulder_diff*100:.0f}%'
                }
                continue
            
            # ========================================
            # 找頸線
            # ========================================
            left_peaks = [p for p in peaks if ls_idx < p[0] < h_idx]
            right_peaks = [p for p in peaks if h_idx < p[0] < rs_idx]
            
            if not left_peaks or not right_peaks:
                continue
            
            left_neckline_idx, left_neckline_price = max(left_peaks, key=lambda x: x[1])
            right_neckline_idx, right_neckline_price = max(right_peaks, key=lambda x: x[1])
            
            # 頸線取兩點平均
            neckline_price = (left_neckline_price + right_neckline_price) / 2
            
            # ========================================
            # 幾何檢查 5: 前置下跌趨勢
            # ========================================
            trend_valid, trend_desc = self._check_prior_downtrend(df, ls_idx, ls_price)
            
            if not trend_valid:
                result['geometry_checks']['prior_trend'] = {
                    'passed': False,
                    'description': trend_desc
                }
                continue
            
            # ========================================
            # 所有幾何檢查通過！
            # ========================================
            result['geometry_checks'] = {
                'head_prominence': {
                    'passed': True,
                    'actual': min(ls_prominence, rs_prominence),
                    'required': min_prominence,
                    'description': f'頭部突出度：{min(ls_prominence, rs_prominence)*100:.1f}% ✓'
                },
                'time_symmetry': {
                    'passed': True,
                    'actual': time_asymmetry,
                    'required': max_asymmetry,
                    'description': f'時間對稱性：T1={t1}天, T2={t2}天, 差異{time_asymmetry*100:.1f}% ✓'
                },
                'shoulder_consistency': {
                    'passed': True,
                    'actual': shoulder_diff,
                    'required': max_shoulder_diff,
                    'description': f'兩肩高度差異：{shoulder_diff*100:.1f}% ✓'
                },
                'prior_trend': {
                    'passed': True,
                    'description': trend_desc
                }
            }
            
            # ========================================
            # 【v4.5.14 新增】提取關鍵點日期
            # ========================================
            try:
                ls_date = df.index[ls_idx].strftime('%Y-%m-%d') if hasattr(df.index[ls_idx], 'strftime') else str(df.index[ls_idx])
                h_date = df.index[h_idx].strftime('%Y-%m-%d') if hasattr(df.index[h_idx], 'strftime') else str(df.index[h_idx])
                rs_date = df.index[rs_idx].strftime('%Y-%m-%d') if hasattr(df.index[rs_idx], 'strftime') else str(df.index[rs_idx])
            except:
                ls_date = f'Day {ls_idx}'
                h_date = f'Day {h_idx}'
                rs_date = f'Day {rs_idx}'
            
            # 關鍵點資訊
            key_points = {
                'left_shoulder': {
                    'date': ls_date,
                    'price': round(ls_price, 2),
                    'index': ls_idx
                },
                'head': {
                    'date': h_date,
                    'price': round(h_price, 2),
                    'index': h_idx
                },
                'right_shoulder': {
                    'date': rs_date,
                    'price': round(rs_price, 2),
                    'index': rs_idx
                },
                'neckline': {
                    'price': round(neckline_price, 2)
                }
            }
            
            # ========================================
            # 檢查頸線突破狀態
            # ========================================
            current_close = df['Close'].iloc[-1]
            current_volume = df['Volume'].iloc[-1]
            ma5_vol = df['MA5_Vol'].iloc[-1]
            
            # 計算目標價與停損
            pattern_height = neckline_price - h_price
            target_price = neckline_price + pattern_height
            stop_loss = h_price
            
            # ========================================
            # 【v4.5.16 新增】形態失效檢查 (Pattern Invalidation)
            # ========================================
            # 如果收盤價跌破頭部最低點，代表頭肩底失敗，趨勢向下
            if current_close < stop_loss:
                # 形態已失效，跳過此形態
                continue
            
            # ========================================
            # 【v4.5.16 新增】形成超時檢查 (Formation Timeout)
            # ========================================
            days_since_last_point = len(df) - 1 - rs_idx
            if days_since_last_point > self.config.max_formation_days:
                # 形態拖太久（超過30天），效力大幅減弱，視為無效
                continue
            
            # 判斷狀態
            if current_close > neckline_price:
                # ========================================
                # 【v4.5.14 新增】陳舊突破檢查 (Stale Breakout Check)
                # ========================================
                is_stale_breakout = False
                period_high = 0
                stale_threshold = 0.05
                
                check_start = rs_idx + 1
                check_end = len(df) - 1
                
                if check_end > check_start:
                    period_high = df['High'].iloc[check_start:check_end].max()
                    if period_high > neckline_price * (1 + stale_threshold):
                        is_stale_breakout = True
                
                # ========================================
                # v4.5.11: 時效性濾網 (Recency Filter)
                # ========================================
                distance_pct = (current_close - neckline_price) / neckline_price
                max_distance = self.config.max_distance_from_neckline
                
                volume_confirmed = current_volume >= ma5_vol * self.config.volume_confirm_ratio
                
                if is_stale_breakout:
                    result = {
                        'detected': True,
                        'pattern_name': PatternType.HEAD_SHOULDERS_BOTTOM.value,
                        'pattern_type': 'bottom',
                        'status': 'PULLBACK_TEST',
                        'neckline_price': round(neckline_price, 2),
                        'target_price': round(target_price, 2),
                        'stop_loss': round(stop_loss, 2),
                        'confidence': 35,
                        'volume_confirmed': volume_confirmed,
                        'description': (
                            f'頭肩底頸線${neckline_price:.2f}回測中（歷史最高曾達${period_high:.2f}），'
                            f'非新鮮突破。左肩${ls_price:.2f}({ls_date})，頭${h_price:.2f}({h_date})，右肩${rs_price:.2f}({rs_date})'
                        ),
                        'signal': 'hold',
                        'score_impact': 3,
                        'geometry_checks': result['geometry_checks'],
                        'distance_from_neckline': round(distance_pct * 100, 1),
                        'key_points': key_points,
                        'period_high': round(period_high, 2),
                        'is_stale_breakout': True
                    }
                    return result
                
                elif distance_pct > max_distance:
                    result = {
                        'detected': True,
                        'pattern_name': PatternType.HEAD_SHOULDERS_BOTTOM.value,
                        'pattern_type': 'bottom',
                        'status': 'TARGET_REACHED',
                        'neckline_price': round(neckline_price, 2),
                        'target_price': round(target_price, 2),
                        'stop_loss': round(stop_loss, 2),
                        'confidence': 40,
                        'volume_confirmed': volume_confirmed,
                        'description': (
                            f'頭肩底已突破一段時間（距頸線+{distance_pct*100:.1f}%），不宜追價。'
                            f'左肩${ls_price:.2f}({ls_date})，頭${h_price:.2f}({h_date})，右肩${rs_price:.2f}({rs_date})'
                        ),
                        'signal': 'hold',
                        'score_impact': 5,
                        'geometry_checks': result['geometry_checks'],
                        'distance_from_neckline': round(distance_pct * 100, 1),
                        'key_points': key_points,
                        'is_stale_breakout': False
                    }
                    return result
                
                else:
                    # ========================================
                    # 形態確立：剛突破頸線
                    # ========================================
                    result = {
                        'detected': True,
                        'pattern_name': PatternType.HEAD_SHOULDERS_BOTTOM.value,
                        'pattern_type': 'bottom',
                        'status': PatternStatus.CONFIRMED_BREAKOUT.value,
                        'neckline_price': round(neckline_price, 2),
                        'target_price': round(target_price, 2),
                        'stop_loss': round(stop_loss, 2),
                        'confidence': 90 if volume_confirmed else 70,
                        'volume_confirmed': volume_confirmed,
                        'description': (
                            f'頭肩底確立！突破頸線${neckline_price:.2f}，'
                            f'距頸線+{distance_pct*100:.1f}%。'
                            f'左肩${ls_price:.2f}({ls_date})，頭${h_price:.2f}({h_date})，右肩${rs_price:.2f}({rs_date})'
                            + ('，量能確認' if volume_confirmed else '')
                        ),
                        'signal': 'buy',
                        'score_impact': 45 if volume_confirmed else 30,
                        'geometry_checks': result['geometry_checks'],
                        'distance_from_neckline': round(distance_pct * 100, 1),
                        'key_points': key_points,
                        'is_stale_breakout': False
                    }
                    return result
            else:
                # ========================================
                # 形態形成中
                # ========================================
                result = {
                    'detected': True,
                    'pattern_name': PatternType.HEAD_SHOULDERS_BOTTOM.value,
                    'pattern_type': 'bottom',
                    'status': PatternStatus.FORMING.value,
                    'neckline_price': round(neckline_price, 2),
                    'target_price': round(target_price, 2),
                    'stop_loss': round(stop_loss, 2),
                    'confidence': 60,
                    'volume_confirmed': False,
                    'description': (
                        f'頭肩底形成中，頸線${neckline_price:.2f}，突破則確立。'
                        f'左肩${ls_price:.2f}({ls_date})，頭${h_price:.2f}({h_date})，右肩${rs_price:.2f}({rs_date})'
                    ),
                    'signal': 'neutral',
                    'score_impact': 15,
                    'geometry_checks': result['geometry_checks'],
                    'key_points': key_points
                }
                return result
        
        return result


# ============================================================================
# 向後兼容性：保持原有的類常量和靜態方法接口
# ============================================================================

# 為了兼容現有代碼，保留原有的類常量定義
PatternAnalyzer.PATTERN_DOUBLE_TOP = PatternType.DOUBLE_TOP.value
PatternAnalyzer.PATTERN_DOUBLE_BOTTOM = PatternType.DOUBLE_BOTTOM.value
PatternAnalyzer.PATTERN_HEAD_SHOULDERS_TOP = PatternType.HEAD_SHOULDERS_TOP.value
PatternAnalyzer.PATTERN_HEAD_SHOULDERS_BOTTOM = PatternType.HEAD_SHOULDERS_BOTTOM.value
PatternAnalyzer.PATTERN_V_REVERSAL = PatternType.V_REVERSAL.value

PatternAnalyzer.STATUS_FORMING = PatternStatus.FORMING.value
PatternAnalyzer.STATUS_CONFIRMED_BREAKOUT = PatternStatus.CONFIRMED_BREAKOUT.value
PatternAnalyzer.STATUS_CONFIRMED_BREAKDOWN = PatternStatus.CONFIRMED_BREAKDOWN.value
PatternAnalyzer.STATUS_FAILED = PatternStatus.FAILED.value

# 預設參數（為了向後兼容）
PatternAnalyzer.DEFAULT_LOOKBACK = 60
PatternAnalyzer.DEFAULT_PEAK_THRESHOLD = 0.03
PatternAnalyzer.DEFAULT_VOLUME_CONFIRM = 1.0


# ============================================================================
# BranchAnalyzer — 分點分析框架 v1.0 (Stub)
# ============================================================================
#
# 設計說明：
#   分點分析是台灣市場特有的 alpha 因子。主力券商分點持續買進，
#   代表知情交易者（informed trader）正在建倉，是趨勢啟動前的領先指標。
#
# 資料結構規格（供外部餵入 result['branch_analysis']）：
#   {
#     'available':            bool,     # 是否有分點資料
#     'main_branch_name':     str,      # 主力分點名稱（如「富邦-古亭」）
#     'main_branch_buy_days': int,      # 主力分點連續買進天數
#     'main_branch_net_lot':  int,      # 主力分點淨買張數（正=買，負=賣）
#     'branch_concentration': float,    # 籌碼集中度（主力/全體，0~1）
#     'multi_branch_buy':     bool,     # 是否多個知名分點同步買進
#     'data_date':            str,      # 資料日期（'YYYY-MM-DD'）
#     'source':               str,      # 資料來源（'goodinfo'/'cmoney' 等）
#   }
#
# Layer 3 整合邏輯（decision_engine.py score_timing() 已實裝）：
#   _branch_trigger:  main_branch_buy_days ≥ 3 → 分點觸發
#   _branch_strong:   main_branch_buy_days ≥ 5 → 強分點（可升 A）
#
# 實作路線圖（待接上真實爬蟲/API）：
#   1. 串接 GoodInfo 或 Fugle 分點資料 API
#   2. 以 TWSE 代碼 → 查詢近 N 天各分點買賣超
#   3. 識別「主力分點」：連買天數最多 / 淨買張最大的單一分點
#   4. 計算 branch_concentration（主力張數 / 當日全體成交張數）
#   5. 偵測多空分點同步（multi_branch_buy）
#   6. 輸出標準化 dict 填入 result['branch_analysis']
# ============================================================================

class BranchAnalyzer:
    """
    分點分析器（台灣市場主力分點追蹤）

    目前為 Stub 框架：analyze() 回傳 available=False 的空結構，
    等待外部資料源接入後補充實作。
    實際資料由外部爬蟲或 API 產生後，填入 result['branch_analysis'] 即可，
    decision_engine.py 的 Layer 3 已可直接讀取。
    """

    # 觸發門檻（與 decision_engine.py 中的邏輯一致）
    STRONG_BUY_DAYS = 5   # 強分點：連買 ≥5 天 → B→A 升級
    WEAK_BUY_DAYS   = 3   # 弱分點：連買 ≥3 天 → 觸發訊號

    @staticmethod
    def empty_result() -> dict:
        """回傳空的分點分析結構（資料不可用時使用）"""
        return {
            'available':            False,
            'main_branch_name':     None,
            'main_branch_buy_days': 0,
            'main_branch_net_lot':  0,
            'branch_concentration': 0.0,
            'multi_branch_buy':     False,
            'data_date':            None,
            'source':               None,
        }

    @staticmethod
    def analyze(symbol: str, df=None, branch_data: dict = None) -> dict:
        """
        執行分點分析。

        Args:
            symbol:      股票代碼（如 '2330'）
            df:          日K DataFrame（目前 stub 不使用）
            branch_data: 外部傳入的分點原始資料（dict），若 None 則回傳空結構

        Returns:
            dict:  符合 result['branch_analysis'] 規格的標準化結構
        """
        if branch_data is None:
            # Stub：尚未接上資料源，回傳空結構
            return BranchAnalyzer.empty_result()

        # ── 外部資料正規化 ──────────────────────────────────────────
        # 允許外部傳入已整理好的 dict，做基本驗證後直接返回
        main_name     = branch_data.get('main_branch_name', '') or ''
        main_buy_days = int(branch_data.get('main_branch_buy_days', 0) or 0)
        main_net_lot  = int(branch_data.get('main_branch_net_lot',  0) or 0)
        concentration = float(branch_data.get('branch_concentration', 0.0) or 0.0)
        multi_buy     = bool(branch_data.get('multi_branch_buy', False))
        data_date     = branch_data.get('data_date', '')
        source        = branch_data.get('source', 'external')

        if not main_name or main_buy_days < 1:
            return BranchAnalyzer.empty_result()

        return {
            'available':            True,
            'main_branch_name':     main_name,
            'main_branch_buy_days': main_buy_days,
            'main_branch_net_lot':  main_net_lot,
            'branch_concentration': round(concentration, 4),
            'multi_branch_buy':     multi_buy,
            'data_date':            data_date,
            'source':               source,
        }

    @staticmethod
    def describe(branch_result: dict) -> str:
        """產生人類可讀的分點摘要，供 UI 顯示"""
        if not branch_result.get('available'):
            return '無分點資料'
        name     = branch_result['main_branch_name']
        days     = branch_result['main_branch_buy_days']
        net_lot  = branch_result['main_branch_net_lot']
        conc     = branch_result['branch_concentration'] * 100
        strength = '強勢' if days >= BranchAnalyzer.STRONG_BUY_DAYS else '持續'
        direction = '買超' if net_lot >= 0 else '賣超'
        return (
            f'{name} {strength}進場 連{days}天'
            f'（淨{direction}{abs(net_lot):,}張'
            f'，集中度{conc:.1f}%）'
        )


if __name__ == '__main__':
    # 簡單測試
    print("PatternAnalyzer v2.0 - 嚴格形態識別分析器")
    print("=" * 60)
    print("支援的形態：")
    print("  頂部形態：M頭、頭肩頂")
    print("  底部形態：W底、頭肩底、V型反轉")
    print()
    print("嚴格過濾條件：")
    print("  1. 前置趨勢檢查（10%）")
    print("  2. 時間間隔（>=15天）")
    print("  3. 高度一致性（±3%）")
    print("  4. 形態深度（>=5%）")
    print("  5. 頭部突出度（>=3%）")
    print("  6. 時間對稱性（<=50%差異）")
    print("=" * 60)
