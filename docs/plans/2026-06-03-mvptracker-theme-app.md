# MVPTracker — NiceGUI 題材追蹤 App 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 NiceGUI 打造獨立自足的台股題材追蹤桌面 App，複製 StockGOGOV2 後端、先用假資料把四頁＋CRUD 跑起來，再逐步接富邦/法人/分析真資料。

**Architecture:** 三層 — (1) `analysis/` 為複製進來、原樣不改的後端（含從 main.py 抽出的 `QuickAnalyzer`）；(2) `concept/`、`data/`、`scanner/` 為薄包裝/聚合層，把後端收斂成 UI 需要的介面，狀態存 SQLite；(3) `ui/` 為 NiceGUI 頁面，依 `mockups/MVPTracker_mockup.html` 像素級實作。先全用假資料（步驟 1–4），再換真資料（步驟 5–7）。

**Tech Stack:** Python 3.13、NiceGUI（+ pywebview native）、SQLite、pandas/numpy、fubon_neo、yfinance、WukongAPI、ECharts（`ui.echart`）、pytest。

**參考文件：** 設計 spec 見 `docs/specs/2026-06-03-mvptracker-theme-app-design.md`；視覺依據 `mockups/MVPTracker_mockup.html`；種子資料來源 `/tmp/concept_seed.csv`（Google Sheet 匯出，151 列）。

**後端來源路徑：** `STOCKGOGO = /Users/jimlai/Desktop/Winho/Python/StockGOGOV2`。

---

## 檔案結構（建立 / 責任）

```
MVPTracker/
  main.py              # 進入點：sys.path、富邦初始化、DB 種子、ui.run(native)、頁面路由
  config.py            # 複製 STOCKGOGO/config.py（QuantConfig）+ 附加 App 設定（DB 路徑等）
  requirements.txt     # nicegui, pywebview（其餘後端依賴已在 system python）
  analysis/            # 複製、原樣不改（quick_analyzer.py 為抽出）
    __init__.py
    analyzers.py            # 複製
    advanced_analyzers.py   # 複製
    decision_engine.py      # 複製
    data_fetcher.py         # 複製（RealtimePriceFetcher/WukongAPI/FubonMarketData/DataSourceManager）
    backtesting.py          # 複製（QuickAnalyzer 依賴 BacktestEngine）
    database.py             # 複製（QuickAnalyzer.get_db 依賴 WatchlistDatabase）
    quick_analyzer.py       # 抽出：YFinanceRateLimiter + QuickAnalyzer（main.py 251–654, 1305–4115）
  concept/
    __init__.py
    db.py              # SQLite 連線 + schema
    store.py           # 題材/子題材/成分股 CRUD + import/export concept_map.json
    master.py          # 代號→名稱/產業 驗證與補名
  data/
    __init__.py
    fetcher.py         # 包裝 DataSourceManager.get_history/get_quote
    institutional.py   # 個股 /iibs 三大法人 → chip_flow（委派 QuickAnalyzer._analyze_chip_flow_wukong）
    cache.py           # scan_cache 讀寫（每日/盤中兩層）
  scanner/
    __init__.py
    theme_scanner.py   # scan_theme(theme_id)：QuickAnalyzer.analyze_stock 逐檔 → 聚合題材層
  ui/
    __init__.py
    theme.py           # 色彩 token（取自 mockup CSS 變數）+ grade_tag + 全域 CSS
    components.py      # heat tile、KPI 卡、成分股列、K線 echart 選項
    overview.py        # 總覽頁
    detail.py          # 題材明細頁
    stock_modal.py     # 個股彈窗
    watchlist.py       # 極簡自選股頁
  storage/
    concept_map.json   # 由 CSV 生成的種子（commit）
    app.db             # SQLite（gitignore）
  scripts/
    gen_seed.py        # CSV → concept_map.json 生成器
  tests/
    test_db.py test_seed.py test_store.py test_institutional.py
    test_master.py test_scanner.py test_watchlist.py
  docs/specs/ docs/plans/
```

---

# Phase 1 — 骨架 + 種子

### Task 1: 專案骨架與依賴

**Files:**
- Create: `requirements.txt`、各套件 `__init__.py`、`.gitignore`

- [ ] **Step 1: 建立目錄與套件檔**

```bash
cd /Users/jimlai/Desktop/Winho/Python/MVPTracker
mkdir -p analysis concept data scanner ui storage scripts tests
touch analysis/__init__.py concept/__init__.py data/__init__.py scanner/__init__.py ui/__init__.py tests/__init__.py
```

- [ ] **Step 2: 寫 requirements.txt**

```
nicegui>=1.4
pywebview>=4.4
```

- [ ] **Step 3: 寫 .gitignore**

```
__pycache__/
*.pyc
storage/app.db
.DS_Store
```

- [ ] **Step 4: 安裝 NiceGUI（裝進跑後端的 system python3）**

Run: `python3 -m pip install nicegui pywebview`
Expected: 安裝成功；`python3 -c "import nicegui, webview; print('ok')"` 印出 `ok`

- [ ] **Step 5: Commit**

```bash
git init && git add -A && git commit -m "chore: scaffold MVPTracker project structure"
```

> 註：MVPTracker 尚非 git repo，先 `git init`。

---

### Task 2: 複製後端 + 抽出 QuickAnalyzer

**Files:**
- Create（複製）: `config.py`、`analysis/{analyzers,advanced_analyzers,decision_engine,data_fetcher,backtesting,database}.py`
- Create（抽出）: `analysis/quick_analyzer.py`
- Test: `tests/test_imports.py`

- [ ] **Step 1: 複製 6 個後端檔 + config.py**

```bash
cd /Users/jimlai/Desktop/Winho/Python/MVPTracker
S=/Users/jimlai/Desktop/Winho/Python/StockGOGOV2
cp "$S/config.py" config.py
for f in analyzers advanced_analyzers decision_engine data_fetcher backtesting database; do cp "$S/$f.py" "analysis/$f.py"; done
```

- [ ] **Step 2: 抽出 quick_analyzer.py（import 標頭 + 兩個類別）**

建立 `analysis/quick_analyzer.py`，內容＝下列 import 標頭，後接從 `main.py` 逐字複製的
`class YFinanceRateLimiter`（行 251–654）與 `class QuickAnalyzer`（行 1305–4115）。

```python
"""quick_analyzer.py — 從 StockGOGOV2/main.py 抽出的純分析引擎（無 GUI 耦合）。
複製，不改邏輯。提供 QuickAnalyzer.analyze_stock(symbol, market='台股') → result dict。"""
import datetime, threading, time, warnings, gc, sys, os
import yfinance as yf
import mplfinance as mpf
import pandas as pd
import numpy as np
from scipy.stats import percentileofscore
import twstock
import requests
from config import QuantConfig
from data_fetcher import RealtimePriceFetcher, WukongAPI, DataSourceManager, FubonMarketData
from analyzers import (DecisionMatrix, WaveAnalyzer, MeanReversionAnalyzer,
                       MarketRegimeAnalyzer, CorrelationAnalyzer,
                       calculate_sma, calculate_bollinger_bands, calculate_macd,
                       calculate_rsi, calculate_kd, analyze_volume_price_relation)
from backtesting import BacktestEngine
from database import WatchlistDatabase

# <<< 貼上 main.py 251–654 的 class YFinanceRateLimiter（逐字） >>>
# <<< 貼上 main.py 1305–4115 的 class QuickAnalyzer（逐字） >>>
```

抽取指令（可輔助核對行號）：

```bash
S=/Users/jimlai/Desktop/Winho/Python/StockGOGOV2
sed -n '251,654p' "$S/main.py"    # YFinanceRateLimiter（確認結尾無越界到 fubon_trading try/except）
sed -n '1305,4115p' "$S/main.py"  # QuickAnalyzer
```

> 注意：`get_yf_ticker()` 是 `analyze_stock` 內的 nested function，會隨類別一起帶入，不需另外處理。
> 不要複製 main.py 655–668 的 `fubon_trading` import fallback（含 `messagebox`），那是模組級 GUI 程式碼，QuickAnalyzer 不需要。

- [ ] **Step 3: 寫匯入冒煙測試**

```python
# tests/test_imports.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "analysis"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

def test_backend_imports():
    import analyzers, advanced_analyzers, decision_engine, data_fetcher, backtesting, database
    from quick_analyzer import QuickAnalyzer, YFinanceRateLimiter
    assert hasattr(QuickAnalyzer, "analyze_stock")
    from config import QuantConfig
    assert QuantConfig is not None
```

- [ ] **Step 4: 跑測試確認可匯入**

Run: `cd /Users/jimlai/Desktop/Winho/Python/MVPTracker && python3 -m pytest tests/test_imports.py -v`
Expected: PASS（若 fail，多半是 YFinanceRateLimiter 結尾行號越界，調整 sed 範圍至 class 真正結束行）

- [ ] **Step 5: Commit**

```bash
git add config.py analysis/ tests/test_imports.py && git commit -m "feat: vendor StockGOGOV2 backend + extract QuickAnalyzer"
```

---

### Task 3: ui/theme.py — 色彩 token + grade_tag + 全域 CSS

**Files:**
- Create: `ui/theme.py`
- Test: `tests/test_theme.py`

- [ ] **Step 1: 寫 grade_tag 測試（純文字分級，移植自 StockGOGOV2/theme.py）**

```python
# tests/test_theme.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ui.theme import grade_tag

def test_grade_tag():
    assert grade_tag("A級主攻") == "grade_A"
    assert grade_tag("建議買進 追蹤") == "grade_B"
    assert grade_tag("觀察") == "grade_C"
    assert grade_tag("暫緩買進") == "grade_sell"
    assert grade_tag("") is None
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 -m pytest tests/test_theme.py -v`
Expected: FAIL（`ModuleNotFoundError: ui.theme`）

- [ ] **Step 3: 寫 ui/theme.py**

```python
# ui/theme.py — 色彩 token（取自 mockups/MVPTracker_mockup.html）+ 分級 + 全域 CSS
from nicegui import ui

# 取自 mockup :root
BG="#0E1116"; BAR="#131820"; RAIL="#10151C"; CARD="#1A1F27"; ELEV="#1C222B"
LINE="rgba(255,255,255,0.07)"; TEXT="#E6E8EB"; T2="#9AA0A8"; T3="#6B7079"
UP="#F0696A"; DOWN="#4CB782"; ACCENT="#EF9F27"; INST="#E8C45C"; BLUE="#5FA8E0"
PAGE_BG="#05070A"
# 訊號 badge 配色（明細列）
BADGE = {"grade_A": ("#B23A36", "#FFE9E8"), "grade_B": ("#2F5C7A", "#D6ECFB"),
         "grade_C": ("#2A2F37", "#9AA0A8"), "grade_sell": ("#327A3C", "#E6F4E8")}

def momentum_color(m: float):
    """5 段動能填色（背景, 前景），取自 mockup momColor。"""
    if m >= 6: return ("#B23A36", "#FFE9E8")
    if m >= 3: return ("#C5524C", "#FFECEA")
    if m >= 0: return ("#7E4A48", "#F6DEDC")
    if m > -3: return ("#46714C", "#E2F1E4")
    return ("#327A3C", "#E6F4E8")

def grade_tag(signal_text):
    """純文字分級（移植自 StockGOGOV2/theme.py，邏輯不改）。"""
    if not signal_text:
        return None
    s = str(signal_text)
    if any(k in s for k in ("賣出","避開","減碼","出場","暫緩","不建議")):
        return "grade_sell"
    if ("A級" in s) or ("A 級" in s) or ("主攻" in s) or ("強烈建議買進" in s) or ("強力買進" in s):
        return "grade_A"
    if ("B級" in s) or ("B 級" in s) or ("追蹤" in s) or ("建議買進" in s):
        return "grade_B"
    if ("C級" in s) or ("C 級" in s) or ("觀察" in s) or ("等待" in s) or ("觀望" in s):
        return "grade_C"
    return None

def apply_global():
    """注入全域 CSS 變數與字體（依 mockup）。在 main.py 啟動時呼叫一次。"""
    ui.add_head_html(f"""
    <style>
      :root {{
        --bg:{BG}; --bar:{BAR}; --rail:{RAIL}; --card:{CARD}; --elev:{ELEV};
        --line:{LINE}; --text:{TEXT}; --t2:{T2}; --t3:{T3};
        --up:{UP}; --down:{DOWN}; --accent:{ACCENT}; --inst:{INST}; --blue:{BLUE};
        --mono:"SF Mono","Menlo","JetBrains Mono","Consolas",monospace;
        --sans:"PingFang TC","Microsoft JhengHei UI","Noto Sans TC","Segoe UI",system-ui,sans-serif;
      }}
      body {{ background:{PAGE_BG}; color:var(--text); font-family:var(--sans); }}
      .mono {{ font-family:var(--mono); }}
      .up {{ color:var(--up); }} .down {{ color:var(--down); }}
      .muted {{ color:var(--t2); }} .gold {{ color:var(--inst); }}
    </style>
    """)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python3 -m pytest tests/test_theme.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ui/theme.py tests/test_theme.py && git commit -m "feat: ui theme tokens, momentum color, grade_tag"
```

---

### Task 4: concept/db.py — SQLite schema

**Files:**
- Create: `concept/db.py`
- Test: `tests/test_db.py`

- [ ] **Step 1: 寫 schema 測試**

```python
# tests/test_db.py
import sys, os, sqlite3
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from concept.db import connect, init_schema

def test_schema_tables(tmp_path):
    con = connect(str(tmp_path / "t.db"))
    init_schema(con)
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"themes","sub_themes","constituents","watchlist","scan_cache"} <= names

def test_scan_cache_pk(tmp_path):
    con = connect(str(tmp_path / "t.db")); init_schema(con)
    con.execute("INSERT INTO scan_cache(code,kind,payload_json,updated_at) VALUES('2330','grade','{}','t')")
    con.execute("INSERT OR REPLACE INTO scan_cache(code,kind,payload_json,updated_at) VALUES('2330','grade','{\"a\":1}','t2')")
    con.commit()
    rows = con.execute("SELECT payload_json FROM scan_cache WHERE code='2330' AND kind='grade'").fetchall()
    assert len(rows) == 1 and rows[0][0] == '{"a":1}'
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 -m pytest tests/test_db.py -v`
Expected: FAIL（`ModuleNotFoundError: concept.db`）

- [ ] **Step 3: 寫 concept/db.py**

```python
# concept/db.py
import os, sqlite3

DEFAULT_DB = os.path.join(os.path.dirname(__file__), "..", "storage", "app.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS themes(
  id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE, name TEXT,
  sort INTEGER DEFAULT 0, is_custom INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS sub_themes(
  id INTEGER PRIMARY KEY AUTOINCREMENT, theme_id INTEGER, key TEXT, name TEXT, sort INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS constituents(
  id INTEGER PRIMARY KEY AUTOINCREMENT, theme_id INTEGER, sub_theme_id INTEGER,
  code TEXT, name TEXT, in_master INTEGER DEFAULT 1, sort INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS watchlist(
  id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT, name TEXT, added_at TEXT);
CREATE TABLE IF NOT EXISTS scan_cache(
  code TEXT, kind TEXT, payload_json TEXT, updated_at TEXT, PRIMARY KEY(code, kind));
"""

def connect(db_path: str = DEFAULT_DB) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    con = sqlite3.connect(db_path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con

def init_schema(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA)
    con.commit()

def is_empty(con: sqlite3.Connection) -> bool:
    return con.execute("SELECT COUNT(*) FROM themes").fetchone()[0] == 0
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python3 -m pytest tests/test_db.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add concept/db.py tests/test_db.py && git commit -m "feat: SQLite schema + connection"
```

---

### Task 5: 生成種子 concept_map.json

**Files:**
- Create: `scripts/gen_seed.py`、`storage/concept_map.json`
- Test: `tests/test_seed.py`

- [ ] **Step 1: 寫 theme_key 解析測試**

```python
# tests/test_seed.py
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from gen_seed import parse_key, build_concept_map

def test_parse_key():
    assert parse_key("01_ai_server") == ("01_ai_server", None)
    assert parse_key("18_robot/servo_motor") == ("18_robot", "servo_motor")

def test_build_structure():
    rows = [
        {"theme_key":"01_ai_server","theme":"AI/伺服器","sub_theme":"","code":"2382","name":"廣達"},
        {"theme_key":"18_robot/servo_motor","theme":"機器人","sub_theme":"伺服馬達/驅動器","code":"2308","name":"台達電"},
    ]
    cm = build_concept_map(rows)
    keys = {t["key"] for t in cm["themes"]}
    assert keys == {"01_ai_server","18_robot"}
    robot = next(t for t in cm["themes"] if t["key"]=="18_robot")
    assert robot["sub_themes"][0]["key"] == "servo_motor"
    assert robot["sub_themes"][0]["constituents"][0]["code"] == "2308"
    ai = next(t for t in cm["themes"] if t["key"]=="01_ai_server")
    assert ai["sub_themes"] == [] and ai["constituents"][0]["code"] == "2382"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 -m pytest tests/test_seed.py -v`
Expected: FAIL（`ModuleNotFoundError: gen_seed`）

- [ ] **Step 3: 寫 scripts/gen_seed.py**

```python
# scripts/gen_seed.py — CSV → concept_map.json
import csv, json, sys, os, datetime

def parse_key(theme_key: str):
    if "/" in theme_key:
        parent, sub = theme_key.split("/", 1)
        return parent, sub
    return theme_key, None

def build_concept_map(rows):
    themes = {}   # parent_key -> theme dict
    order = []
    for r in rows:
        pkey, skey = parse_key(r["theme_key"].strip())
        if pkey not in themes:
            themes[pkey] = {"key": pkey, "name": r["theme"].strip(), "is_custom": False,
                            "sub_themes": [], "constituents": [], "_subidx": {}}
            order.append(pkey)
        t = themes[pkey]
        cons = {"code": r["code"].strip(), "name": r["name"].strip()}
        if skey:
            if skey not in t["_subidx"]:
                sub = {"key": skey, "name": r["sub_theme"].strip(), "constituents": []}
                t["_subidx"][skey] = sub; t["sub_themes"].append(sub)
            t["_subidx"][skey]["constituents"].append(cons)
        else:
            t["constituents"].append(cons)
    for t in themes.values():
        t.pop("_subidx", None)
    return {"version": 1, "exported_at": datetime.date.today().isoformat(),
            "themes": [themes[k] for k in order]}

def main(csv_path, out_path):
    with open(csv_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    cm = build_concept_map(rows)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(cm, f, ensure_ascii=False, indent=2)
    print(f"wrote {len(cm['themes'])} themes -> {out_path}")

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
```

- [ ] **Step 4: 跑測試確認通過 + 生成正式種子檔**

Run: `python3 -m pytest tests/test_seed.py -v`
Expected: PASS

Run: `python3 scripts/gen_seed.py /tmp/concept_seed.csv storage/concept_map.json`
Expected: `wrote 19 themes -> storage/concept_map.json`（17 主類 + 18_robot + 19_software）

- [ ] **Step 5: Commit**

```bash
git add scripts/gen_seed.py storage/concept_map.json tests/test_seed.py
git commit -m "feat: generate concept_map.json seed from sheet CSV"
```

---

### Task 6: concept/store.py — import + CRUD

**Files:**
- Create: `concept/store.py`
- Test: `tests/test_store.py`

- [ ] **Step 1: 寫 import + CRUD 測試**

```python
# tests/test_store.py
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from concept.db import connect, init_schema
from concept import store

SEED = {"version":1,"themes":[
  {"key":"01_ai_server","name":"AI/伺服器","is_custom":False,"sub_themes":[],
   "constituents":[{"code":"2382","name":"廣達"}]},
  {"key":"18_robot","name":"機器人","is_custom":False,"constituents":[],
   "sub_themes":[{"key":"servo_motor","name":"伺服馬達/驅動器",
                  "constituents":[{"code":"2308","name":"台達電"}]}]}]}

def _con(tmp_path):
    con = connect(str(tmp_path/"t.db")); init_schema(con); return con

def test_import_seed(tmp_path):
    con=_con(tmp_path); store.import_concept_map(con, SEED)
    themes = store.list_themes(con)
    assert len(themes)==2
    robot = store.get_theme_by_key(con,"18_robot")
    subs = store.list_sub_themes(con, robot["id"])
    assert subs[0]["name"]=="伺服馬達/驅動器"
    cons = store.list_constituents(con, robot["id"], subs[0]["id"])
    assert cons[0]["code"]=="2308"

def test_add_theme_and_constituent(tmp_path):
    con=_con(tmp_path); store.import_concept_map(con, SEED)
    tid = store.add_theme(con, name="新題材", is_custom=True)
    store.add_constituent(con, theme_id=tid, code="9999", name="測試", in_master=0)
    cons = store.list_constituents(con, tid, None)
    assert cons[0]["code"]=="9999" and cons[0]["in_master"]==0

def test_remove_constituent(tmp_path):
    con=_con(tmp_path); store.import_concept_map(con, SEED)
    ai = store.get_theme_by_key(con,"01_ai_server")
    cons = store.list_constituents(con, ai["id"], None)
    store.remove_constituent(con, cons[0]["id"])
    assert store.list_constituents(con, ai["id"], None) == []

def test_export_roundtrip(tmp_path):
    con=_con(tmp_path); store.import_concept_map(con, SEED)
    exported = store.export_concept_map(con)
    assert {t["key"] for t in exported["themes"]} == {"01_ai_server","18_robot"}
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 -m pytest tests/test_store.py -v`
Expected: FAIL（`ModuleNotFoundError: concept.store`）

- [ ] **Step 3: 寫 concept/store.py**

```python
# concept/store.py — 題材/子題材/成分股 CRUD + concept_map.json import/export
import datetime

def import_concept_map(con, cm: dict):
    for ti, t in enumerate(cm.get("themes", [])):
        cur = con.execute("INSERT OR IGNORE INTO themes(key,name,sort,is_custom) VALUES(?,?,?,?)",
                          (t["key"], t["name"], ti, 1 if t.get("is_custom") else 0))
        theme_id = con.execute("SELECT id FROM themes WHERE key=?", (t["key"],)).fetchone()["id"]
        for ci, c in enumerate(t.get("constituents", [])):
            con.execute("INSERT INTO constituents(theme_id,sub_theme_id,code,name,in_master,sort) VALUES(?,?,?,?,1,?)",
                        (theme_id, None, c["code"], c["name"], ci))
        for si, s in enumerate(t.get("sub_themes", [])):
            con.execute("INSERT INTO sub_themes(theme_id,key,name,sort) VALUES(?,?,?,?)",
                        (theme_id, s["key"], s["name"], si))
            sub_id = con.execute("SELECT id FROM sub_themes WHERE theme_id=? AND key=?",
                                 (theme_id, s["key"])).fetchone()["id"]
            for ci, c in enumerate(s.get("constituents", [])):
                con.execute("INSERT INTO constituents(theme_id,sub_theme_id,code,name,in_master,sort) VALUES(?,?,?,?,1,?)",
                            (theme_id, sub_id, c["code"], c["name"], ci))
    con.commit()

def list_themes(con):
    return [dict(r) for r in con.execute("SELECT * FROM themes ORDER BY sort, id")]

def get_theme_by_key(con, key):
    r = con.execute("SELECT * FROM themes WHERE key=?", (key,)).fetchone()
    return dict(r) if r else None

def get_theme(con, theme_id):
    r = con.execute("SELECT * FROM themes WHERE id=?", (theme_id,)).fetchone()
    return dict(r) if r else None

def list_sub_themes(con, theme_id):
    return [dict(r) for r in con.execute("SELECT * FROM sub_themes WHERE theme_id=? ORDER BY sort, id", (theme_id,))]

def list_constituents(con, theme_id, sub_theme_id):
    if sub_theme_id is None:
        rows = con.execute("SELECT * FROM constituents WHERE theme_id=? AND sub_theme_id IS NULL ORDER BY sort, id", (theme_id,))
    else:
        rows = con.execute("SELECT * FROM constituents WHERE theme_id=? AND sub_theme_id=? ORDER BY sort, id", (theme_id, sub_theme_id))
    return [dict(r) for r in rows]

def add_theme(con, name, key=None, is_custom=True):
    key = key or ("custom_" + datetime.datetime.now().strftime("%Y%m%d%H%M%S"))
    sort = (con.execute("SELECT COALESCE(MAX(sort),0)+1 FROM themes").fetchone()[0])
    con.execute("INSERT INTO themes(key,name,sort,is_custom) VALUES(?,?,?,?)", (key, name, sort, 1 if is_custom else 0))
    con.commit()
    return con.execute("SELECT id FROM themes WHERE key=?", (key,)).fetchone()["id"]

def add_sub_theme(con, theme_id, name, key=None):
    key = key or ("sub_" + datetime.datetime.now().strftime("%Y%m%d%H%M%S"))
    sort = con.execute("SELECT COALESCE(MAX(sort),0)+1 FROM sub_themes WHERE theme_id=?", (theme_id,)).fetchone()[0]
    con.execute("INSERT INTO sub_themes(theme_id,key,name,sort) VALUES(?,?,?,?)", (theme_id, key, name, sort))
    con.commit()
    return con.execute("SELECT id FROM sub_themes WHERE theme_id=? AND key=?", (theme_id, key)).fetchone()["id"]

def add_constituent(con, theme_id, code, name, sub_theme_id=None, in_master=1):
    sort = con.execute("SELECT COALESCE(MAX(sort),0)+1 FROM constituents WHERE theme_id=?", (theme_id,)).fetchone()[0]
    con.execute("INSERT INTO constituents(theme_id,sub_theme_id,code,name,in_master,sort) VALUES(?,?,?,?,?,?)",
                (theme_id, sub_theme_id, code, name, in_master, sort))
    con.commit()

def remove_constituent(con, constituent_id):
    con.execute("DELETE FROM constituents WHERE id=?", (constituent_id,)); con.commit()

def export_concept_map(con):
    out = {"version": 1, "exported_at": datetime.date.today().isoformat(), "themes": []}
    for t in list_themes(con):
        td = {"key": t["key"], "name": t["name"], "is_custom": bool(t["is_custom"]),
              "sub_themes": [], "constituents": [
                  {"code": c["code"], "name": c["name"]} for c in list_constituents(con, t["id"], None)]}
        for s in list_sub_themes(con, t["id"]):
            td["sub_themes"].append({"key": s["key"], "name": s["name"],
                "constituents": [{"code": c["code"], "name": c["name"]} for c in list_constituents(con, t["id"], s["id"])]})
        out["themes"].append(td)
    return out
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python3 -m pytest tests/test_store.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add concept/store.py tests/test_store.py && git commit -m "feat: concept store CRUD + concept_map import/export"
```

---

# Phase 2 — 總覽頁（假資料）

### Task 7: scanner 介面 + 假資料 provider

**Files:**
- Create: `scanner/theme_scanner.py`（先含假資料模式）
- Test: `tests/test_scanner.py`

- [ ] **Step 1: 寫 ThemeMetrics dataclass + mock 測試**

```python
# tests/test_scanner.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scanner.theme_scanner import ThemeMetrics, mock_overview

def test_mock_overview_shape():
    metrics = mock_overview()
    assert len(metrics) >= 5
    m = metrics[0]
    assert isinstance(m, ThemeMetrics)
    assert hasattr(m, "momentum_5d") and hasattr(m, "inst_net") and hasattr(m, "count")
    assert isinstance(m.diverge, bool)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 -m pytest tests/test_scanner.py -v`
Expected: FAIL

- [ ] **Step 3: 寫 scanner/theme_scanner.py（介面 + 假資料）**

```python
# scanner/theme_scanner.py
from dataclasses import dataclass

@dataclass
class ThemeMetrics:
    theme_id: int
    key: str
    name: str
    momentum_5d: float      # 5日動能 %
    inst_net: float         # 法人買超強度（-100..100，正=買超）
    count: int              # 成分股數
    up_count: int = 0
    down_count: int = 0
    strong_ratio: float = 0.0
    signal: str = ""
    diverge: bool = False

def is_diverge(m: float, inst: float) -> bool:
    return (m > 0 and inst < -20) or (m < 0 and inst > 20)

def mock_overview():
    """假資料（取自 mockup themes 陣列），步驟 5 換成 real_overview。"""
    raw = [("先進封裝",8.4,70,8),("AI/伺服器",6.7,55,16),("機器人",5.2,25,20),("散熱",4.1,60,6),
           ("半導體設備",3.3,20,7),("軟體",2.9,-15,30),("光通訊",2.6,40,10),("IC設計",1.6,10,6),
           ("記憶體",1.1,-45,9),("連接器",0.8,5,4),("晶圓代工",0.4,-20,3),("電源管理",-0.4,-10,5),
           ("PCB",-1.2,-55,16),("低軌衛星",-1.8,35,9),("被動元件",-3.1,-70,10)]
    out=[]
    for i,(n,m,inst,c) in enumerate(raw):
        out.append(ThemeMetrics(theme_id=i+1, key=f"mock_{i}", name=n, momentum_5d=m,
                                inst_net=inst, count=c, diverge=is_diverge(m,inst)))
    return out
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python3 -m pytest tests/test_scanner.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scanner/theme_scanner.py tests/test_scanner.py && git commit -m "feat: scanner ThemeMetrics interface + mock overview"
```

---

### Task 8: ui/components.py — 熱圖塊 + KPI 卡

**Files:**
- Create: `ui/components.py`

- [ ] **Step 1: 寫 heat_tile / kpi_card / add_tile**

```python
# ui/components.py
from nicegui import ui
from ui import theme

def kpi_card(label, value, sub, sub_class="muted"):
    with ui.element("div").style("background:var(--card);border-radius:12px;padding:14px 16px;"):
        ui.label(label).style("font-size:11px;color:var(--t2);")
        ui.label(value).style("font-size:18px;font-weight:700;margin-top:4px;")
        ui.label(sub).classes(sub_class).style("font-size:12px;margin-top:2px;font-family:var(--mono);")

def heat_tile(m, on_click):
    bg, fg = theme.momentum_color(m.momentum_5d)
    flex = max(4, m.count)
    with ui.element("div").style(
        f"position:relative;height:72px;border-radius:9px;padding:9px 11px 12px;flex:{flex} 1 102px;"
        f"min-width:104px;background:{bg};cursor:pointer;overflow:hidden;display:flex;flex-direction:column;justify-content:space-between;"
    ).on("click", lambda e, t=m: on_click(t)) as tile:
        ui.label(m.name).style(f"font-size:13px;font-weight:600;color:{fg};")
        sign = "+" if m.momentum_5d >= 0 else ""
        ui.label(f"{sign}{m.momentum_5d:.1f}%").style(f"font-size:13px;font-family:var(--mono);color:{fg};")
        # 底部金條（法人買超寬度；賣超用綠斜紋）
        if m.inst_net >= 0:
            fill = f"width:{min(100, m.inst_net)}%;background:var(--inst);"
        else:
            fill = (f"width:{min(100, -m.inst_net)}%;"
                    "background:repeating-linear-gradient(90deg,#4CB78255,#4CB78255 3px,transparent 3px,transparent 6px);")
        ui.element("div").style("position:absolute;left:0;bottom:0;height:5px;width:100%;background:rgba(255,255,255,0.06);")
        ui.element("div").style(f"position:absolute;left:0;bottom:0;height:5px;{fill}")
        if m.diverge:
            ui.label("⚠").style("position:absolute;top:6px;right:8px;font-size:12px;")
    return tile

def add_tile(on_click):
    el = ui.element("div").style(
        "height:72px;border:1px dashed rgba(255,255,255,0.18);border-radius:9px;display:flex;"
        "align-items:center;justify-content:center;color:var(--t2);font-size:13px;cursor:pointer;flex:6 1 102px;min-width:104px;")
    with el:
        ui.label("＋ 新題材")
    el.on("click", lambda e: on_click())
    return el
```

- [ ] **Step 2: 語法冒煙檢查**

Run: `python3 -c "import sys; sys.path.insert(0,'.'); import ui.components; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add ui/components.py && git commit -m "feat: ui components heat tile + kpi card"
```

---

### Task 9: ui/overview.py — 總覽頁

**Files:**
- Create: `ui/overview.py`

- [ ] **Step 1: 寫 render(container, con, on_open_theme, get_metrics)**

```python
# ui/overview.py
from nicegui import ui
from ui import components
from concept import store

def render(con, on_open_theme, get_metrics, on_theme_changed):
    """總覽頁。get_metrics() → List[ThemeMetrics]；on_open_theme(theme_id)。"""
    metrics = get_metrics()
    # KPI ×4
    strongest = max(metrics, key=lambda m: m.momentum_5d)
    most_inst = max(metrics, key=lambda m: m.inst_net)
    diverge_n = sum(1 for m in metrics if m.diverge)
    with ui.element("div").style("display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:18px;"):
        components.kpi_card("動能最強", strongest.name, f"5日 +{strongest.momentum_5d:.1f}%", "up")
        components.kpi_card("法人最買超", most_inst.name, "外資+投信 淨買", "gold")
        components.kpi_card("加權指數", "—", "接後端後顯示", "muted")
        components.kpi_card("背離警示", f"{diverge_n} 檔", "價量籌不一致", "muted")
    # section header + legend
    with ui.element("div").style("display:flex;align-items:center;justify-content:space-between;margin:6px 0 12px;"):
        ui.label("題材熱度 ＋ 法人").style("font-size:13px;color:#C9CDD2;font-weight:600;")
    # heatmap
    with ui.element("div").style("display:flex;flex-wrap:wrap;gap:7px;") as heat:
        for m in metrics:
            components.heat_tile(m, on_open_theme)
        components.add_tile(lambda: _new_theme_dialog(con, on_theme_changed))
    ui.label("填色=5日動能(紅漲綠跌)；塊底金條=法人買超；⚠=背離。點題材→明細；點「＋ 新題材」就地新增。"
             ).style("font-size:11px;color:var(--t3);margin-top:12px;line-height:1.6;")

def _new_theme_dialog(con, on_theme_changed):
    with ui.dialog() as dlg, ui.card().style("background:var(--card);"):
        ui.label("新增題材").style("font-weight:600;")
        name = ui.input("題材名稱")
        subs = ui.input("子題材（逗號分隔，可空）")
        with ui.row():
            ui.button("取消", on_click=dlg.close).props("flat")
            def save():
                if not name.value:
                    return
                tid = store.add_theme(con, name=name.value, is_custom=True)
                for s in [x.strip() for x in (subs.value or "").split(",") if x.strip()]:
                    store.add_sub_theme(con, tid, name=s)
                dlg.close(); on_theme_changed()
            ui.button("新增", on_click=save).props("color=amber")
    dlg.open()
```

- [ ] **Step 2: 語法冒煙檢查**

Run: `python3 -c "import sys; sys.path.insert(0,'.'); import ui.overview; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add ui/overview.py && git commit -m "feat: overview page (heatmap + KPI + new-theme dialog)"
```

---

### Task 10: main.py — app shell、路由、ui.run(native)

**Files:**
- Create: `main.py`

- [ ] **Step 1: 寫 main.py（sys.path、DB 種子、app bar、左軌、頁面切換）**

```python
# main.py
import os, sys, json
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)                       # config 等頂層模組
sys.path.insert(0, os.path.join(ROOT, "analysis"))  # 讓 vendored 後端扁平 import 解析

from nicegui import ui
from concept import db as condb, store
from ui import theme, overview
from scanner import theme_scanner

con = condb.connect(); condb.init_schema(con)
if condb.is_empty(con):
    with open(os.path.join(ROOT, "storage", "concept_map.json"), encoding="utf-8") as f:
        store.import_concept_map(con, json.load(f))

state = {"page": "overview", "theme_id": None}

def get_metrics():
    return theme_scanner.mock_overview()   # 步驟 5 換 real_overview(con)

@ui.page("/")
def index():
    theme.apply_global()
    with ui.element("div").style("max-width:1180px;margin:0 auto;background:var(--bg);border-radius:14px;overflow:hidden;"):
        _app_bar()
        with ui.element("div").style("display:flex;"):
            _rail()
            content = ui.element("div").style("flex:1;padding:18px;min-width:0;")
        _render_page(content)

def _app_bar():
    with ui.element("div").style("display:flex;align-items:center;gap:10px;padding:13px 18px;background:var(--bar);flex-wrap:wrap;"):
        ui.html('<span style="font-size:16px;font-weight:700;"><span style="color:var(--accent)">MVP</span>Tracker</span>')
        n = len(store.list_themes(con))
        for txt in (f"concept_map {n}類", "回看 5日"):
            ui.label(txt).style("font-size:11px;color:var(--t2);background:var(--elev);border-radius:999px;padding:4px 11px;")
        ui.label("法人:接後端後顯示").classes("gold").style("font-size:11px;background:var(--elev);border-radius:999px;padding:4px 11px;")

def _rail():
    items = [("overview","總覽",False),("quadrant","象限",True),("detail","明細",False),("watch","自選",False)]
    with ui.element("div").style("width:80px;background:var(--rail);padding:14px 0;display:flex;flex-direction:column;gap:6px;"):
        for key,label,disabled in items:
            active = state["page"]==key
            color = "var(--accent)" if active else ("#4A4F57" if disabled else "#7E848C")
            el = ui.element("div").style(
                f"display:flex;flex-direction:column;align-items:center;gap:4px;padding:11px 0;color:{color};"
                f"border-left:3px solid {'var(--accent)' if active else 'transparent'};font-size:10.5px;"
                f"{'background:rgba(239,159,39,0.10);' if active else ''}{'opacity:0.5;' if disabled else 'cursor:pointer;'}")
            with el:
                ui.label(label)
            if not disabled:
                el.on("click", lambda e,k=key: _go(k))

def _go(page, theme_id=None):
    state["page"]=page; state["theme_id"]=theme_id; ui.navigate.to("/")

def _render_page(content):
    with content:
        if state["page"]=="overview":
            overview.render(con, on_open_theme=lambda m:_go("detail", m.theme_id),
                            get_metrics=get_metrics, on_theme_changed=lambda:_go("overview"))
        elif state["page"]=="detail":
            ui.label("明細頁（Task 11 實作）")
        elif state["page"]=="watch":
            ui.label("自選頁（Task 19 實作）")

ui.run(native=True, title="MVPTracker", window_size=(1240, 860), reload=False)
```

- [ ] **Step 2: 啟動冒煙（headless 開不了 native，用瀏覽器模式確認頁面渲染）**

臨時把最後一行改 `ui.run(native=False, port=8111, reload=False, show=False)`，背景啟動：
Run: `python3 main.py & sleep 6 && curl -s localhost:8111 | grep -c MVPTracker && kill %1`
Expected: 輸出 `>=1`（頁面含 MVPTracker），無 traceback

- [ ] **Step 3: 還原 native=True 並用 Claude Preview / 截圖人工確認**

啟動 App，確認：頂 bar、左軌（象限 disabled）、KPI ×4、熱圖塊（成分股數＝塊寬、紅綠填色、底部金條、⚠）、「＋ 新題材」可開 dialog 並新增後熱圖出現新塊。

- [ ] **Step 4: Commit**

```bash
git add main.py && git commit -m "feat: app shell, rail nav, overview routing, ui.run native"
```

---

# Phase 3 — 題材明細（假資料）

### Task 11: ui/detail.py — 子題材分組 + 成分股列

**Files:**
- Create: `ui/detail.py`
- Modify: `main.py`（detail 分支改呼叫 `detail.render`）

- [ ] **Step 1: 寫 render(con, theme_id, get_row, on_open_stock, on_changed)**

成分股列欄位（依 mockup grid）：idx / 代號·名稱 / 現價 / 今日% / 5日% / RS / 法人(張) / 訊號 badge。
`get_row(code)` 回 dict（假資料階段回 mock；步驟 5 換真）：`{price, today_pct, d5_pct, rs, inst, signal}`。

```python
# ui/detail.py
from nicegui import ui
from concept import store
from ui import theme

def _mock_row(code):
    import random; random.seed(hash(code)%9999)
    t=round(random.uniform(-3,4),1); d5=round(random.uniform(-5,9),1); rs=random.randint(40,95)
    inst=random.randint(-800,1500)
    sig = "可進場 A" if rs>=80 else ("偏多 續抱" if rs>=65 else "觀察")
    return {"price": round(random.uniform(40,1000),1), "today_pct":t, "d5_pct":d5, "rs":rs, "inst":inst, "signal":sig}

def render(con, theme_id, on_open_stock, on_changed, get_row=_mock_row):
    t = store.get_theme(con, theme_id)
    if not t:
        ui.label("題材不存在"); return
    ui.html(f'<div style="font-size:12px;color:var(--t2);margin-bottom:12px;">題材總覽 › <b style="color:var(--text)">{t["name"]}</b></div>')
    # 題材標頭（假資料聚合，步驟 5 換真）
    with ui.element("div").style("display:flex;align-items:center;gap:18px;background:var(--card);border-radius:12px;padding:14px 18px;margin-bottom:16px;"):
        ui.label(t["name"]).style("font-size:18px;font-weight:700;")
        ui.html('<span style="font-size:12px;color:var(--t2)">5日動能<b class="up" style="font-size:15px;display:block;font-family:var(--mono)">+5.2%</b></span>')
        ui.html('<span style="font-size:12px;color:var(--t2)">法人<b class="gold" style="font-size:15px;display:block;font-family:var(--mono)">+25</b></span>')
    subs = store.list_sub_themes(con, theme_id)
    if subs:
        for i,s in enumerate(subs):
            _group(con, theme_id, s["id"], s["name"], on_open_stock, on_changed, get_row, open_default=(i==0))
    else:
        _group(con, theme_id, None, t["name"], on_open_stock, on_changed, get_row, open_default=True)

def _group(con, theme_id, sub_id, title, on_open_stock, on_changed, get_row, open_default):
    cons = store.list_constituents(con, theme_id, sub_id)
    with ui.element("div").style("background:var(--card);border-radius:12px;margin-bottom:10px;overflow:hidden;"):
        rows_box = ui.element("div").style(f"border-top:0.5px solid var(--line);{'' if open_default else 'display:none;'}")
        opened = {"v": open_default}
        with ui.element("div").style("display:flex;align-items:center;gap:12px;padding:12px 16px;cursor:pointer;") as head:
            chev = ui.label("▶").style(f"color:var(--t3);display:inline-block;{'transform:rotate(90deg);' if open_default else ''}")
            ui.label(title).style("font-size:14px;font-weight:600;")
            ui.label(f"{len(cons)} 檔").style("font-size:12px;color:var(--t2);font-family:var(--mono);")
        def toggle():
            opened["v"]=not opened["v"]
            rows_box.style("display:block;" if opened["v"] else "display:none;")
            chev.style("transform:rotate(90deg);" if opened["v"] else "transform:rotate(0deg);")
        head.on("click", lambda e: toggle())
        with rows_box:
            _header_row()
            for i,c in enumerate(cons):
                _stock_row(i+1, c, get_row(c["code"]), on_open_stock)
            _add_row(con, theme_id, sub_id, title, on_changed)

def _header_row():
    cols = ["", "代號 / 名稱", "現價", "今日", "5日", "RS", "法人(張)", "訊號"]
    with ui.element("div").style("display:grid;grid-template-columns:24px 1.5fr 0.8fr 0.8fr 0.7fr 0.5fr 1fr 1.1fr;padding:9px 16px;gap:6px;font-size:11px;color:var(--t3);"):
        for c in cols: ui.label(c)

def _stock_row(idx, c, r, on_open_stock):
    dc = "up" if r["today_pct"]>=0 else "down"; fc = "up" if r["d5_pct"]>=0 else "down"
    ic = "down" if r["inst"]<0 else "gold"; rsc = "gold" if r["rs"]>=80 else "muted"
    tag = theme.grade_tag(r["signal"]) or "grade_C"; bg,fg = theme.BADGE[tag]
    with ui.element("div").style("display:grid;grid-template-columns:24px 1.5fr 0.8fr 0.8fr 0.7fr 0.5fr 1fr 1.1fr;align-items:center;padding:9px 16px;gap:6px;font-size:13px;border-top:0.5px solid rgba(255,255,255,0.04);cursor:pointer;").on(
            "click", lambda e: on_open_stock(c, r)):
        ui.label(str(idx)).classes("muted")
        ui.html(f'<span><span class="mono muted">{c["code"]}</span> {c["name"]}{"" if c["in_master"] else " ⚑"}</span>')
        ui.label(f'{r["price"]}').classes("mono").style("text-align:right;")
        ui.label(f'{r["today_pct"]:+.1f}%').classes(f"mono {dc}").style("text-align:right;")
        ui.label(f'{r["d5_pct"]:+.1f}%').classes(f"mono {fc}").style("text-align:right;")
        ui.label(f'{r["rs"]}').classes(f"mono {rsc}").style("text-align:right;")
        ui.label(f'{r["inst"]:+,}').classes(f"mono {ic}").style("text-align:right;")
        ui.html(f'<span style="font-size:12px;padding:4px 10px;border-radius:7px;font-weight:600;background:{bg};color:{fg}">{r["signal"]}</span>')

def _add_row(con, theme_id, sub_id, title, on_changed):
    el = ui.element("div").style("padding:10px 16px;font-size:12px;color:var(--t2);cursor:pointer;border-top:0.5px solid rgba(255,255,255,0.04);")
    with el: ui.label(f"＋ 新增個股到「{title}」")
    el.on("click", lambda e: _add_stock_dialog(con, theme_id, sub_id, on_changed))

def _add_stock_dialog(con, theme_id, sub_id, on_changed):
    from concept import master
    with ui.dialog() as dlg, ui.card().style("background:var(--card);"):
        ui.label("新增個股").style("font-weight:600;")
        code = ui.input("代號")
        name = ui.input("名稱（查不到母清單時手動填）")
        def lookup():
            nm = master.lookup_name(code.value)
            if nm: name.value = nm
        code.on("blur", lambda e: lookup())
        with ui.row():
            ui.button("取消", on_click=dlg.close).props("flat")
            def save():
                if not code.value: return
                in_master = 1 if master.lookup_name(code.value) else 0
                nm = name.value or master.lookup_name(code.value) or code.value
                store.add_constituent(con, theme_id, code.value, nm, sub_theme_id=sub_id, in_master=in_master)
                dlg.close(); on_changed()
            ui.button("新增", on_click=save).props("color=amber")
    dlg.open()
```

- [ ] **Step 2: main.py detail 分支改呼叫 detail.render**

把 `main.py` `_render_page` 的 detail 分支改為：

```python
        elif state["page"]=="detail":
            from ui import detail, stock_modal
            detail.render(con, state["theme_id"],
                          on_open_stock=lambda c,r: stock_modal.open_modal(c, r),
                          on_changed=lambda: _go("detail", state["theme_id"]))
```

> 註：`concept/master.py`（`lookup_name`）與 `ui/stock_modal.py` 分別於 Task 16、Task 13 完成；
> 在那之前 Task 11 驗證可暫時用 `master.lookup_name` 回 None 的最小樁（見 Task 16 Step 1 會補真實作）。

- [ ] **Step 3: 補 master 最小樁讓 detail 可跑**

建立 `concept/master.py` 暫時內容（Task 16 會擴充）：

```python
# concept/master.py（最小樁；Task 16 擴充）
def lookup_name(code: str):
    return None
```

- [ ] **Step 4: 人工驗證**

啟動 App → 點熱圖任一塊 → 進明細：機器人/軟體顯示可收合子題材分組（首組展開）、列含 8 欄與 A/B/C badge、
「＋ 新增個股」可開 dialog、新增 9999 測試（標 ⚑ 非母清單）後即時出現、收合/展開正常。

- [ ] **Step 5: Commit**

```bash
git add ui/detail.py concept/master.py main.py && git commit -m "feat: detail page with sub-theme groups + inline add constituent"
```

---

# Phase 4 — 個股彈窗（假 K 線）

> 註：任務編號由 11 跳至 13（加/移成分股已併入 Task 11，無獨立 Task 12）。執行時請依文件順序逐任務進行，編號僅作標籤。

### Task 13: ui/stock_modal.py — ECharts K線 + chips + 按鈕

**Files:**
- Create: `ui/stock_modal.py`

- [ ] **Step 1: 寫 open_modal(c, r, get_ohlc=None, on_add_watch=None, on_report=None)**

```python
# ui/stock_modal.py
from nicegui import ui

def _mock_ohlc(n=40):
    import random; random.seed(7); p=400; out=[]
    for _ in range(n):
        o=p; c=max(5,o+(random.random()-0.45)*16); h=max(o,c)+random.random()*8; l=min(o,c)-random.random()*8
        out.append([round(o,1),round(c,1),round(l,1),round(h,1)]); p=c   # ECharts: [open,close,low,high]
    return out

def open_modal(c, r, get_ohlc=None, on_add_watch=None, on_report=None):
    ohlc = (get_ohlc(c["code"]) if get_ohlc else None) or _mock_ohlc()
    x = [str(i) for i in range(len(ohlc))]
    up = r["today_pct"] >= 0
    with ui.dialog() as dlg, ui.card().style("background:#151A21;width:560px;max-width:100%;padding:0;"):
        with ui.element("div").style("display:flex;align-items:baseline;gap:12px;padding:16px 18px;border-bottom:0.5px solid var(--line);"):
            ui.label(f'{c["name"]} {c["code"]}').style("font-size:16px;font-weight:700;")
            arrow = "▲" if up else "▼"
            ui.label(f'${r["price"]} {arrow} {r["today_pct"]:+.2f}%').classes("mono " + ("up" if up else "down"))
            ui.label("✕").style("margin-left:auto;color:var(--t3);cursor:pointer;").on("click", dlg.close)
        with ui.element("div").style("padding:16px 18px;"):
            ui.echart({
                "backgroundColor": "#0E1116",
                "grid": {"left":40,"right":12,"top":10,"bottom":20},
                "xAxis": {"type":"category","data":x,"axisLabel":{"show":False}},
                "yAxis": {"type":"value","scale":True,"axisLabel":{"color":"#6B7079"}},
                "series": [{
                    "type":"candlestick","data":ohlc,
                    "itemStyle":{"color":"#F0696A","color0":"#4CB782","borderColor":"#F0696A","borderColor0":"#4CB782"}
                }]
            }).style("height:180px;width:100%;")
            chips = [f'RS {r["rs"]}', "KD 黃金交叉", "均線 多頭排列", "量增 +35%", "法人連買 4 日"]
            with ui.element("div").style("display:flex;gap:8px;flex-wrap:wrap;margin:12px 0;"):
                for ch in chips:
                    ui.label(ch).style("font-size:11px;background:var(--elev);border-radius:6px;padding:4px 10px;color:var(--t2);")
            with ui.element("div").style("display:flex;gap:10px;margin-top:14px;"):
                ui.button("＋ 加入自選股", on_click=lambda: (on_add_watch and on_add_watch(c))).style(
                    "flex:1;background:var(--elev);color:var(--text);").props("flat")
                ui.button("產生完整分析報告", on_click=lambda: (on_report and on_report(c))).style(
                    "flex:1;background:var(--accent);color:#0E1116;font-weight:600;")
    dlg.open()
```

- [ ] **Step 2: 語法冒煙檢查**

Run: `python3 -c "import sys; sys.path.insert(0,'.'); import ui.stock_modal; print('ok')"`
Expected: `ok`

- [ ] **Step 3: 人工驗證**

明細頁點任一成分股 → 彈窗出現：股名/代號/現價漲跌（紅漲綠跌）、ECharts candlestick（紅綠 K）、指標 chips、兩顆按鈕。

- [ ] **Step 4: Commit**

```bash
git add ui/stock_modal.py && git commit -m "feat: stock modal with ECharts candlestick + chips + buttons"
```

---

# Phase 5 — 接後端真資料

### Task 14: data/fetcher.py — 包裝 DataSourceManager + 富邦初始化

**Files:**
- Create: `data/fetcher.py`
- Modify: `main.py`（啟動時嘗試富邦登入、傳遞富邦狀態給 app bar）

- [ ] **Step 1: 寫 data/fetcher.py**

```python
# data/fetcher.py — 包裝 vendored DataSourceManager
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "analysis"))
from data_fetcher import DataSourceManager

def get_history(code, market="台股", period="6mo"):
    return DataSourceManager.get_history(code, market, period=period)

def get_quote(code, market="台股"):
    return DataSourceManager.get_realtime_price(code, market)

def fubon_available() -> bool:
    return DataSourceManager.is_fubon_available()

def init_fubon(sdk=None) -> bool:
    try:
        return DataSourceManager.initialize(sdk)
    except Exception as e:
        print(f"[fetcher] 富邦初始化失敗: {e}")
        return False

def ohlc_for_echart(code, market="台股", period="3mo"):
    """回 ECharts candlestick 用 [[open,close,low,high],...] 與 x 軸日期。"""
    df = get_history(code, market, period)
    if df is None or len(df)==0:
        return None, None
    x = [d.strftime("%m/%d") for d in df.index]
    data = [[round(float(r.Open),2), round(float(r.Close),2), round(float(r.Low),2), round(float(r.High),2)]
            for r in df.itertuples()]
    return x, data
```

- [ ] **Step 2: main.py 啟動時嘗試富邦登入**

在 `main.py` DB 種子之後加入（富邦登入細節依 StockGOGOV2 慣例；未登入則只走 yfinance）：

```python
from data import fetcher
FUBON_OK = fetcher.init_fubon()   # 需要憑證；失敗回 False，app bar 顯示提示
```

並把 `_app_bar` 的「法人」chip 旁加一個來源提示：

```python
        src = "富邦即時" if FUBON_OK else "Yahoo(fallback)"
        ui.label(src).style("font-size:11px;color:var(--t2);background:var(--elev);border-radius:999px;padding:4px 11px;")
```

- [ ] **Step 3: 真資料冒煙（取一檔歷史）**

Run: `cd MVPTracker && python3 -c "import sys;sys.path.insert(0,'.');sys.path.insert(0,'analysis');from data import fetcher;x,d=fetcher.ohlc_for_echart('2330');print(len(d) if d else 0,'bars')"`
Expected: 印出 `>0 bars`（富邦未登入時走 yfinance 仍應有資料；若 0，檢查網路/yfinance）

- [ ] **Step 4: Commit**

```bash
git add data/fetcher.py main.py && git commit -m "feat: data fetcher wrapper + fubon init in app shell"
```

---

### Task 15: data/institutional.py — 個股 /iibs 三大法人 chip_flow

**Files:**
- Create: `data/institutional.py`、`data/cache.py`
- Test: `tests/test_institutional.py`

- [ ] **Step 1: 寫 chip_flow 聚合測試（純函式，給定 iibs 樣本）**

```python
# tests/test_institutional.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.institutional import summarize_iibs, theme_inst_ratio

SAMPLE = {"iibs":[
  {"inputDate":"2026-05-30","foreignInvestorsBuySell":1000,"investmentTrustBuySell":200,"dealerBuySell":-100,"total":1100},
  {"inputDate":"2026-05-29","foreignInvestorsBuySell":500,"investmentTrustBuySell":50,"dealerBuySell":0,"total":550},
  {"inputDate":"2026-05-28","foreignInvestorsBuySell":-30,"investmentTrustBuySell":10,"dealerBuySell":0,"total":-20},
]}

def test_summarize_latest_and_streak():
    s = summarize_iibs(SAMPLE)
    assert s["available"] is True
    assert s["total"] == 1100 and s["foreign_net"] == 1000 and s["trust_net"] == 200
    assert s["foreign_consecutive_days"] == 2   # 連 2 日外資買超
    assert s["total"] > 0

def test_summarize_empty():
    assert summarize_iibs({"iibs":[]})["available"] is False

def test_theme_ratio():
    nets = {"2330":1100,"2317":-50,"2454":300}
    assert theme_inst_ratio(nets) == 2/3   # 3 檔中 2 檔淨買
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 -m pytest tests/test_institutional.py -v`
Expected: FAIL

- [ ] **Step 3: 寫 data/cache.py 與 data/institutional.py**

```python
# data/cache.py — scan_cache 讀寫
import json, datetime
def get(con, code, kind):
    r = con.execute("SELECT payload_json, updated_at FROM scan_cache WHERE code=? AND kind=?", (code, kind)).fetchone()
    return (json.loads(r["payload_json"]), r["updated_at"]) if r else (None, None)
def put(con, code, kind, payload):
    con.execute("INSERT OR REPLACE INTO scan_cache(code,kind,payload_json,updated_at) VALUES(?,?,?,?)",
                (code, kind, json.dumps(payload, ensure_ascii=False), datetime.datetime.now().isoformat()))
    con.commit()
def is_today(updated_at) -> bool:
    if not updated_at: return False
    return updated_at[:10] == datetime.date.today().isoformat()
```

```python
# data/institutional.py — 個股 /iibs 三大法人 → chip_flow
import requests
from data import cache

IIBS_URL = "https://api.wukong.com.tw/stock/{code}/iibs"
HEADERS = {"User-Agent":"Mozilla/5.0","Accept":"application/json","Referer":"https://wukong.com.tw/"}

def _streak(items, key):
    """連續同向天數：買超正、賣超負。items 已依日期新→舊排序。"""
    if not items: return 0
    first = items[0].get(key, 0) or 0
    if first == 0: return 0
    sign = 1 if first > 0 else -1
    n = 0
    for it in items:
        v = it.get(key, 0) or 0
        if (v > 0 and sign > 0) or (v < 0 and sign < 0): n += 1
        else: break
    return n * sign

def summarize_iibs(data: dict) -> dict:
    items = sorted(data.get("iibs", []), key=lambda x: x.get("inputDate",""), reverse=True)
    if not items:
        return {"available": False}
    latest = items[0]
    return {
        "available": True,
        "foreign_net": latest.get("foreignInvestorsBuySell",0) or 0,
        "trust_net": latest.get("investmentTrustBuySell",0) or 0,
        "dealer_net": latest.get("dealerBuySell",0) or 0,
        "total": latest.get("total",0) or 0,
        "foreign_consecutive_days": _streak(items, "foreignInvestorsBuySell"),
        "trust_consecutive_days": _streak(items, "investmentTrustBuySell"),
        "date": latest.get("inputDate",""),
    }

def chip_flow(code, con=None):
    """逐檔取得個股三大法人 chip_flow（每日快取）。"""
    if con is not None:
        payload, ts = cache.get(con, code, "iibs")
        if payload and cache.is_today(ts):
            return payload
    try:
        resp = requests.get(IIBS_URL.format(code=code), headers=HEADERS, timeout=10)
        data = resp.json() if resp.status_code == 200 else {"iibs": []}
    except Exception as e:
        print(f"[institutional] {code} 取得失敗: {e}")
        data = {"iibs": []}
    summary = summarize_iibs(data)
    if con is not None and summary.get("available"):
        cache.put(con, code, "iibs", summary)
    return summary

def theme_inst_ratio(code_to_net: dict) -> float:
    if not code_to_net: return 0.0
    return sum(1 for v in code_to_net.values() if v > 0) / len(code_to_net)

def intraday_force(code):
    """v2 預留：盤中主力力道（富邦 tick）。MVP 回 None。"""
    return None
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python3 -m pytest tests/test_institutional.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add data/institutional.py data/cache.py tests/test_institutional.py
git commit -m "feat: per-stock /iibs institutional chip_flow + scan_cache"
```

---

### Task 16: concept/master.py — 代號→名稱補名

**Files:**
- Modify: `concept/master.py`（取代 Task 11 的樁）
- Test: `tests/test_master.py`

- [ ] **Step 1: 寫補名測試（twstock 優先，info_cache 次之）**

```python
# tests/test_master.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from concept import master

def test_lookup_known():
    # 2330 台積電 應可由 twstock 取得名稱
    name = master.lookup_name("2330")
    assert name and ("積" in name or name != "2330")

def test_lookup_unknown_returns_none():
    assert master.lookup_name("00000") is None
```

- [ ] **Step 2: 跑測試確認失敗（樁回 None → test_lookup_known FAIL）**

Run: `python3 -m pytest tests/test_master.py -v`
Expected: `test_lookup_known` FAIL

- [ ] **Step 3: 寫 concept/master.py（twstock + WukongAPI fallback）**

```python
# concept/master.py — 代號→名稱/產業 驗證與補名
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "analysis"))

_cache = {}

def lookup_name(code: str):
    if not code: return None
    code = code.strip()
    if code in _cache: return _cache[code]
    name = None
    # 1) twstock 母清單
    try:
        import twstock
        info = twstock.codes.get(code)
        if info and getattr(info, "name", None):
            name = info.name
    except Exception:
        pass
    # 2) WukongAPI 個股資訊 fallback
    if not name:
        try:
            from data_fetcher import WukongAPI
            r = WukongAPI.get_stock_info(code)
            if r and r.get("name"):
                name = r["name"]
        except Exception:
            pass
    _cache[code] = name
    return name

def in_master(code: str) -> bool:
    return lookup_name(code) is not None
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python3 -m pytest tests/test_master.py -v`
Expected: PASS（若 twstock 無此版本資料，至少 WukongAPI 路徑可補；網路不可用時 `test_lookup_known` 可能 skip——確認 2330 可取得名稱）

- [ ] **Step 5: Commit**

```bash
git add concept/master.py tests/test_master.py && git commit -m "feat: master code->name lookup (twstock + WukongAPI)"
```

---

### Task 17: scanner 真聚合 — QuickAnalyzer 逐檔 + 題材聚合

**Files:**
- Modify: `scanner/theme_scanner.py`（加 `analyze_stock_row`、`scan_theme`、`real_overview`）
- Test: `tests/test_scanner.py`（加聚合測試）

- [ ] **Step 1: 加題材聚合純函式測試**

```python
# tests/test_scanner.py （append）
from scanner.theme_scanner import aggregate_theme

def test_aggregate_theme():
    rows = [
        {"d5_pct": 6.0, "today_pct": 1.0, "inst": 100, "signal": "可進場 A"},
        {"d5_pct": -2.0, "today_pct": -1.0, "inst": -50, "signal": "觀察"},
        {"d5_pct": 4.0, "today_pct": 0.5, "inst": 200, "signal": "建議買進 追蹤"},
    ]
    agg = aggregate_theme(rows)
    assert round(agg["momentum_5d"],2) == round((6-2+4)/3,2)
    assert agg["up_count"] == 2 and agg["down_count"] == 1
    assert round(agg["inst_ratio"],2) == round(2/3,2)   # 2 檔淨買
    assert 0 <= agg["strong_ratio"] <= 1
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 -m pytest tests/test_scanner.py::test_aggregate_theme -v`
Expected: FAIL

- [ ] **Step 3: 擴充 scanner/theme_scanner.py**

```python
# scanner/theme_scanner.py （append）
import sys, os, statistics
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "analysis"))
from concept import store
from data import institutional
from ui import theme as uitheme

def analyze_stock_row(code, con=None):
    """單檔 → 明細列 dict。用 QuickAnalyzer.analyze_stock（grade/RS/recommendation）+ /iibs 法人。"""
    from quick_analyzer import QuickAnalyzer
    res = QuickAnalyzer.analyze_stock(code, "台股")
    chip = institutional.chip_flow(code, con)
    if not res:
        return {"price":0,"today_pct":0,"d5_pct":0,"rs":50,"inst":chip.get("total",0),"signal":"觀察"}
    rs = res.get("relative_strength",{}).get("rs_score",50)
    rec = res.get("recommendation","") or res.get("decision_matrix",{}).get("signal","")
    d5 = res.get("relative_strength",{}).get("rs_5d",0)
    return {
        "price": res.get("current_price",0),
        "today_pct": res.get("price_change_pct",0),
        "d5_pct": d5,
        "rs": round(rs),
        "inst": chip.get("total",0),
        "signal": rec if rec else "觀察",
    }

def aggregate_theme(rows):
    if not rows:
        return {"momentum_5d":0,"up_count":0,"down_count":0,"inst_ratio":0,"strong_ratio":0,"inst_net":0}
    moms = [r["d5_pct"] for r in rows]
    nets = {str(i): r["inst"] for i,r in enumerate(rows)}
    strong = sum(1 for r in rows if uitheme.grade_tag(r["signal"]) in ("grade_A","grade_B"))
    return {
        "momentum_5d": statistics.mean(moms),
        "up_count": sum(1 for r in rows if r["today_pct"]>0),
        "down_count": sum(1 for r in rows if r["today_pct"]<0),
        "inst_ratio": institutional.theme_inst_ratio(nets),
        "strong_ratio": strong/len(rows),
        "inst_net": round((institutional.theme_inst_ratio(nets)*2-1)*100),  # -100..100 給熱圖
    }

def scan_theme(con, theme_id):
    """題材所有成分股逐檔分析 → rows + 聚合。"""
    cons = store.list_constituents(con, theme_id, None)
    for s in store.list_sub_themes(con, theme_id):
        cons += store.list_constituents(con, theme_id, s["id"])
    rows = {c["code"]: analyze_stock_row(c["code"], con) for c in cons}
    agg = aggregate_theme(list(rows.values()))
    return rows, agg

def real_overview(con):
    out=[]
    for t in store.list_themes(con):
        cons = store.list_constituents(con, t["id"], None)
        for s in store.list_sub_themes(con, t["id"]):
            cons += store.list_constituents(con, t["id"], s["id"])
        rows = [analyze_stock_row(c["code"], con) for c in cons]
        agg = aggregate_theme(rows)
        out.append(ThemeMetrics(theme_id=t["id"], key=t["key"], name=t["name"],
                                momentum_5d=round(agg["momentum_5d"],1), inst_net=agg["inst_net"],
                                count=len(cons), up_count=agg["up_count"], down_count=agg["down_count"],
                                strong_ratio=agg["strong_ratio"], diverge=is_diverge(agg["momentum_5d"], agg["inst_net"])))
    return out
```

- [ ] **Step 4: 跑聚合測試確認通過**

Run: `python3 -m pytest tests/test_scanner.py -v`
Expected: PASS（含 mock + aggregate）

- [ ] **Step 5: 單檔真分析冒煙**

Run: `cd MVPTracker && python3 -c "import sys;sys.path.insert(0,'.');sys.path.insert(0,'analysis');from scanner.theme_scanner import analyze_stock_row;print(analyze_stock_row('2330'))"`
Expected: 印出含 price/rs/signal 的 dict（網路/資料源可用時）；若 None 走 fallback 仍回 dict

- [ ] **Step 6: Commit**

```bash
git add scanner/theme_scanner.py tests/test_scanner.py
git commit -m "feat: real theme scan via QuickAnalyzer + theme aggregation"
```

---

### Task 18: 接線 — overview/detail/modal 換真資料 + 報告按鈕

**Files:**
- Modify: `main.py`、`ui/detail.py`、`ui/stock_modal.py`

- [ ] **Step 1: main.py get_metrics 換 real_overview（含每日快取）**

```python
# main.py：把 get_metrics 換成
def get_metrics():
    return theme_scanner.real_overview(con)
```

> 註：`real_overview` 會逐檔跑 QuickAnalyzer，首次較慢。可選優化：在 `real_overview` 內對每檔
> row 也存 `scan_cache(kind='row')` 每日快取（與 institutional 同模式），盤中只刷價格。MVP 可先直接跑。

- [ ] **Step 2: detail.render 改用真 row provider**

把 main.py detail 分支改為傳入真 row：

```python
        elif state["page"]=="detail":
            from ui import detail, stock_modal
            rows, agg = theme_scanner.scan_theme(con, state["theme_id"])
            detail.render(con, state["theme_id"],
                          on_open_stock=lambda c,r: stock_modal.open_modal(
                              c, r, get_ohlc=lambda code: fetcher.ohlc_for_echart(code)[1],
                              on_add_watch=lambda cc: _add_watch(cc),
                              on_report=lambda cc: _show_report(cc)),
                          on_changed=lambda: _go("detail", state["theme_id"]),
                          get_row=lambda code: rows.get(code) or detail._mock_row(code))
```

- [ ] **Step 3: 報告按鈕 → QuickAnalyzer recommendation 對話框**

在 main.py 加：

```python
def _show_report(c):
    sys.path.insert(0, os.path.join(ROOT, "analysis"))
    from quick_analyzer import QuickAnalyzer
    res = QuickAnalyzer.analyze_stock(c["code"], "台股") or {}
    text = res.get("recommendation","") or "（無法產生報告）"
    with ui.dialog() as dlg, ui.card().style("background:var(--card);max-width:680px;"):
        ui.label(f'{c["name"]} {c["code"]} 完整分析報告').style("font-weight:700;")
        ui.markdown(text if isinstance(text,str) else str(text)).style("white-space:pre-wrap;font-size:13px;")
        ui.button("關閉", on_click=dlg.close).props("flat")
    dlg.open()

def _add_watch(c):
    from concept import watchstore   # Task 19
    watchstore.add(con, c["code"], c.get("name",""))
    ui.notify(f'已加入自選：{c.get("name","")} {c["code"]}')
```

> 註：`_add_watch` 依賴 Task 19 的 `watchstore`；若先做 Task 18，可暫時讓 `_add_watch` 只 `ui.notify`。

- [ ] **Step 4: 人工驗證（真資料）**

啟動 App：總覽熱圖以真動能/法人著色；點題材→明細列顯示真現價/今日/RS/法人/grade；
點個股→彈窗 K 線為富邦/yfinance 真 OHLC；「產生完整分析報告」跳出 QuickAnalyzer 報告文字。

- [ ] **Step 5: Commit**

```bash
git add main.py ui/detail.py ui/stock_modal.py
git commit -m "feat: wire overview/detail/modal to real scanner + report button"
```

---

# Phase 6 — 極簡自選股

### Task 19: watchlist — store + 頁面 + 由彈窗加入

**Files:**
- Create: `concept/watchstore.py`、`ui/watchlist.py`
- Modify: `main.py`（watch 分支、`_add_watch`）
- Test: `tests/test_watchlist.py`

- [ ] **Step 1: 寫 watchstore 測試**

```python
# tests/test_watchlist.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from concept.db import connect, init_schema
from concept import watchstore

def test_add_list_remove(tmp_path):
    con = connect(str(tmp_path/"t.db")); init_schema(con)
    watchstore.add(con, "2330", "台積電")
    watchstore.add(con, "2330", "台積電")   # 不重複
    items = watchstore.list_all(con)
    assert len(items) == 1 and items[0]["code"] == "2330"
    watchstore.remove(con, items[0]["id"])
    assert watchstore.list_all(con) == []
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python3 -m pytest tests/test_watchlist.py -v`
Expected: FAIL

- [ ] **Step 3: 寫 concept/watchstore.py**

```python
# concept/watchstore.py
import datetime
def add(con, code, name):
    exists = con.execute("SELECT id FROM watchlist WHERE code=?", (code,)).fetchone()
    if exists: return exists["id"]
    con.execute("INSERT INTO watchlist(code,name,added_at) VALUES(?,?,?)",
                (code, name, datetime.datetime.now().isoformat())); con.commit()
    return con.execute("SELECT id FROM watchlist WHERE code=?", (code,)).fetchone()["id"]
def list_all(con):
    return [dict(r) for r in con.execute("SELECT * FROM watchlist ORDER BY added_at DESC")]
def remove(con, wid):
    con.execute("DELETE FROM watchlist WHERE id=?", (wid,)); con.commit()
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python3 -m pytest tests/test_watchlist.py -v`
Expected: PASS

- [ ] **Step 5: 寫 ui/watchlist.py 並接 main.py watch 分支**

```python
# ui/watchlist.py
from nicegui import ui
from concept import watchstore

def render(con, on_open_stock=None):
    items = watchstore.list_all(con)
    if not items:
        ui.label("自選股清單為空。於個股彈窗按「加入自選股」加入。").style("font-size:13px;color:var(--t3);")
        return
    for it in items:
        with ui.element("div").style("display:flex;align-items:center;gap:12px;background:var(--card);border-radius:10px;padding:10px 14px;margin-bottom:8px;"):
            ui.html(f'<span class="mono muted">{it["code"]}</span> {it["name"]}')
            ui.element("div").style("flex:1;")
            ui.button("移除", on_click=lambda e, w=it["id"]: (watchstore.remove(con, w), ui.navigate.to("/"))).props("flat dense")
```

main.py：watch 分支改 `from ui import watchlist; watchlist.render(con)`；`_add_watch` 改用 `watchstore.add`（取代 Task 18 樁）。

- [ ] **Step 6: 人工驗證**

個股彈窗按「加入自選股」→ 切到自選頁 → 出現該股；按移除 → 消失。

- [ ] **Step 7: Commit**

```bash
git add concept/watchstore.py ui/watchlist.py main.py tests/test_watchlist.py
git commit -m "feat: minimal watchlist store + page + add-from-modal"
```

---

# Phase 7 — 驗收

### Task 20: 端到端驗收 + native 視窗

**Files:**
- Create: `docs/ACCEPTANCE.md`（勾選結果）

- [ ] **Step 1: 全測試綠燈**

Run: `cd MVPTracker && python3 -m pytest -v`
Expected: 全 PASS（test_imports/theme/db/seed/store/scanner/institutional/master/watchlist）

- [ ] **Step 2: 逐項人工驗收（對照 mockup 與規格步驟 7）**

依序確認並記錄於 `docs/ACCEPTANCE.md`：
1. 四頁能跑（總覽/明細/個股彈窗/自選；象限與報告 nav 為 disabled）。
2. CRUD：新增題材→熱圖即時出現；明細加/移成分股→即時反映；重啟 App 後資料仍在（寫入 SQLite）。
3. 非母清單代號可手動加入並標 ⚑。
4. 富邦資料進得來（app bar 顯示「富邦即時」；未登入顯示「Yahoo(fallback)」仍有 K 線）。
5. 配色：紅漲綠跌、A=琥珀/B=藍/C=灰 badge 正確；法人金條/⚠ 背離顯示正確。
6. `ui.run(native=True)` 能開成桌面視窗。

- [ ] **Step 3: native 視窗確認**

Run: `cd MVPTracker && python3 main.py`
Expected: 開出 1240×860 桌面視窗，標題 MVPTracker，總覽頁正常。

- [ ] **Step 4: Commit**

```bash
git add docs/ACCEPTANCE.md && git commit -m "docs: MVP acceptance checklist results"
```

---

## 打包（v2，列項不實作）

- `nicegui-pack --onefile --name MVPTracker main.py` 或 PyInstaller；需處理 fubon_neo 動態庫與憑證路徑。

## 與規格的對應（自我檢查）

- 總覽熱圖+法人+背離 → Task 7–10、17–18 ✓
- 題材明細子題材分組+就地 CRUD → Task 11、16、18 ✓
- 個股彈窗 K線+報告 → Task 13、18 ✓
- 極簡自選股 → Task 19 ✓
- 後端複製（含 QuickAnalyzer 抽出）→ Task 2 ✓
- EOD 法人逐檔 /iibs → Task 15 ✓
- 種子由 Sheet 生成 + 啟動匯入 → Task 5、10 ✓
- 紅漲綠跌 / A·B·C 配色 → Task 3 ✓
- ui.run(native) 桌面視窗 → Task 10、20 ✓
- v2 預留 intraday_force → Task 15 ✓
