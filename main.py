# main.py
import os, sys, json
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)                              # config 等頂層模組
sys.path.insert(0, os.path.join(ROOT, "analysis"))   # 讓 vendored 後端扁平 import 解析

from nicegui import ui
from concept import db as condb, store
from ui import theme, overview
from scanner import theme_scanner
from data import fetcher

con = condb.connect(); condb.init_schema(con)
if condb.is_empty(con):
    with open(os.path.join(ROOT, "storage", "concept_map.json"), encoding="utf-8") as f:
        store.import_concept_map(con, json.load(f))

# 富邦行情初始化（需登入憑證；失敗則只走 yfinance fallback）
FUBON_OK = fetcher.init_fubon()

# 測試/除錯 hook：MVP_DETAIL=<theme_id> 停在明細頁；MVP_PAGE=<page> 停在指定頁
_dt = os.environ.get("MVP_DETAIL")
_pg = os.environ.get("MVP_PAGE") or ("detail" if _dt else "overview")
state = {"page": _pg, "theme_id": int(_dt) if _dt else None}


def get_metrics():
    # 真資料：逐檔 QuickAnalyzer + /iibs 法人，聚合題材層（每日 row 快取）。
    # 註：冷啟動會逐檔掃描全部成分股；富邦登入時最快，未登入走 yfinance 較慢。
    # 設 MVP_MOCK=1 可改用假資料（離線/快速 demo）。
    if os.environ.get("MVP_MOCK") == "1":
        return theme_scanner.mock_overview()
    return theme_scanner.real_overview(con)


def _show_report(c):
    from quick_analyzer import QuickAnalyzer
    res = QuickAnalyzer.analyze_stock(c["code"], "台股") or {}
    rec = res.get("recommendation") or {}
    lines = []
    if isinstance(rec, dict):
        lines.append(f"**綜合建議**：{rec.get('overall', '—')}（評分 {rec.get('score', '—')}）")
        lines.append(f"**情境**：{rec.get('scenario_name', '—')}")
        if rec.get("action_timing"):
            lines.append(f"**時機**：{rec['action_timing']}")
        if rec.get("warning_message"):
            lines.append(f"**提醒**：{rec['warning_message']}")
        for k, label in (("short_term", "短線"), ("mid_term", "中線"), ("long_term", "長線")):
            seg = rec.get(k) or {}
            if seg:
                lines.append(f"**{label}**：{seg.get('action', '')}　{seg.get('reason', '')}")
    text = "\n\n".join(lines) or "（無法產生報告）"
    with ui.dialog() as dlg, ui.card().style("background:var(--card);max-width:680px;"):
        ui.label(f'{c["name"]} {c["code"]} 完整分析報告').style("font-weight:700;font-size:16px;")
        ui.markdown(text)
        ui.button("關閉", on_click=dlg.close).props("flat")
    dlg.open()


def _add_watch(c):
    from concept import watchstore
    watchstore.add(con, c["code"], c.get("name", ""))
    ui.notify(f'已加入自選：{c.get("name", "")} {c["code"]}')


def _app_bar():
    with ui.element("div").style("display:flex;align-items:center;gap:10px;padding:13px 18px;background:var(--bar);flex-wrap:wrap;"):
        ui.html('<span style="font-size:16px;font-weight:700;"><span style="color:var(--accent)">MVP</span>Tracker</span>')
        n = len(store.list_themes(con))
        for txt in (f"concept_map {n}類", "回看 5日"):
            ui.label(txt).style("font-size:11px;color:var(--t2);background:var(--elev);border-radius:999px;padding:4px 11px;")
        ui.label("法人:接後端後顯示").classes("gold").style("font-size:11px;background:var(--elev);border-radius:999px;padding:4px 11px;")
        src = "富邦即時" if FUBON_OK else "Yahoo(fallback)"
        ui.label(src).style("font-size:11px;color:var(--t2);background:var(--elev);border-radius:999px;padding:4px 11px;")


_RAIL_ITEMS = [("overview", "總覽", False), ("quadrant", "象限", True), ("detail", "明細", False), ("watch", "自選", False)]


@ui.page("/")
def index():
    from nicegui import run
    theme.apply_global()
    price_cells = {}   # {code: (price_label, today_label)}，供自動刷新就地更新

    # ----- 固定外框：app bar（固定）＋ body(rail + content) -----
    with ui.element("div").style("max-width:1180px;margin:0 auto;background:var(--bg);border-radius:14px;overflow:hidden;"):
        _app_bar()                               # 固定，不隨換頁重建
        with ui.element("div").style("display:flex;"):
            rail_box = ui.element("div").style("width:80px;background:var(--rail);padding:14px 0;display:flex;flex-direction:column;gap:6px;")
            content_box = ui.element("div").style("flex:1;padding:18px;min-width:0;")

    def navigate(page, theme_id=None):
        state["page"] = page
        state["theme_id"] = theme_id
        _render_rail()
        _render_content()

    def _render_rail():
        rail_box.clear()
        with rail_box:
            for key, label, disabled in _RAIL_ITEMS:
                active = state["page"] == key
                color = "var(--accent)" if active else ("#4A4F57" if disabled else "#7E848C")
                el = ui.element("div").style(
                    f"display:flex;flex-direction:column;align-items:center;gap:4px;padding:11px 0;color:{color};"
                    f"border-left:3px solid {'var(--accent)' if active else 'transparent'};font-size:10.5px;"
                    f"{'background:rgba(239,159,39,0.10);' if active else ''}{'opacity:0.5;' if disabled else 'cursor:pointer;'}")
                with el:
                    ui.label(label)
                if not disabled:
                    el.on("click", lambda e, k=key: navigate(k))

    def _render_content():
        price_cells.clear()
        content_box.clear()
        with content_box:
            if state["page"] == "overview":
                def do_refresh():
                    theme_scanner.real_overview(con, force=True)
                    navigate("overview")
                overview.render(con, on_open_theme=lambda m: navigate("detail", m.theme_id),
                                get_metrics=get_metrics, on_theme_changed=lambda: navigate("overview"),
                                on_refresh=(None if os.environ.get("MVP_MOCK") == "1" else do_refresh))
            elif state["page"] == "detail":
                from ui import detail, stock_modal
                tid = state["theme_id"]
                if not tid:
                    ui.label("請從『總覽』點選一個題材以查看明細。").style("color:var(--t3);")
                    return
                rows, agg = theme_scanner.scan_theme(con, tid)
                theme_scanner.refresh_prices(rows)
                header = {"momentum_5d": agg["momentum_5d"], "count": len(rows),
                          "inst_buy_count": round(agg["inst_ratio"] * len(rows))}

                def open_stock(c, r):
                    stock_modal.open_modal(c, r, get_ohlc=lambda code: fetcher.ohlc_for_echart(code)[1],
                                           on_add_watch=_add_watch, on_report=_show_report)

                def do_refresh():
                    theme_scanner.scan_theme(con, tid, force=True)
                    navigate("detail", tid)
                detail.render(con, tid, on_open_stock=open_stock,
                              on_changed=lambda: navigate("detail", tid),
                              get_row=lambda code: rows.get(code) or detail._mock_row(code),
                              header=header, on_refresh=do_refresh, price_cells=price_cells)
                if os.environ.get("MVP_MODAL"):
                    ui.timer(0.4, lambda: stock_modal.open_modal(
                        {"code": "2049", "name": "上銀", "in_master": 1},
                        {"price": 612, "today_pct": 3.0, "rs": 91}), once=True)
            elif state["page"] == "watch":
                from ui import watchlist
                watchlist.render(con)
            elif state["page"] == "quadrant":
                ui.label("象限圖為 v2 功能（先預留）。").style("color:var(--t3);")

    _render_rail()
    _render_content()

    # ----- 自動刷新當前股價（僅明細頁；就地更新 price/today，不重繪、不收合分組）-----
    async def _auto_price_tick():
        if state["page"] != "detail" or not price_cells:
            return
        for code, (pl, tl) in list(price_cells.items()):
            try:
                q = await run.io_bound(fetcher.get_quote, code)
                if q and q.get("price"):
                    pl.set_text(str(q["price"]))
                    cp = q.get("change_pct")
                    if cp:   # 僅非零才更新，避免盤後 0 蓋掉收盤漲幅
                        tl.set_text(f"{cp:+.1f}%")
                        tl.classes(replace="mono " + ("up" if cp >= 0 else "down"))
            except Exception:
                pass
    if os.environ.get("MVP_NOREFRESH") != "1":
        ui.timer(float(os.environ.get("MVP_REFRESH_SEC", "30")), _auto_price_tick)


def _loop_already_running() -> bool:
    import asyncio
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


if _loop_already_running():
    # 在 Jupyter / IPython / Spyder 等已有事件迴圈的互動環境中無法啟動 ui.run。
    print("\n" + "=" * 70)
    print("⚠ 偵測到已在執行的事件迴圈（Jupyter / IPython / Spyder 互動環境）。")
    print("  NiceGUI 桌面模式需要自己掌控行程，無法在互動環境內啟動。")
    print("  請改在『一般終端機』執行：")
    print("      cd " + ROOT)
    print("      python3 main.py")
    print("  （或瀏覽器模式測試：MVP_WEB=1 python3 main.py，再開 http://localhost:8111）")
    print("=" * 70 + "\n")
elif os.environ.get("MVP_WEB") == "1":
    # 測試/驗證用：瀏覽器模式（headless 可截圖）
    ui.run(native=False, port=int(os.environ.get("MVP_PORT", "8111")), reload=False, show=False)
else:
    ui.run(native=True, title="MVPTracker", window_size=(1240, 860), reload=False)
