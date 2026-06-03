# MVPTracker — NiceGUI 題材追蹤 App v1 設計文件

日期：2026-06-03
狀態：待使用者確認

## 目標

用 NiceGUI 做一個全新的台股題材追蹤桌面 App，**複製**現有後端
（`analyzers.py` / `decision_engine.py` / `advanced_analyzers.py` / `data_fetcher.py`）
到 MVPTracker 成為獨立自足的專案，不重寫分析邏輯。先用假資料把版面與互動做到能跑，
再逐步接後端真資料。可打包成桌面 App（native 模式）。

不含：自動交易、下單、回測。

## 已定案決策（含本次確認）

1. **CRUD 允許母清單以外代號**：輸入代號→母清單查得到自動帶名稱；查不到允許手動輸入名稱、
   標記 `in_master=0`（非母清單），不阻擋。
2. **持久化用 SQLite**：題材定義、成分股、自選股、分析快取都存 SQLite；另提供匯入/匯出
   `concept_map.json`（與 Claude Code / Cowork 共用的橋接）。
3. **本次範圍 = 規格步驟 1–7（全部，含接後端）**：總覽熱圖（EOD 法人 + 背離）、題材明細
   （子題材分組）、個股彈窗（K線+報告）、就地 CRUD、極簡自選股，並接 fetcher / institutional / scanner 真資料。
4. **自選股重建**：不匯入舊系統資料，SQLite 新表從零開始。
5. **資料源**：富邦為主（即時報價/歷史/分鐘K），失敗 fallback Yahoo/yfinance；EOD 三大法人用
   WukongAPI；盤中主力力道（v2）用富邦 tick。
6. **CRUD 就地編輯**：總覽頁直接開新題材；明細頁直接加/移成分股（不做獨立管理頁）。
7. **後端用「複製」而非 import**（本次確認）：把 4 個後端檔複製進 MVPTracker，專案獨立自足；
   未來原檔有更新時由使用者手動同步兩邊。
8. **EOD 法人逐筆精算**（本次確認）：用**個股端點逐檔查**，不用排行近似。STOCKGOGO 報告的
   個股法人來自 `main.py::_analyze_chip_flow_wukong(symbol)`，打 `GET https://api.wukong.com.tw/stock/{symbol}/iibs`，
   回傳該股每日 `foreignInvestorsBuySell / investmentTrustBuySell / dealerBuySell / total`（單位：張）
   與歷史（可算連續買/賣超天數）。此邏輯**不在那 4 個檔裡**，故需一併移植到
   `data/institutional.py`，產出與 `decision_engine._get_chip` 相容的 `chip_flow` dict
   （`foreign_net / trust_net / dealer_net / foreign_consecutive_days / trust_consecutive_days /
   available / signal`）。每檔逐筆查、每日快取於 `scan_cache`。

## v2（本次不做，預留接口）

動能×法人象限圖、盤中主力力道（富邦 tick）、報告匯出給 Cowork、完整自選股（分組/掃描）、設定頁。
程式內預留 `institutional.intraday_force(code)` 回 `None`。

## 後端複製與相依（關鍵架構決策）

四個後端檔的本地相依：

- `analyzers.py` → `from config import QuantConfig`、`from decision_engine import ThreeLayerEngine`
- `decision_engine.py` → `from config import QuantConfig`（函式內延遲匯入）
- `data_fetcher.py` → `from config import QuantConfig`
- `advanced_analyzers.py` → 無本地相依

因此複製後必須讓 `import analyzers` / `import decision_engine` / `from config import QuantConfig`
能解析。做法：

- 複製 `StockGOGOV2/config.py` → `MVPTracker/config.py`（含 `QuantConfig`），再於其中**附加**
  App 設定（路徑、DB 位置等），保留 `QuantConfig` 原樣。
- 複製 `analyzers.py` / `advanced_analyzers.py` / `decision_engine.py` / `data_fetcher.py` →
  `MVPTracker/analysis/`（原樣不改）。
- 啟動時 `sys.path.insert(0, <analysis 目錄>)`，使這些檔內部的扁平 `import analyzers` /
  `import decision_engine` 能解析；`from config import QuantConfig` 則解析到 MVPTracker 根的
  `config.py`（root 已在 sys.path）。
- `data/fetcher.py`、`data/institutional.py` 為薄包裝，`from analysis import data_fetcher` 後
  收斂介面給 UI/scanner 使用。

> 原則：複製進來的 4 個檔**不修改任何分析邏輯**；只透過 wrapper 呼叫。

## 檔案結構

```
MVPTracker/
  main.py              # NiceGUI 進入點，ui.run(native=True)，分頁路由，啟動時初始化 DB/sys.path/富邦
  config.py            # 複製自 StockGOGOV2（QuantConfig）+ 附加 App 設定
  analysis/            # 複製進來、原樣不改
    analyzers.py
    advanced_analyzers.py
    decision_engine.py
    data_fetcher.py    # 含 RealtimePriceFetcher / WukongAPI / FubonMarketData / DataSourceManager
  data/
    fetcher.py         # 包裝 DataSourceManager：get_history / get_quote（富邦主、yfinance fallback）
    institutional.py   # 個股 /iibs 逐檔三大法人（移植自 main.py _analyze_chip_flow_wukong），
                       #   產出 chip_flow dict（相容 decision_engine._get_chip）；intraday_force()→None（v2）
    cache.py           # SQLite 兩層快取（重運算每日 / 盤中輕量）
  concept/
    db.py              # SQLite 連線與 schema 建立
    store.py           # 題材/子題材/成分股 CRUD；匯入匯出 concept_map.json
    master.py          # 股票母清單（代號→名稱/產業），驗證與自動補名
  scanner/
    theme_scanner.py   # 成分股跑分析→聚合題材層
  ui/
    theme.py           # 色彩/樣式 token（深色，移植自 StockGOGOV2/theme.py）
    components.py       # 共用：熱圖塊、成分股列、KPI卡、K線（ECharts）
    overview.py        # 總覽
    detail.py          # 題材明細
    stock_modal.py     # 個股彈窗
    watchlist.py       # 極簡自選股
  storage/
    app.db             # SQLite（gitignore）
    concept_map.json   # 由 Google Sheet 生成的種子 + 匯出/匯入用
  docs/specs/          # 本設計文件
```

## SQLite Schema

```sql
themes(id INTEGER PK, key TEXT UNIQUE, name TEXT, sort INTEGER, is_custom INTEGER DEFAULT 0)
sub_themes(id INTEGER PK, theme_id INTEGER, key TEXT, name TEXT, sort INTEGER)
constituents(id INTEGER PK, theme_id INTEGER, sub_theme_id INTEGER NULL,
             code TEXT, name TEXT, in_master INTEGER DEFAULT 1, sort INTEGER)
watchlist(id INTEGER PK, code TEXT, name TEXT, added_at TEXT)
scan_cache(code TEXT, kind TEXT, payload_json TEXT, updated_at TEXT, PRIMARY KEY(code, kind))
```

啟動時：若 DB 空，從 `storage/concept_map.json` 匯入種子（17 主類 + 機器人 + 軟體）。

## 種子資料 concept_map.json（由 Google Sheet 推導）

來源 Sheet 欄位：`theme_key, theme, sub_theme, code, name`。層級編碼在 `theme_key`：

- `01_ai_server` … `17_optics_sensor`：17 個主類，無子題材。
- `18_robot/<sub>`：母題材「機器人」，6 個子題材（傳動/關節核心、伺服馬達/驅動器、整機/系統整合、
  氣動/自動化元件、機構件/結構件、感測/視覺/大腦）。
- `19_software/<sub>`：母題材「軟體」，6 個子題材（系統整合/IT服務、資安、SaaS/雲端服務、
  金融科技/支付、遊戲、網路平台/服務）。

= 19 themes（17 + robot + software），共 12 sub_themes，約 145 檔成分股。

橋接 JSON 格式：

```json
{
  "version": 1,
  "exported_at": "2026-06-03",
  "themes": [
    {
      "key": "01_ai_server", "name": "AI/伺服器", "is_custom": false,
      "sub_themes": [],
      "constituents": [{"code": "2382", "name": "廣達"}, "..."]
    },
    {
      "key": "18_robot", "name": "機器人", "is_custom": false,
      "sub_themes": [
        {
          "key": "transmission_joint", "name": "傳動/關節核心",
          "constituents": [{"code": "2049", "name": "上銀"}, "..."]
        }
      ],
      "constituents": []
    }
  ]
}
```

匯入解析：`theme_key` 含 `/` → 切成 `parent_key` / `sub_key`，`theme` 欄為母題材名、`sub_theme` 欄為
子題材名；不含 `/` → 主類，`sub_theme_id = NULL`。

## 各頁規格（MVP）

### 總覽 overview.py
- 頂部 app bar：標題、來源/回看 chips、法人時間戳「截至 X 收盤」、更新時間、富邦登入狀態提示。
- 左側導覽軌：總覽 / 明細 / 自選；象限、報告（v2）disabled。
- KPI 卡 ×4：動能最強、法人最買超、加權指數、背離警示檔數。
- 題材熱度熱圖：每塊=題材，填色=5日動能（紅漲綠跌、色深=強），塊底金條=EOD 法人買超
  （空/綠=賣超），⚠=背離（動能與法人反向）。塊大小依成分股數。
- 排序、回看天數切換、熱圖↔列表切換。
- 就地新增題材：末尾「＋ 新題材」塊 → 彈出輸入（名稱、可選子題材）→ 寫 DB → 即時重繪。
- 點題材塊 → 進明細頁（帶 theme_id）。

### 題材明細 detail.py
- 麵包屑「題材總覽 › {題材}」+ 題材標頭（動能/法人/家數/綜合訊號）。
- 有子題材：可收合分組，組標題顯示子題材動能/法人/家數；無子題材：直接成分股表。
- 成分股列：代號/名稱、現價、今日漲幅、5日、RS、法人（張）、訊號 badge（A琥珀/B藍/C灰）。
- 就地加成分股：每個（子）題材底部「＋ 新增個股」→ 輸入代號→ master 自動帶名稱（查不到允許手動 +
  標記非母清單）→ 寫 DB → 即時更新；每列可移除。
- 點成分股列 → 開個股彈窗。

### 個股彈窗 stock_modal.py
- `ui.dialog`：股名/代號/現價漲跌（紅漲綠跌）。
- K線：`ui.echart` candlestick + 均線 + 量（副圖），富邦真實 OHLC（歷史+當日）。
- 指標 chips：RS、KD、均線排列、量能、法人連買。
- 按鈕：產生完整分析報告（呼叫現有報告產生器）、加入自選股。

### 極簡自選股 watchlist.py
- SQLite watchlist 表；列表顯示 + 移除；個股彈窗「加入自選」寫入這裡。（分組/掃描 v2）

## 資料源層 data/

- `fetcher.get_history(code)` / `get_quote(code)`：包裝 `DataSourceManager`
  （`get_history(symbol, market, period=...)`、`get_realtime_price/get_quote`）。富邦優先，失敗
  fallback yfinance。富邦 SDK 啟動時 `DataSourceManager.initialize(sdk)`（需登入；未登入只走
  fallback 並在 app bar 提示）。
- `institutional.chip_flow(code)`：打 `GET https://api.wukong.com.tw/stock/{code}/iibs`（逐檔），
  取最新筆 `foreign/trust/dealer/total`（張）與歷史算連續買/賣超天數，產出 `chip_flow` dict
  （相容 `decision_engine._get_chip`：`foreign_net / trust_net / dealer_net /
  foreign_consecutive_days / trust_consecutive_days / available / signal`）。移植自
  `main.py::_analyze_chip_flow_wukong`，邏輯不改。每檔每日快取於 `scan_cache`。
- 題材聚合：法人買超家數占比 = 成分股中 `total > 0` 的家數 ÷ 成分股數；個股淨買取 `total`。
- `institutional.intraday_force(code)`（盤中主力力道，v2）先回 `None`。

## scanner/theme_scanner.py

- `scan_theme(theme_id)`：取成分股 → 每檔 `fetcher.get_history` 取 OHLC + `institutional.chip_flow`
  取籌碼 → 餵 `decision_engine` / `analyzers` 算 grade（A/B/C）/RS/訊號（chip_flow 即 `_get_chip` 的
  輸入）→ 聚合題材層：5日動能（成分股平均/中位）、漲跌家數、強勢占比（買訊占比）、
  法人買超家數占比、綜合訊號、背離旗標（動能與法人反向）。
- 快取：重運算（RS/趨勢）走 `scan_cache` 每日；盤中只刷價格/漲幅。

## 視覺 / theme.py

**版面與配色以使用者提供的單一 mockup `mockups/MVPTracker_mockup.html` 為像素級依據**（步驟 2–4 照圖）。
該檔已定義完整 CSS 變數，`ui/theme.py` 直接沿用：

```
--bg #0E1116  --bar #131820  --rail #10151C  --card #1A1F27  --elev #1C222B
--line rgba(255,255,255,.07)  --text #E6E8EB  --t2 #9AA0A8  --t3 #6B7079
--up #F0696A(紅漲)  --down #4CB782(綠跌)  --accent #EF9F27(琥珀)  --inst #E8C45C(法人金)  --blue #5FA8E0(B級)
```

關鍵版面（取自 mockup）：
- App 容器 max-width 1180px、圓角卡片、頁底 #05070A。
- 頂 bar：`MVPTracker`（MVP 為 accent）+ chips（`concept_map N類`、`回看 5日`、`法人:截至 MM/DD 收盤` 金色）+ 更新時間。
- 左軌 80px：總覽 / 象限 / 明細 / 自選；active = accent 左邊條 + 淡底。
- 熱圖 tile：`flex` 權重 = 成分股數，背景色 = 5 段動能色（`momColor`：>=6 / >=3 / >=0 / >-3 / else），
  底部 5px 金條 = 法人買超寬度（賣超用綠色斜紋），右上 `⚠` = 背離；末尾虛線 `＋ 新題材` tile。
- 明細：crumb + thead（name + 5日動能/法人/家數 KV + 綜合訊號 badge）+ 可收合子題材 group；
  列 grid 欄位 `idx / 代號·名稱 / 現價 / 今日 / 5日 / RS / 法人(張) / 訊號 badge`；
  訊號 badge 色：A `#B23A36`、B `#2F5C7A`、C `#2A2F37`；每組底部 `＋ 新增個股到「X」`。
- 個股彈窗：header（股名代號 + 現價漲跌紅綠）+ K線（mockup 用 SVG；本案改 `ui.echart` candlestick + MA + 量）
  + 指標 chips（RS / KD / 均線 / 量能 / 法人連買）+ 按鈕（加入自選 / 產生完整分析報告）。

注意：mockup 含「象限」頁與其 nav，但依規格決策 3，**MVP 的象限/報告為 v2，nav 列出但 disabled**；
象限頁面內容不在本次實作。

## 環境 / 啟動

- Python 3.13.9。`pip install nicegui pywebview`（裝進跑後端的 system python3，才 import 得到
  fubon_neo / yfinance；兩者已確認可用）。
- `main.py` 用 `ui.run(native=True)` 開桌面視窗。
- 富邦 SDK 需登入憑證（`N124680879_*.p12` / `api_cert.pfx` 在 StockGOGOV2）；登入失敗則只走
  yfinance fallback 並提示。

## 建置順序（交付全 1–7，分步可回滾）

1. 專案骨架 + `config.py`（複製）+ `ui/theme.py` + `concept/db.py`（schema）+ 複製 4 後端檔 +
   由 Google Sheet 生成 `concept_map.json` 並匯入種子。
2. 總覽頁（假資料熱圖 + KPI + 新增題材）。
3. 明細頁（子題材分組 + 假成分股 + 加/移個股）。
4. 個股彈窗（假 K 線）。
5. 接 `master`（代號驗證/補名）、`fetcher`（富邦真資料）、`institutional`（個股 /iibs 逐檔法人）、
   `scanner`（聚合）。
6. 極簡自選股。
7. 驗收：四頁能跑、CRUD 寫入 SQLite 並即時反映、富邦資料進得來、紅漲綠跌與 A/B/C 配色正確、
   `ui.run(native=True)` 能開桌面視窗。打包（PyInstaller/nicegui-pack）列 v2。

## 限制與原則

- 不改 `analysis/` 內複製檔的分析邏輯；只呼叫。
- NiceGUI 狀態走 Python/SQLite，不用 localStorage。
- 小步可執行、可回滾：先全用假資料把四頁 + CRUD 跑起來，確認版面/互動，再逐一接
  `data` / `scanner` / `analysis`。

## 開放項

- 無。Mockup 已提供（單檔 `mockups/MVPTracker_mockup.html`，含全部頁面），視覺依據齊備。
