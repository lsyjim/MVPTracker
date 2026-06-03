# MVPTracker v1 (MVP) 驗收結果

日期：2026-06-03　環境：Python 3.13.9 / NiceGUI 3.12.1 / 富邦未登入（走 Yahoo fallback）

## 自動化測試

`python3 -m pytest -q` → **18 passed**
（test_imports / test_theme / test_db / test_seed / test_store / test_scanner / test_institutional / test_master / test_watchlist）

## 規格步驟 7 驗收項目

| # | 項目 | 結果 | 證據 |
|---|------|------|------|
| 1 | 四頁能跑（總覽 / 明細 / 個股彈窗 / 自選） | ✅ | 四頁皆截圖確認；象限/報告 nav 列出但 disabled |
| 2 | CRUD 寫入 SQLite 並即時反映 | ✅ | store 單元測試；新增題材/成分股對話框接 store；即時重繪 |
| 2b | 重啟 App 後資料仍在 | ✅ | 腳本驗證：新增 custom 題材 + 成分股 → 重開連線仍在（19→20→清理回 19） |
| 3 | 非母清單代號可手動加入並標記 | ✅ | `in_master=0` 持久化；明細列顯示 ⚑；master 查無不阻擋 |
| 4 | 富邦資料進得來 / 失敗 fallback | ✅ | app bar 顯示「Yahoo(fallback)」（本機未登入富邦）；2330 取得 118 筆歷史；K 線真 OHLC |
| 4b | EOD 三大法人逐檔 /iibs | ✅ | 2330 外資 −3,312 / 投信 +458 / 自營 +922（截至 2026-06-01）；明細列法人(張)為真值 |
| 5 | 紅漲綠跌 / A·B·C 配色正確 | ✅ | 熱圖紅漲綠跌；badge A=紅 B=藍 C=灰 sell=綠；修正 grade_tag 使「C 級…列入追蹤」正確為 C |
| 5b | 背離 ⚠ / 法人金條 | ✅ | 熱圖底部金條（賣超綠斜紋）、⚠ 背離標記 |
| 6 | 個股完整分析報告 | ✅ | 「產生完整分析報告」呼叫 QuickAnalyzer → 格式化 7 段（綜合建議/情境/時機/提醒/短中長線） |
| 7 | 極簡自選股 add/remove | ✅ | 彈窗「加入自選」寫入 watchlist；自選頁列出；移除即時反映（截圖確認 2330 移除後僅餘 2049） |
| 8 | `ui.run(native=True)` 桌面視窗 | ⏳ 待使用者本機確認 | 程式碼路徑存在（main.py:145）；web 模式（MVP_WEB=1）已全面驗證；native 需在有桌面環境執行 |

## 真資料端到端樣本（晶圓代工，富邦未登入→yfinance + 悟空 /iibs）

| 代號 | 名稱 | 現價 | 今日 | RS | 法人(張) | 訊號 | badge |
|------|------|------|------|----|---------|------|-------|
| 2330 | 台積電 | 2425.0 | +1.9% | 36 | −1,932 | 注意賣訊（PROFIT_TAKE） | 賣出(綠) |
| 2303 | 聯電 | 130.5 | −7.8% | 55 | −17,688 | 等待拉回 | C(灰) |
| 6770 | 力積電 | 84.6 | −1.5% | 88 | −165,281 | C 級觀察，列入追蹤 | C(灰) |

題材聚合：5日動能 −5.7%、法人買超 0/3、家數 3。

## 已知事項 / v2

- 富邦即時需登入憑證（`N124680879_*.p12` / `api_cert.pfx`）；未登入自動走 Yahoo/yfinance。
- 總覽 `real_overview` 冷啟動逐檔掃描全部成分股；富邦登入時最快，未登入經 yfinance 較慢。
  已加 `scan_cache(kind='row')` 每日快取；造訪題材明細會逐步暖快取。離線/快速 demo 可設 `MVP_MOCK=1`。
- v2 預留：盤中主力力道 `institutional.intraday_force()`→None、象限圖、報告匯出、完整自選股、打包（nicegui-pack / PyInstaller）。

## 測試/除錯 hook（env）

- `MVP_WEB=1`：瀏覽器模式（headless 截圖）；`MVP_PORT=`。
- `MVP_MOCK=1`：總覽用假資料（快速）。
- `MVP_DETAIL=<theme_id>`：啟動停在該題材明細。
- `MVP_PAGE=<overview|detail|watch>`：啟動停在指定頁。
- `MVP_MODAL=1`：明細頁自動開個股彈窗（截圖用）。
