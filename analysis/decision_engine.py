"""
decision_engine.py - 三層決策引擎 v3.0

設計原則：
1. 三層各自獨立，有否決門檻，不通過就不進下一層
2. 各層評分不跨層混合（方向分不加進位置分，位置分不加進時機分）
3. 買點分 A/B/C 三級，而非 0-100 的模糊分數
4. 賣點分 防守 / 停利 / 反轉 三類，清楚好執行
5. 籌碼作為排序器/過濾器，不決定買賣

v3.0 重大更新：
- Layer 1：加入 MA20 斜率修正 + 相對強度（RS）加分
- Layer 2：籌碼去化條件可解除過熱天花板（強勢股不被誤殺）
           深度超跌 + 空頭趨勢 → 強制否決（禁止接刀）
           RR fallback 無效數據時懲罰扣分
- Layer 3：A 級改為雙因子交叉確認
           籌碼連買 / 分點介入可作為 B→A 升級條件
           C 級門檻收緊（需次要條件）
- 籌碼資料：修正 chip_analysis → chip_flow 的 key 對應問題

Layer 1 - 方向分 (Direction):
    問題：這檔股票現在的大方向值不值得看？
    否決門檻：< 40 → 直接跳過，不看下去

Layer 2 - 位置分 (Position):
    問題：現在的位置是不是適合進場？
    否決門檻：< 40 → 不買，等位置改善

Layer 3 - 時機分 (Timing):
    問題：今天是不是該動手？
    輸出：A級主攻 / B級追蹤 / C級觀察 / X無訊號
"""

from __future__ import annotations


# ============================================================================
# 內部工具：統一取籌碼資料（修正 chip_analysis / chip_flow key 不一致）
# ============================================================================

def _get_chip(result: dict) -> dict:
    """
    統一取籌碼資料的入口。
    result 中可能是 'chip_analysis'（舊版）或 'chip_flow'（新版），
    此函式同時相容兩個 key，並做欄位正規化。
    """
    chip = result.get('chip_analysis') or result.get('chip_flow') or {}
    if not chip or not chip.get('available'):
        return {'available': False}

    # 欄位正規化：chip_flow 使用 foreign_consecutive_days，
    # chip_analysis 使用 consecutive_buy_days
    out = dict(chip)
    if 'consecutive_buy_days' not in out:
        fd = out.get('foreign_consecutive_days', 0) or 0
        td = out.get('trust_consecutive_days', 0) or 0
        # 外資+投信同向買超天數取較大值
        buy_days  = max(fd if fd > 0 else 0, td if td > 0 else 0)
        sell_days = max(abs(fd) if fd < 0 else 0, abs(td) if td < 0 else 0)
        out['consecutive_buy_days']  = buy_days
        out['consecutive_sell_days'] = sell_days

    if 'foreign_net' not in out:
        out['foreign_net'] = out.get('foreign_net', 0) or 0

    return out


# ============================================================================
# 主引擎
# ============================================================================

class ThreeLayerEngine:

    # 各層否決門檻
    DIRECTION_VETO = 40
    POSITION_VETO  = 40

    # ─── A2 共用：動能模式判定 ───────────────────────────────────────────────
    @staticmethod
    def _is_momentum(result: dict) -> bool:
        """
        動能模式判定（RS 領先 + 多頭排列）。L2/L3/賣訊/覆蓋層共用同一判斷，
        確保「強勢領漲股」在各層的處理一致：正乖離/RSI 偏高/過熱賣訊一律
        降級為風險提示，而非把訊號翻成觀望或賣出。

        條件：
          RS 領先大盤（rs_score≥65 或 vs_market≥3%）
          且 多頭排列（ma5/ma20/ma60 + 現價 的 3 層上升結構，bull_count≥3）
        """
        tech    = result.get('technical', {}) or {}
        current = result.get('current_price', 0) or 0
        rs_data = result.get('relative_strength', {}) or {}

        rs_score  = rs_data.get('rs_score', 50) or 50
        vs_market = rs_data.get('vs_market', 0) or 0

        ma5_m  = tech.get('ma5',  current) or current
        ma20_m = tech.get('ma20', current) or current
        ma60_m = tech.get('ma60', current) or current
        if isinstance(ma5_m,  str): ma5_m  = current
        if isinstance(ma20_m, str): ma20_m = current
        if isinstance(ma60_m, str): ma60_m = current

        bull_count = sum([
            current > ma20_m,
            ma20_m  > ma60_m,
            current > ma60_m,
            ma5_m   > ma20_m,
        ])
        rs_lead = (rs_score >= 65) or (vs_market >= 3)
        return bool(rs_lead and bull_count >= 3)

    # ─── 主入口 ──────────────────────────────────────────────────────────────

    @staticmethod
    def analyze(result: dict) -> dict:
        """
        主入口：取代舊版 DecisionMatrix.analyze(result)

        v2.1 新增：大盤濾網（Market Regime Gate）
        - result['market_regime'] 由 QuickAnalyzer 計算並傳入，無需重複抓取
        - 大盤空頭時壓制 A 級信號，避免逆勢操作
        - 大盤震盪時 A 級降為 B 級，謹慎操作

        Returns:
            dict: 與舊版格式兼容的決策結果，並額外包含 three_layer 詳細分解
        """
        try:
            # ── 大盤濾網（前置過濾）──────────────────────────────
            market_regime = result.get('market_regime', {})
            market_available = market_regime.get('available', False)
            market_trend = market_regime.get('trend_direction', '未知')  # 多頭/空頭/盤整
            market_adx   = market_regime.get('adx', 25)

            # 判斷大盤狀態
            _is_market_bear  = market_available and market_trend == '空頭'
            _is_market_range = market_available and (
                market_trend == '盤整' or market_adx < 20
            )

            # Layer 1: 方向
            direction = ThreeLayerEngine.score_direction(result)
            if direction['score'] < ThreeLayerEngine.DIRECTION_VETO:
                return ThreeLayerEngine._build_skip_output(direction, result)

            # Layer 2: 位置
            position = ThreeLayerEngine.score_position(result)

            # 接刀防護：深度超跌 + 方向偏弱（< 50）→ 強制否決，避免逆勢接刀
            if position.get('deep_oversold_risk') and direction['score'] < 50:
                position['score'] = min(position['score'], 35)
                position['details'].append('⛔ 深度超跌+方向偏空，接刀否決')

            if position['score'] < ThreeLayerEngine.POSITION_VETO:
                return ThreeLayerEngine._build_wait_output(direction, position, result)

            # Layer 3: 時機
            timing = ThreeLayerEngine.score_timing(result)

            # ── 大盤濾網作用於 timing grade ──────────────────────
            if _is_market_bear and timing['grade'] == 'A':
                # 大盤空頭：A 級降為 B 級，加入大盤警示
                timing['grade'] = 'B'
                timing['label'] = '追蹤（大盤空頭降級）'
                timing['triggers'].append(f'⚠️ 大盤空頭（{market_trend}，ADX={market_adx:.0f}），A→B 降級')
            elif _is_market_bear and timing['grade'] == 'B':
                # 大盤空頭：B 級降為 C 級
                timing['grade'] = 'C'
                timing['label'] = '觀察（大盤空頭降級）'
                timing['triggers'].append(f'⚠️ 大盤空頭，B→C 降級')
            elif _is_market_range and timing['grade'] == 'A':
                # 大盤震盪：A 級降為 B 級
                timing['grade'] = 'B'
                timing['label'] = '追蹤（大盤震盪降級）'
                timing['triggers'].append(f'⚠️ 大盤震盪（ADX={market_adx:.0f}），A→B 降級')

            # 籌碼過濾（可能降級 timing.grade）
            chip = ThreeLayerEngine.apply_chip_filter(result, timing)

            # 賣訊檢查（優先於買訊）
            sell = ThreeLayerEngine.check_sell_signal(result)

            return ThreeLayerEngine._build_buy_output(direction, position, timing, chip, sell, result)

        except Exception as e:
            import traceback
            traceback.print_exc()
            return {'available': False, 'message': f'三層引擎錯誤: {e}'}

    # ─── Layer 1: 方向分 ─────────────────────────────────────────────────────

    @staticmethod
    def score_direction(result: dict) -> dict:
        """
        方向分 (0–100)，閾值 40

        v3.0 更新：
        主判斷：均線排列（MA20/60/120/240）→ 基礎分
        修正 1：MA20 5日斜率（方向動能）→ ±8
        修正 2：ADX 趨勢強度 → ±10
        修正 3：相對強度 RS（vs 大盤）→ ±12
          領先大盤 (+12)、跟隨大盤 (+5)、落後大盤 (-8)

        設計意圖：
        - MA 排列代表現狀，斜率代表動能
        - RS 是台股選強汰弱的核心因子，在多頭市場尤其關鍵
        - ADX 只能「加強」或「削弱」，不能逆轉方向
        """
        tech    = result.get('technical', {})
        current = result.get('current_price', 0)
        rs_data = result.get('relative_strength', {})

        ma20  = tech.get('ma20',  current) or current
        ma60  = tech.get('ma60',  current) or current
        ma120 = tech.get('ma120', current) or current
        ma240 = tech.get('ma240', current) or current
        adx   = tech.get('adx', 20) or 20

        # MA20 歷史序列（用於斜率計算）
        ma20_series = tech.get('ma20_series', [])  # 近 5 日 MA20，由 analyzers 填入

        details = []

        # ── 主判斷：均線排列（0–4 層多頭）────────────────────────
        bull_layers = [
            current > ma20,
            ma20    > ma60,
            ma60    > ma120,
            ma120   > ma240,
        ]
        bull_count = sum(bull_layers)
        bear_count = 4 - bull_count

        # v3.1 L1：改用整數步進，消除 16.25 浮點截斷誤差（原 15/31/47/63/80 間距不等）
        # 新設計：10 / 30 / 50 / 68 / 85，明確的多空分界在 50（2/4 中性）
        _BASE_SCORE_MAP = {0: 10, 1: 30, 2: 50, 3: 68, 4: 85}
        base_score = _BASE_SCORE_MAP[bull_count]

        _align_labels = {4: '均線完全多頭排列（4/4）', 3: '均線偏多排列（3/4）',
                         2: '均線中性混合（2/4）',   1: '均線偏空排列（1/4）',
                         0: '均線完全空頭排列（0/4）'}
        details.append(_align_labels[bull_count])

        # ── 修正 1：MA20 斜率（±8）────────────────────────────────
        # 斜率 = (最新 MA20 - 5日前 MA20) / 5日前 MA20 × 100
        slope_mod = 0
        if isinstance(ma20_series, (list, tuple)) and len(ma20_series) >= 5:
            try:
                ma20_now  = float(ma20_series[-1])
                ma20_prev = float(ma20_series[-5])
                if ma20_prev > 0:
                    slope_pct = (ma20_now - ma20_prev) / ma20_prev * 100
                    if slope_pct >= 0.5:
                        slope_mod = 8
                        details.append(f'MA20斜率向上（+{slope_pct:.2f}%）')
                    elif slope_pct >= 0:
                        slope_mod = 3
                        details.append(f'MA20斜率平穩（+{slope_pct:.2f}%）')
                    elif slope_pct >= -0.5:
                        slope_mod = -3
                        details.append(f'MA20斜率微降（{slope_pct:.2f}%）')
                    else:
                        slope_mod = -8
                        details.append(f'MA20斜率向下（{slope_pct:.2f}%）')
            except (TypeError, ValueError, IndexError):
                pass
        else:
            # 無歷史序列時，用 MA60 與 MA20 的相對位置推算斜率方向
            if ma20 > ma60 * 1.005:
                slope_mod = 3
            elif ma20 < ma60 * 0.995:
                slope_mod = -3

        # ── 修正 2：ADX 趨勢強度（±10）──────────────────────────
        if adx >= 30:
            adx_mod = 10
            details.append(f'趨勢強 ADX={adx:.0f}')
        elif adx >= 20:
            adx_mod = 0
            details.append(f'趨勢中 ADX={adx:.0f}')
        else:
            adx_mod = -10
            details.append(f'趨勢弱 ADX={adx:.0f}（盤整市）')

        # ── 修正 3：相對強度 RS（±12）────────────────────────────
        rs_mod = 0
        if rs_data and isinstance(rs_data, dict):
            rs_score   = rs_data.get('rs_score', 50) or 50    # 0–100，50 = 跟大盤同步
            vs_market  = rs_data.get('vs_market', 0) or 0     # 正 = 領先，負 = 落後
            if rs_score >= 65 or vs_market >= 5:
                rs_mod = 12
                details.append(f'領先大盤 RS={rs_score:.0f}（+{vs_market:.1f}%）')
            elif rs_score >= 50 or vs_market >= 0:
                rs_mod = 5
                details.append(f'跟隨大盤 RS={rs_score:.0f}')
            elif rs_score >= 35:
                rs_mod = -4
                details.append(f'略落後大盤 RS={rs_score:.0f}（{vs_market:.1f}%）')
            else:
                rs_mod = -8
                details.append(f'明顯落後大盤 RS={rs_score:.0f}（{vs_market:.1f}%）')

        # ── 修正 4：量能（±8）— 趨勢健康度（一級因子）──────────────
        # 量能是短線最具預測力的變數之一。帶量上攻=趨勢有量支撐(+)，
        # 價漲量縮=無量虛漲警訊(-)，帶量下殺=趨勢轉弱(-)。
        # 量能資料缺失時 vol_mod=0（不懲罰缺資料，比照 RR 處理原則）。
        # 資料來源：result['volume_price']（technical 不含量能欄位）。
        # 方向用 current vs ma5（近5日短期方向），量能用 vol_ratio（今日量/20日均量）。
        vol_mod = 0
        vp = result.get('volume_price', {}) or {}
        if vp.get('available'):
            vol_ratio = vp.get('vol_ratio', 1.0) or 1.0
            ma5_v     = tech.get('ma5', current) or current
            if isinstance(ma5_v, str):
                ma5_v = current
            short_up   = (current > ma5_v) if (current and ma5_v) else False
            short_down = (current < ma5_v) if (current and ma5_v) else False
            if short_up:
                if vol_ratio >= 1.2:
                    vol_mod = 8
                    details.append(f'量能放大配合上攻（量比{vol_ratio:.1f}）+8')
                elif vol_ratio < 0.8:
                    vol_mod = -8
                    details.append(f'價漲量縮背離，無量虛漲（量比{vol_ratio:.1f}）−8')
                else:
                    details.append(f'量能持平（量比{vol_ratio:.1f}）')
            elif short_down and vol_ratio >= 1.5:
                vol_mod = -6
                details.append(f'帶量下殺，趨勢轉弱（量比{vol_ratio:.1f}）−6')

        score = max(0, min(100, int(base_score + slope_mod + adx_mod + rs_mod + vol_mod)))

        if score >= 70:
            label = '強多頭'
        elif score >= 55:
            label = '偏多'
        elif score >= 45:
            label = '中性'
        elif score >= 30:
            label = '偏空'
        else:
            label = '強空頭'

        return {
            'score':      score,
            'label':      label,
            'details':    details,
            'bull_count': bull_count,
            'bear_count': bear_count,
            'slope_mod':  slope_mod,
            'rs_mod':     rs_mod,
            'vol_mod':    vol_mod,
        }

    # ─── Layer 2: 位置分 ─────────────────────────────────────────────────────

    @staticmethod
    def score_position(result: dict) -> dict:
        """
        位置分 (0–100)，閾值 40

        v3.1 更新：
        主判斷：乖離率（bias_20）→ 基礎分 + 天花板（v3.1 加入 ATR σ 正規化）
        修正 1：RSI → ±20（v3.1 改為線性化，消除邊界斷崖）
        修正 2：RR 比 ← 已移出 L2（v3.1）
          → RR 屬於「交易品質」不屬於「位置品質」，改為純展示用（_compute_targets）
          → 原 RR 無資料扣 10 分懲罰同時移除（不懲罰資料缺失）
        修正 3：籌碼去化條件可解除過熱天花板
          - 外資/投信連買 ≥ 3 天：過熱天花板放寬 +15
          - 外資/投信連買 ≥ 5 天：過熱天花板放寬 +25
          → 強勢股在法人持續進貨時不被「乖離過熱」誤殺

        防止接刀邏輯（新增）：
        - 深度超跌（bias_z < -1.0）且方向分 < 50 → score 強制 ≤ 35（否決）
        """
        tech    = result.get('technical', {})
        mr      = result.get('mean_reversion', {})
        sr      = result.get('support_resistance', {})
        current = result.get('current_price', 0)
        chip    = _get_chip(result)

        rsi = tech.get('rsi', 50) or 50

        # ── 乖離率 ────────────────────────────────────────────────
        if mr.get('available'):
            bias_20 = mr.get('bias_analysis', {}).get('bias_20', 0) or 0
        else:
            ma20    = tech.get('ma20', current) or current
            bias_20 = ((current - ma20) / ma20 * 100) if ma20 > 0 else 0

        # ── v3.1：ATR σ 正規化乖離（解決高 Beta 股系統性偏差）─────
        # 原理：乖離率相同的兩檔股票，對高波動股是正常範圍，對低波動股則是過熱。
        # 使用 ATR% 作為個股日波動率代理（σ），計算正規化乖離 bias_z = bias_20 / (2σ)
        # Z > +1.5 ≈ 超過 3σ 距離 → 嚴重過熱
        # Z > +0.8 ≈ 超過 1.6σ   → 偏熱
        # Z  -0.5~+0.3            → 理想區間
        # Z < -1.0 ≈ 超過 -2σ    → 深度超跌
        atr_raw  = tech.get('atr', 0) or tech.get('atr14', 0) or 0
        if atr_raw > 0 and current > 0:
            sigma_pct = (atr_raw / current) * 100   # ATR 佔現價的百分比（日波動率代理）
        else:
            sigma_pct = 4.0   # fallback：台灣中型股經驗值 σ ≈ 4%
        sigma_pct = max(sigma_pct, 1.5)             # 防止除以極小值
        bias_z = bias_20 / (2.0 * sigma_pct)        # 正規化 Z 分數

        # ── A2 動能模式判定（解除「正乖離=過熱」對強勢股的系統性壓制）──
        # 判定邏輯抽到 ThreeLayerEngine._is_momentum()，L2/L3/賣訊/覆蓋層共用，
        # 確保強勢領漲股在各層處理一致。
        rs_data   = result.get('relative_strength', {}) or {}
        rs_score  = rs_data.get('rs_score', 50) or 50
        is_momentum = ThreeLayerEngine._is_momentum(result)
        # bull_count 僅供回傳顯示（與 _is_momentum 內計算一致）
        ma5_d  = tech.get('ma5',  current) or current
        ma20_d = tech.get('ma20', current) or current
        ma60_d = tech.get('ma60', current) or current
        if isinstance(ma5_d,  str): ma5_d  = current
        if isinstance(ma20_d, str): ma20_d = current
        if isinstance(ma60_d, str): ma60_d = current
        bull_count = sum([
            current > ma20_d, ma20_d > ma60_d, current > ma60_d, ma5_d > ma20_d,
        ])

        # ── 籌碼去化：計算天花板放寬幅度 ────────────────────────
        chip_relax = 0
        chip_note  = ''
        if chip.get('available'):
            buy_days = chip.get('consecutive_buy_days', 0) or 0
            if buy_days >= 5:
                chip_relax = 25
                chip_note  = f'法人連買{buy_days}天，天花板+25'
            elif buy_days >= 3:
                chip_relax = 15
                chip_note  = f'法人連買{buy_days}天，天花板+15'

        details = []

        # ── 主判斷：正規化乖離 bias_z ──────────────────────────────
        # A2：依「動能模式」分流。
        #   - 動能模式（RS領先+多頭排列）：正乖離=強度，給中性偏上 base，
        #     只在極度延伸（bias_z>2.5σ）時附風險提示，但不打到 40 以下。
        #   - 一般模式：沿用原均值回歸表（弱勢股仍受過熱保護）。
        # 負乖離（拉回 / 超跌）兩種模式共用同一段邏輯（動能股拉回也該正常給分）。
        if is_momentum and bias_z > 0.3:
            # 動能模式正乖離評分表（不壓 base、不設過熱天花板）
            bias_cap = 100
            if bias_z > 2.5:
                bias_base = 55
                details.append(
                    f'動能延伸 乖離{bias_20:+.1f}%（{bias_z:.1f}σ，RS={rs_score:.0f}）'
                    f' ⚠️ 極度延伸，留意追高風險'
                )
            else:
                bias_base = 65
                details.append(
                    f'動能強勢 乖離{bias_20:+.1f}%（{bias_z:.1f}σ，RS={rs_score:.0f}，趨勢中正常乖離）'
                )
        # 分段說明（以 σ=4% 為例對應的原始 bias_20 值）：
        #   Z > 1.5 → bias > 12%；Z > 0.8 → bias > 6.4%；Z > 0.3 → bias > 2.4%
        #   -0.5 ~ 0.3 → -4% ~ 2.4%；-1.0 ~ -0.5 → -8% ~ -4%；< -1.0 → bias < -8%
        elif bias_z > 1.5:
            bias_base = 10
            bias_cap  = min(100, 20 + chip_relax)
            details.append(
                f'嚴重過熱 乖離{bias_20:+.1f}%（{bias_z:.1f}σ，天花板{bias_cap}'
                f'{"，"+chip_note if chip_note else ""}）'
            )
        elif bias_z > 0.8:
            bias_base = 25
            bias_cap  = min(100, 35 + chip_relax)
            details.append(
                f'偏熱 乖離{bias_20:+.1f}%（{bias_z:.1f}σ，天花板{bias_cap}'
                f'{"，"+chip_note if chip_note else ""}）'
            )
        elif bias_z > 0.3:
            bias_base = 45
            bias_cap  = 100
            details.append(f'略熱 乖離{bias_20:+.1f}%（{bias_z:.1f}σ）')
        elif bias_z >= -0.5:
            bias_base = 65
            bias_cap  = 100
            details.append(f'理想位置 乖離{bias_20:+.1f}%（{bias_z:.1f}σ）')
        elif bias_z >= -1.0:
            bias_base = 55
            bias_cap  = 100
            details.append(f'輕微超跌 乖離{bias_20:+.1f}%（{bias_z:.1f}σ）')
        else:
            bias_base = 40
            bias_cap  = 75
            details.append(f'深度超跌 乖離{bias_20:+.1f}%（{bias_z:.1f}σ，小心接刀）')

        # ── RSI 線性化（v3.1）：消除 70/65/40 邊界斷崖 ──────────
        # 原設計：70→71 瞬間扣 10 分（斷崖式）
        # 新設計：adj = clip( (65 - RSI) / 5, -20, +15 )
        # RSI=40 → adj=+5；RSI=50 → adj=+3；RSI=65 → adj=0；RSI=70 → adj=-1；RSI=80 → adj=-3；RSI=90 → adj=-5（再乘係數4）
        # 實際公式：分段線性，保持 +15 上限（RSI≤40）和 -20 下限（RSI≥90）
        if rsi <= 40:
            rsi_adj = 15
            details.append(f'RSI超跌 {rsi:.0f}（+15）')
        elif rsi <= 65:
            # 線性：40→+15, 65→0
            rsi_adj = int(15 * (65 - rsi) / 25)
            details.append(f'RSI健康 {rsi:.0f}（{rsi_adj:+d}）')
        elif rsi <= 80:
            # 線性：65→0, 80→-12
            rsi_adj = int(-12 * (rsi - 65) / 15)
            details.append(f'RSI偏熱 {rsi:.0f}（{rsi_adj:+d}）')
        else:
            # 線性：80→-12, 100→-20
            rsi_adj = int(-12 - 8 * (rsi - 80) / 20)
            details.append(f'RSI超買 {rsi:.0f}（{rsi_adj:+d}）')

        # ── RR 比：v3.1 已移出 L2，改為純展示（不影響位置分）─────
        # RR 是「交易品質」評估，不是「位置品質」評估。
        # 且以「無支撐壓力資料 → 扣分」懲罰資料缺失並不合理。
        # RR 資訊保留在 _compute_targets() 作為展示用途。
        take_profit = sr.get('take_profit', 0) or 0
        stop_loss_  = sr.get('stop_loss',   0) or 0
        if isinstance(take_profit, str): take_profit = 0
        if isinstance(stop_loss_,  str): stop_loss_  = 0
        _rr_valid = (take_profit > current > stop_loss_ > 0)
        if _rr_valid:
            potential_gain = take_profit - current
            potential_loss = current - stop_loss_
            rr = potential_gain / potential_loss
            details.append(f'RR={rr:.1f}（僅展示，不計入位置分）')
        else:
            rr = 0
            # 不再扣分，僅記錄資料狀態

        raw_score = bias_base + rsi_adj          # v3.1：移除 rr_adj
        score     = max(0, min(bias_cap, raw_score))

        # ── 防止接刀：深度超跌 + 方向偏空 → 強制否決 ───────────
        # v3.1：改用正規化 bias_z < -1.0（≈ 超跌 2σ 以上），比固定 -10% 更合理
        _deep_oversold_risk = (bias_z < -1.0)

        if score >= 70:
            label = '位置優異'
        elif score >= 55:
            label = '位置合理'
        elif score >= 40:
            label = '位置勉強'
        else:
            label = '位置不佳'

        return {
            'score':              score,
            'label':              label,
            'details':            details,
            'bias_20':            round(bias_20, 2),
            'bias_z':             round(bias_z, 2),     # v3.1：正規化乖離 Z 分數
            'sigma_pct':          round(sigma_pct, 2),  # v3.1：ATR σ 百分比
            'rsi':                rsi,
            'rr_ratio':           round(rr, 2),         # 僅展示，不影響評分
            'rr_valid':           _rr_valid,
            'deep_oversold_risk': _deep_oversold_risk,
            'chip_relax':         chip_relax,
            'is_momentum':        is_momentum,          # A2：是否進入動能模式
            'rs_score':           round(rs_score, 1),
            'bull_count':         bull_count,
        }

    # ─── Layer 3: 時機分 ─────────────────────────────────────────────────────

    @staticmethod
    def score_timing(result: dict) -> dict:
        """
        時機分：輸出 A / B / C / X 分級

        v3.0 重大更新：
        A 級改為「雙因子交叉確認」：
          單一因子最高只能到 A-候選，需第二個獨立因子確認才成為 A
          例外：底部形態 CONFIRMED + 量能確認（高置信度）可單獨觸發 A

        籌碼/分點 B→A 升級機制：
          B 級訊號 + 法人連買 ≥ 3 天 → 升為 A 級
          B 級訊號 + 分點主力進場 → 升為 A 級

        C 級收緊門檻（v2.1 維持）：
          多頭環境 + RSI/乖離/BB壓縮 其中一項才觸發

        因子正交說明：
          三盤突破 / VP05 / 量價同向 → 同屬「量價突破類」，互斥取一
          形態確立 → 獨立因子
          籌碼/分點 → 獨立因子（與技術訊號正交）
        """
        wave    = result.get('wave_analysis', {})
        pattern = result.get('pattern_analysis', {})
        vp      = result.get('volume_price', {})
        mr      = result.get('mean_reversion', {})
        chip    = _get_chip(result)
        branch  = result.get('branch_analysis', {})   # 分點分析（可選）
        tech    = result.get('technical', {})
        current = result.get('current_price', 0)

        triggers: list[str] = []

        # ── 因子偵測（各自獨立，不互相影響）─────────────────────

        # 量價突破類（互斥，只取最強一個）
        _vp_trigger = ''
        if wave.get('available'):
            bo = wave.get('breakout_signal', {})
            if bo.get('detected') and bo.get('volume_confirmed'):
                _vp_trigger = '三盤突破（帶量）'
            elif bo.get('detected'):
                _vp_trigger = '三盤突破（量能待確認）'
        if not _vp_trigger and vp.get('available'):
            for sig in vp.get('signals', []):
                if sig.get('code') == 'VP05':
                    _vp_trigger = '帶量突破 VP05'
                    break
        _vp_strong = _vp_trigger and '待確認' not in _vp_trigger   # 強突破 vs 弱突破

        # 形態類（最高信度形態）
        # v3.1 重要修正：_pat_strong（單獨觸發 A 級的例外路徑）
        #   僅保留「頭肩底」與「W底」兩種需要多點確認的複雜形態。
        #   「V型反轉」偵測門檻低（5%急跌+1根K棒+50%反彈），高波動股每月皆可觸發，
        #   不應享有 A 級例外資格，最高只到 B 級，需靠雙因子路徑升 A。
        _A_ELIGIBLE_PATTERNS = {'頭肩底', 'W底'}   # 可享受例外路徑的形態

        _pat_trigger = ''
        _pat_strong  = False
        if pattern.get('detected') and pattern.get('pattern_type') == 'bottom':
            pat_status = pattern.get('status', '')
            pat_name   = pattern.get('pattern_name', '底部')
            if 'CONFIRMED' in pat_status and pattern.get('volume_confirmed'):
                _pat_trigger = f'{pat_name}確立（量能確認）'
                # V型反轉不納入例外路徑，最高只到 B
                _pat_strong  = (pat_name in _A_ELIGIBLE_PATTERNS)
                if not _pat_strong:
                    _pat_trigger += '（V型反轉限 B 級，需雙因子升 A）'
            elif 'CONFIRMED' in pat_status:
                _pat_trigger = f'{pat_name}確立（量能待確認）'
            elif 'FORMING' in pat_status:
                neckline = pattern.get('neckline_price', 0)
                if neckline > 0 and current > 0:
                    dist = (neckline - current) / current * 100
                    if 0 < dist < 3:
                        # v3.1 M3：形成中形態（尚未突破）預設給 C 級，
                        # 需等 _pat_trigger 被 B 級路徑判斷時搭配量能才升 B。
                        # 用 _pat_forming_near_neck 旗標區分（非 _pat_strong）
                        _pat_trigger = f'{pat_name}形成中（距頸線{dist:.1f}%，等待突破）'

        # 左側超跌反彈（v3.1：加入強度控制，避免弱訊號單獨觸發 B 級）
        # 規則：
        #   _mr_trigger_strong → ≥2 個觸發理由（多重超跌確認）→ 可給 B
        #   _mr_trigger_weak   → 只有 1 個理由，且 RSI < 50    → 可給 B（RSI 超跌輔助）
        #   其餘：只有 1 個理由且 RSI ≥ 50                     → 只到 C 級
        _mr_trigger        = ''
        _mr_trigger_strong = False   # True → B 級資格
        if mr.get('available'):
            lbs = mr.get('left_buy_signal', {})
            if lbs.get('triggered'):
                reasons   = lbs.get('trigger_reasons', [])
                rsi_now   = tech.get('rsi', 50) or 50
                n_reasons = len(reasons) if isinstance(reasons, list) else 1
                if n_reasons >= 2:
                    _mr_trigger        = f'超跌反彈（多重確認：{"、".join(reasons[:2])}）'
                    _mr_trigger_strong = True
                elif rsi_now < 50:
                    _mr_trigger        = f'超跌反彈（{reasons[0] if reasons else "訊號"}+RSI偏低{rsi_now:.0f}）'
                    _mr_trigger_strong = True
                else:
                    # 單一理由且 RSI 不在超跌區：僅給 C 級
                    _mr_trigger        = f'超跌反彈（弱訊號，{reasons[0] if reasons else "單一條件"}，RSI={rsi_now:.0f}）'
                    _mr_trigger_strong = False

        # 籌碼類（連買天數）
        # v3.1 新增 _chip_strong_plus（≥7天）作為 B→A 升級的更高要求：
        #   _chip_trigger:     ≥3天 → 觸發一般籌碼訊號
        #   _chip_strong:      ≥5天 → 雙因子組合路徑（搭配 VP 突破可升 A）
        #   _chip_strong_plus: ≥7天 → 允許 B→A 單獨升級（稀缺性更高）
        _chip_trigger      = ''
        _chip_strong       = False
        _chip_strong_plus  = False   # v3.1：B→A 升級需此旗標
        if chip.get('available'):
            buy_days = chip.get('consecutive_buy_days', 0) or 0
            if buy_days >= 7:
                _chip_trigger     = f'法人連買{buy_days}天（強++）'
                _chip_strong      = True
                _chip_strong_plus = True
            elif buy_days >= 5:
                _chip_trigger = f'法人連買{buy_days}天（強）'
                _chip_strong  = True
            elif buy_days >= 3:
                _chip_trigger = f'法人連買{buy_days}天'

        # 分點類（主力券商進場）
        # v3.1：同樣新增 _branch_strong_plus（≥7天）作為 B→A 升級條件
        _branch_trigger      = ''
        _branch_strong       = False
        _branch_strong_plus  = False   # v3.1：B→A 升級需此旗標
        if branch.get('available'):
            main_buy_days = branch.get('main_branch_buy_days', 0) or 0
            branch_name   = branch.get('main_branch_name', '主力分點')
            if main_buy_days >= 7:
                _branch_trigger     = f'{branch_name}連買{main_buy_days}天（強++）'
                _branch_strong      = True
                _branch_strong_plus = True
            elif main_buy_days >= 5:
                _branch_trigger = f'{branch_name}連買{main_buy_days}天（強）'
                _branch_strong  = True
            elif main_buy_days >= 3:
                _branch_trigger = f'{branch_name}連買{main_buy_days}天'

        # ── A 級：雙因子交叉確認 ─────────────────────────────────
        # 規則：需要兩個獨立因子同時觸發
        # 例外：高置信度形態（量能確認）可單獨觸發 A
        grade = 'X'

        # 例外路徑：高置信度形態單獨觸發 A
        if _pat_strong:
            triggers.append(_pat_trigger)
            grade = 'A'

        # 雙因子路徑：量價突破（強）+ 形態或籌碼
        if grade != 'A' and _vp_strong:
            if _pat_trigger:
                triggers.extend([_vp_trigger, _pat_trigger])
                grade = 'A'
            elif _chip_trigger:
                triggers.extend([_vp_trigger, _chip_trigger])
                grade = 'A'
            elif _branch_trigger:
                triggers.extend([_vp_trigger, _branch_trigger])
                grade = 'A'

        # 雙因子路徑：強籌碼 + 形態（確立）
        if grade != 'A' and _chip_strong and _pat_trigger and 'CONFIRMED' in pattern.get('status', ''):
            triggers.extend([_chip_trigger, _pat_trigger])
            grade = 'A'

        # ── B 級：單一強因子 or B→A 升級未達條件 ────────────────
        if grade != 'A':
            # 強突破但缺第二因子 → B
            if _vp_strong:
                triggers.append(_vp_trigger)
                grade = 'B'
            # 形態確立但量能不足 → B
            elif _pat_trigger and 'CONFIRMED' in pattern.get('status', ''):
                triggers.append(_pat_trigger)
                grade = 'B'
            # 弱突破訊號 → B
            elif _vp_trigger:
                triggers.append(_vp_trigger)
                grade = 'B'
            # 超跌反彈 → B（v3.1：需強訊號才給 B，弱訊號只到 C）
            elif _mr_trigger and _mr_trigger_strong:
                triggers.append(_mr_trigger)
                grade = 'B'
            # 形態形成中（v3.1 M3）：
            # 預設降為 C 級，需同時有量能放大訊號（_vp_trigger）才升 B
            elif _pat_trigger and '形成中' in _pat_trigger:
                if _vp_trigger:
                    # 量能放大 + 形成中 → 已有突破苗頭，給 B
                    triggers.extend([_pat_trigger, _vp_trigger])
                    grade = 'B'
                else:
                    # 純形成中無量能 → C 級觀察，等待突破確認
                    triggers.append(_pat_trigger + '（無量，僅觀察）')
                    grade = 'C'

            # B 級 + 強籌碼/分點 → 升 A（v3.1：需 ≥7天的 _plus 旗標，收緊門檻）
            if grade == 'B' and (_chip_strong_plus or _branch_strong_plus):
                upgrade_reason = _chip_trigger if _chip_strong_plus else _branch_trigger
                triggers.append(f'⬆️ 籌碼/分點強力確認升 A（≥7天）：{upgrade_reason}')
                grade = 'A'

        # ── C 級：多頭環境 + 次要條件，或弱超跌反彈 ──────────────
        # 弱超跌反彈（M2）：不需要多頭環境，直接給 C 級
        if grade == 'X' and _mr_trigger and not _mr_trigger_strong:
            triggers.append(_mr_trigger)
            grade = 'C'

        if grade == 'X' and wave.get('is_bullish_env'):
            rsi_c  = tech.get('rsi', 50) or 50
            if mr.get('available'):
                bias_c = mr.get('bias_analysis', {}).get('bias_20', 0) or 0
            else:
                ma20_c = tech.get('ma20', current) or current
                bias_c = ((current - ma20_c) / ma20_c * 100) if ma20_c > 0 else 0
            bb_squeeze = tech.get('bb_squeeze', False)

            # v3.1 L2：籌碼/分點已由 apply_chip_filter() 統一處理，
            # 不在此重複加入 C 級條件，避免職責混淆。
            c_conditions = []
            if 40 <= rsi_c <= 65:  c_conditions.append(f'RSI健康區({rsi_c:.0f})')
            if -4 <= bias_c <= 4:  c_conditions.append(f'乖離適中({bias_c:+.1f}%)')
            if bb_squeeze:         c_conditions.append('BB壓縮蓄勢')

            if c_conditions:
                triggers.append('多頭環境 + ' + '、'.join(c_conditions))
                grade = 'C'
            else:
                triggers.append(f'多頭環境但條件不足（RSI={rsi_c:.0f} 乖離={bias_c:+.1f}%）')
        elif grade == 'X':
            triggers.append('無明確進場訊號')

        # ── 超買安全閥（v3.1 M4）───────────────────────────────────
        # 設計意圖：Layer 2 靠籌碼去化（chip_relax）撐過否決門檻後，
        # Layer 3 若仍輸出 A 級，代表在極度過熱狀態追高。
        # 條件：RSI > 85 且 bias_z > 1.5σ → A 強制降 B，輸出警示。
        if grade == 'A':
            _rsi_ov   = tech.get('rsi', 50) or 50
            _atr_ov   = tech.get('atr', 0) or tech.get('atr14', 0) or 0
            _sigma_ov = (_atr_ov / current * 100) if (_atr_ov > 0 and current > 0) else 4.0
            _sigma_ov = max(_sigma_ov, 1.5)
            _mr_ov    = result.get('mean_reversion', {})
            if _mr_ov.get('available'):
                _bias_ov = _mr_ov.get('bias_analysis', {}).get('bias_20', 0) or 0
            else:
                _ma20_ov  = tech.get('ma20', current) or current
                _bias_ov  = ((current - _ma20_ov) / _ma20_ov * 100) if _ma20_ov > 0 else 0
            _bias_z_ov = _bias_ov / (2.0 * _sigma_ov)

            if _rsi_ov > 85 and _bias_z_ov > 1.5:
                # A2 改動2 + 修正2：強勢領漲股的超買處理分兩段（與 L2 動能分支
                # 對「極度延伸 bias_z>2.5」的態度對齊）：
                #   1.5σ < bias_z ≤ 2.5σ：動能股只警示、不降級（維持 A2 寬鬆待遇）。
                #   bias_z > 2.5σ（極度延伸）：即使動能股也強制 A→B，防噴出末端追高。
                # 非動能股：維持原本 A→B 降級。
                if ThreeLayerEngine._is_momentum(result):
                    if _bias_z_ov > 2.5:
                        grade = 'B'
                        triggers.append(
                            f'⚠️ 極度延伸硬上限：RSI={_rsi_ov:.0f} 且 乖離{_bias_ov:+.1f}%'
                            f'（{_bias_z_ov:.1f}σ）→ A→B 降級為追蹤，不宜立即追進'
                        )
                    else:
                        triggers.append(
                            f'⚠️ 過熱提示（強勢股不降級）：RSI={_rsi_ov:.0f} 且 '
                            f'乖離{_bias_ov:+.1f}%（{_bias_z_ov:.1f}σ），續抱但留意追高風險'
                        )
                else:
                    grade = 'B'
                    triggers.append(
                        f'⚠️ 超買安全閥：RSI={_rsi_ov:.0f} 且 乖離{_bias_ov:+.1f}%（{_bias_z_ov:.1f}σ）'
                        f' → A→B 強制降級，追高風險高'
                    )

        _grade_labels = {
            'A': '主攻（立即進場）',
            'B': '追蹤（等待確認）',
            'C': '觀察（記錄追蹤）',
            'X': '無訊號',
        }

        return {
            'grade':    grade,
            'label':    _grade_labels[grade],
            'triggers': triggers,
        }

    # ─── 籌碼過濾器 ──────────────────────────────────────────────────────────

    @staticmethod
    def apply_chip_filter(result: dict, timing: dict) -> dict:
        """
        籌碼：排序器 + 過濾器，不做主判斷

        用途：
        - ranking_boost：同類型股票中，籌碼強的排前面（+2/+1/0/-1/-2）
        - 過濾降級：B/C 且法人連賣 ≥ 5 天 → 降為 C/X

        不用途：
        - 不加分到 direction/position 分
        - 不單獨決定買賣
        """
        chip = _get_chip(result)

        if not chip.get('available'):
            return {'filter': 'neutral', 'note': '無籌碼資料', 'ranking_boost': 0}

        consecutive_buy  = chip.get('consecutive_buy_days',  0) or 0
        consecutive_sell = chip.get('consecutive_sell_days', 0) or 0
        foreign_net      = chip.get('foreign_net', 0) or 0

        notes: list[str] = []
        ranking_boost = 0

        # 排序分數
        if consecutive_buy >= 5:
            ranking_boost = 2
            notes.append(f'法人連買 {consecutive_buy} 天（強）')
        elif consecutive_buy >= 3:
            ranking_boost = 1
            notes.append(f'法人連買 {consecutive_buy} 天')
        elif consecutive_sell >= 5:
            ranking_boost = -2
            notes.append(f'法人連賣 {consecutive_sell} 天（警示）')
        elif consecutive_sell >= 3:
            ranking_boost = -1
            notes.append(f'法人連賣 {consecutive_sell} 天')

        if foreign_net > 0:
            notes.append(f'外資淨買 {foreign_net:,} 張')
        elif foreign_net < 0:
            notes.append(f'外資淨賣 {abs(foreign_net):,} 張')

        # 降級邏輯（只在邊緣情況起作用）
        filter_action = 'pass'
        grade = timing.get('grade', 'C')

        if consecutive_sell >= 5 and grade in ('B', 'C'):
            filter_action = 'downgrade'
            if grade == 'B':
                timing['grade'] = 'C'
                timing['label'] = '觀察（籌碼降級）'
                notes.append('B→C 籌碼持續惡化')
            else:
                timing['grade'] = 'X'
                timing['label'] = '無訊號（籌碼出場）'
                notes.append('C→X 籌碼惡化，跳過')

        return {
            'filter':        filter_action,
            'ranking_boost': ranking_boost,
            'note':          '；'.join(notes) if notes else '籌碼中性',
        }

    # ─── 賣訊檢查 ────────────────────────────────────────────────────────────

    @staticmethod
    def check_sell_signal(result: dict) -> dict:
        """
        賣訊：3 種類型，清楚分層

        1. 防守型 (DEFENSIVE)：停損 / 跌破關鍵均線
           → 最高優先，severity='urgent'
        2. 停利型 (PROFIT_TAKE)：過熱賣訊 / 移動停利
           → severity='warning'
        3. 反轉型 (REVERSAL)：頭部形態確立 / 籌碼嚴重惡化
           → severity 視嚴重程度而定

        設計意圖：
        - 賣點不超過 3 類，每類有明確觸發條件
        - urgent 訊號 → action_code = SELL
        - warning 訊號 → action_code = HOLD（注意）
        """
        import json as _json
        import os as _os

        tech    = result.get('technical',      {})
        wave    = result.get('wave_analysis',  {})
        mr      = result.get('mean_reversion', {})
        pattern = result.get('pattern_analysis', {})
        chip    = _get_chip(result)
        current = result.get('current_price',  0)
        symbol  = result.get('symbol', '')

        ma20 = tech.get('ma20', 0) or 0
        sell_signals: list[dict] = []

        # A2 改動3：強勢領漲股（RS 領先+多頭排列）的「過熱/背離」預測型賣訊
        # 降為 info 純提示，不翻成 SELL/HOLD（飆股拉回常被誤判做頭）。
        # 防守型（跌破均線/三盤跌破）、反轉型（頭部形態/籌碼惡化）、移動停利
        # 維持原樣——那是真趨勢轉壞或實質獲利回吐，不屬「過熱預測」。
        _momentum = ThreeLayerEngine._is_momentum(result)
        _overheat_severity = 'info' if _momentum else 'warning'

        # ── 1. 防守型 ─────────────────────────────────────────────
        # 1a: 三盤跌破
        if wave.get('available'):
            bd = wave.get('breakdown_signal', {})
            if bd.get('detected'):
                sell_signals.append({
                    'type':     'DEFENSIVE',
                    'reason':   '三盤跌破，趨勢轉空',
                    'severity': 'urgent',
                })

        # 1b: 收盤跌破 MA20 超過 2%
        if ma20 > 0 and current < ma20 * 0.98:
            sell_signals.append({
                'type':     'DEFENSIVE',
                'reason':   f'跌破 MA20（現價 {current:.2f} < MA20 {ma20:.2f} ×0.98）',
                'severity': 'warning',
            })

        # ── 2. 停利型 ─────────────────────────────────────────────
        if mr.get('available'):
            left_sell = mr.get('left_sell_signal', {})
            if left_sell.get('triggered'):
                reasons = '、'.join(left_sell.get('trigger_reasons', ['過熱']))
                _prefix = '過熱提示（強勢股，僅參考）：' if _momentum else '過熱賣訊：'
                sell_signals.append({
                    'type':     'PROFIT_TAKE',
                    'reason':   f'{_prefix}{reasons}',
                    'severity': _overheat_severity,
                })

        # ── 2b. 移動停利（TRAILING STOP）─────────────────────────
        # 從 trailing_stop_data.json 讀取各股的最高點記錄
        # 觸發條件：當前價格從最高點回落 ≥ TRAILING_STOP_DISTANCE_PCT
        try:
            from config import QuantConfig as _QC
            if _QC.TRAILING_STOP_ENABLED and symbol and current > 0:
                _trailing_path = _os.path.join(
                    _os.path.dirname(_os.path.abspath(__file__)),
                    'trailing_stop_data.json'
                )
                if _os.path.exists(_trailing_path):
                    with open(_trailing_path, 'r', encoding='utf-8') as _f:
                        _ts_data = _json.load(_f)
                    _ts = _ts_data.get(str(symbol), {})
                    _peak = _ts.get('peak_price', 0)
                    _entry = _ts.get('entry_price', 0)

                    if _peak > 0 and _entry > 0:
                        _gain_from_entry = (_peak - _entry) / _entry * 100
                        _drawdown_from_peak = (_peak - current) / _peak * 100

                        # 啟動條件：已獲利達到啟動門檻
                        _activation = _QC.TRAILING_STOP_ACTIVATION_PCT * 100
                        _distance   = _QC.TRAILING_STOP_DISTANCE_PCT   * 100

                        if (_gain_from_entry >= _activation and
                                _drawdown_from_peak >= _distance):
                            sell_signals.append({
                                'type':     'PROFIT_TAKE',
                                'reason':   (
                                    f'移動停利觸發：峰值 {_peak:.2f}，'
                                    f'回落 {_drawdown_from_peak:.1f}%'
                                    f'（門檻 {_distance:.0f}%）'
                                ),
                                'severity': 'warning',
                                'peak_price':    _peak,
                                'drawdown_pct':  round(_drawdown_from_peak, 2),
                            })
        except Exception:
            pass  # 移動停利讀取失敗時，靜默跳過，不影響其他賣訊

        # ── 3. 反轉型 ─────────────────────────────────────────────
        # 3a: 頭部形態確立
        if (pattern.get('detected') and
                pattern.get('pattern_type') == 'top' and
                'CONFIRMED' in pattern.get('status', '')):
            sell_signals.append({
                'type':     'REVERSAL',
                'reason':   f'{pattern.get("pattern_name", "頭部形態")}確立，趨勢反轉',
                'severity': 'urgent',
            })

        # 3b: 法人連賣（v3.1 M5：門檻下調，更早捕捉籌碼惡化）
        # 新規則：
        #   連賣 ≥ 5 天 且 日均賣超量 ≥ 500 張 → 量能確認的惡化，severity='warning'
        #   連賣 ≥ 7 天（無論量能）             → 持續惡化，severity='warning'
        if chip.get('available'):
            csell    = chip.get('consecutive_sell_days', 0) or 0
            sell_net = abs(chip.get('foreign_net', 0) or 0)   # 外資淨賣（張）
            if csell >= 5 and sell_net >= 500:
                sell_signals.append({
                    'type':     'REVERSAL',
                    'reason':   f'法人連賣 {csell} 天且日均賣超 {sell_net:,} 張，籌碼快速惡化',
                    'severity': 'warning',
                })
            elif csell >= 7:
                sell_signals.append({
                    'type':     'REVERSAL',
                    'reason':   f'法人連賣 {csell} 天（持續惡化），籌碼嚴重流失',
                    'severity': 'warning',
                })

        # ── 新增：持倉過熱警示（v3.1 M5）─────────────────────────
        # 設計意圖：現有賣訊只在進場前過濾過熱（L2），
        # 未處理進場後股票繼續上漲進入極度超買的情況。
        # 觸發：RSI > 85 且 bias_z > 1.5σ → 停利警示（非強制賣出）
        try:
            _rsi_sell  = tech.get('rsi', 50) or 50
            _atr_sell  = tech.get('atr', 0) or tech.get('atr14', 0) or 0
            _sigma_sell = (_atr_sell / current * 100) if (_atr_sell > 0 and current > 0) else 4.0
            _sigma_sell = max(_sigma_sell, 1.5)
            if mr.get('available'):
                _bias_sell = mr.get('bias_analysis', {}).get('bias_20', 0) or 0
            else:
                _ma20_sell = tech.get('ma20', current) or current
                _bias_sell = ((current - _ma20_sell) / _ma20_sell * 100) if _ma20_sell > 0 else 0
            _bias_z_sell = _bias_sell / (2.0 * _sigma_sell)

            if _rsi_sell > 85 and _bias_z_sell > 1.5:
                _ph_tail = ('，強勢股續抱、僅留意追高風險' if _momentum
                            else '，建議考慮部分停利')
                sell_signals.append({
                    'type':     'PROFIT_TAKE',
                    'reason':   (
                        f'持倉過熱警示：RSI={_rsi_sell:.0f}，乖離{_bias_sell:+.1f}%（{_bias_z_sell:.1f}σ）'
                        f'{_ph_tail}'
                    ),
                    'severity': _overheat_severity,
                })
        except Exception:
            pass  # 過熱警示計算失敗時靜默跳過

        if not sell_signals:
            return {'triggered': False, 'primary': None, 'all': []}

        # A2 改動3：info 級（強勢股過熱提示）不觸發賣訊動作，只當資訊註記。
        actionable = [s for s in sell_signals if s['severity'] in ('urgent', 'warning')]
        info_notes = [s for s in sell_signals if s['severity'] == 'info']

        if not actionable:
            # 只有過熱提示（強勢股）→ 不翻賣訊，續走買訊/分級邏輯
            return {
                'triggered': False,
                'primary':   None,
                'all':       sell_signals,
                'info_notes': info_notes,
            }

        # urgent 優先
        urgent = [s for s in actionable if s['severity'] == 'urgent']
        primary = urgent[0] if urgent else actionable[0]
        return {
            'triggered':  True,
            'primary':    primary,
            'all':        sell_signals,
            'info_notes': info_notes,
        }

    # ─── 目標價計算（簡化版）────────────────────────────────────────────────

    @staticmethod
    def _compute_targets(result: dict, is_buy: bool) -> dict:
        """
        計算目標價與停損，作為報告使用

        優先順序：
        1. 形態學測幅
        2. 支撐/壓力位
        3. ATR 估算
        """
        current = result.get('current_price', 0)
        if current <= 0:
            return {'available': False}

        pattern = result.get('pattern_analysis', {})
        sr      = result.get('support_resistance', {})
        tech    = result.get('technical', {})

        target_price = 0
        stop_loss    = 0
        source       = ''

        # 1. 形態測幅（最高優先）
        if pattern.get('detected') and pattern.get('target_price', 0) > 0:
            pt   = pattern['pattern_type']
            tgt  = pattern['target_price']
            stop = pattern.get('stop_loss', 0)
            if (pt == 'bottom' and is_buy) or (pt == 'top' and not is_buy):
                target_price = tgt
                stop_loss    = stop
                source       = f'{pattern.get("pattern_name", "形態")}測幅'

        # 2. 支撐/壓力位
        if target_price <= 0:
            if is_buy:
                r1 = sr.get('resistance1', 0)
                s1 = sr.get('support1',    0)
                target_price = r1 if r1 > current else round(current * 1.08, 2)
                stop_loss    = s1 if (s1 > 0 and s1 < current) else round(current * 0.93, 2)
                source       = '壓力位 / 預估 8%'
            else:
                s1 = sr.get('support1', 0)
                target_price = s1 if (s1 > 0 and s1 < current) else round(current * 0.92, 2)
                stop_loss    = round(current * 1.05, 2)
                source       = '支撐位'

        # 確保目標價已超過現價時動態推移
        if is_buy and target_price > 0 and current >= target_price:
            ma60 = tech.get('ma60', 0) or current
            target_price = round(max(ma60, current) * 1.10, 2)
            source += '（動態推移）'

        gain = (target_price - current) / current * 100 if target_price > 0 else 0
        loss = (current - stop_loss)    / current * 100 if stop_loss    > 0 else 0
        rr   = abs(gain / loss)                          if loss > 0    else 0

        return {
            'available':         True,
            'target_price':      round(target_price, 2) if target_price > 0 else None,
            'stop_loss':         round(stop_loss,    2) if stop_loss    > 0 else None,
            'target_source':     source,
            'potential_gain_pct': round(gain, 2),
            'potential_loss_pct': round(loss, 2),
            'rr_ratio':           round(rr,   2),
            'current_price':      current,
        }

    # ─── 輸出建構 ────────────────────────────────────────────────────────────

    @staticmethod
    def _build_skip_output(direction: dict, result: dict) -> dict:
        """Layer 1 否決：方向不對，跳過"""
        return {
            'available':    True,
            'scenario':     'SKIP',
            'scenario_name': '方向不對',
            'score':        direction['score'],
            'recommendation': '不建議關注',
            'action_timing':  f'{direction["label"]}，方向分 {direction["score"]} < 40',
            'warning_message': f'均線空頭排列（{direction["bull_count"]}/4 多頭）',
            'confidence':     'High',
            'action_code':    'SKIP',
            'filters_applied': ['方向否決'],
            'downgraded':     False,
            'short_term_action': '跳過',
            'original_recommendation': '不建議關注',
            'three_layer': {
                'direction': direction,
                'position':  None,
                'timing':    None,
                'chip':      None,
                'sell_signal': None,
            },
            'price_targets': {'available': False},
        }

    @staticmethod
    def _build_wait_output(direction: dict, position: dict, result: dict) -> dict:
        """Layer 2 否決：位置不佳，等待
        v3.1 L3：WAIT 分數直接取 position score（否決層），
                  語意明確：顯示的就是「卡關的那層」得了幾分。
        """
        composite = position['score']   # 位置分本身，不再與方向分加權混合
        return {
            'available':    True,
            'scenario':     'WAIT',
            'scenario_name': '位置不佳',
            'score':        composite,
            'recommendation': '等待拉回',
            'action_timing':  f'{position["label"]}，位置分 {position["score"]} < 40',
            'warning_message': '；'.join(position['details'][:3]),
            'confidence':     'Medium',
            'action_code':    'WAIT',
            'filters_applied': ['位置否決'],
            'downgraded':     False,
            'short_term_action': '觀望',
            'original_recommendation': '等待拉回',
            'three_layer': {
                'direction': direction,
                'position':  position,
                'timing':    None,
                'chip':      None,
                'sell_signal': None,
            },
            'price_targets': {'available': False},
        }

    @staticmethod
    def _build_buy_output(
        direction: dict,
        position:  dict,
        timing:    dict,
        chip:      dict,
        sell:      dict,
        result:    dict,
    ) -> dict:
        """Layer 1+2 通過後的完整決策輸出"""

        grade = timing['grade']

        # 賣訊優先於買訊
        if sell['triggered']:
            primary_sell = sell['primary']
            is_urgent = primary_sell['severity'] == 'urgent'
            action_code   = 'SELL' if is_urgent else 'HOLD'
            recommendation = f'賣出訊號（{primary_sell["type"]}）' if is_urgent else f'注意賣訊（{primary_sell["type"]}）'
            scenario       = 'SELL'
            scenario_name  = '賣出訊號'
            action_timing  = primary_sell['reason']
            score          = 25 if is_urgent else 42
            confidence     = 'High' if is_urgent else 'Medium'
            is_buy         = False

        elif grade == 'A':
            action_code    = 'STRONG_BUY'
            recommendation = 'A 級主攻，立即進場'
            scenario       = 'A'
            scenario_name  = 'A 級主攻'
            action_timing  = '；'.join(timing['triggers'])
            # v3.1：移除人為保底 70，讓分數真實反映 direction+position 品質
            # 強 A（direction=85, position=80）≈ 82；弱 A（direction=43, position=43）≈ 43
            score          = int(direction['score'] * 0.35 + position['score'] * 0.65)
            confidence     = 'High'
            is_buy         = True

        elif grade == 'B':
            action_code    = 'BUY'
            recommendation = 'B 級追蹤，等待確認'
            scenario       = 'B'
            scenario_name  = 'B 級追蹤'
            action_timing  = '；'.join(timing['triggers'])
            # v3.1：移除保底 55 / 上限 69，真實計算
            score          = int(direction['score'] * 0.35 + position['score'] * 0.65)
            confidence     = 'Medium'
            is_buy         = True

        else:  # C 或 X
            action_code    = 'HOLD'
            recommendation = 'C 級觀察，列入追蹤'
            scenario       = 'C'
            scenario_name  = 'C 級觀察'
            action_timing  = '；'.join(timing['triggers']) if timing['triggers'] else '等待進場時機'
            # v3.1：移除上限 54，真實計算
            score          = int(direction['score'] * 0.35 + position['score'] * 0.65)
            confidence     = 'Low'
            is_buy         = True

        # 籌碼備注
        chip_note = chip.get('note', '') if chip else ''

        # 目標價
        price_targets = ThreeLayerEngine._compute_targets(result, is_buy)

        return {
            'available':    True,
            'scenario':     scenario,
            'scenario_name': scenario_name,
            'score':        score,
            'recommendation': recommendation,
            'action_timing':  action_timing,
            'warning_message': chip_note,
            'confidence':     confidence,
            'action_code':    action_code,
            'filters_applied': [] if chip.get('filter') == 'pass' else ['籌碼降級'],
            'downgraded':     chip.get('filter') == 'downgrade',
            'short_term_action': recommendation,
            'original_recommendation': recommendation,
            'three_layer': {
                'direction':   direction,
                'position':    position,
                'timing':      timing,
                'chip':        chip,
                'sell_signal': sell,
            },
            'price_targets': price_targets,
        }
