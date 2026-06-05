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

# 富邦自動登入並啟用行情（憑證見 storage/fubon_credentials.json 或 FUBON_* 環境變數）；
# 失敗則只走 yfinance fallback。
from data import fubon_login
FUBON_OK, FUBON_MSG = fubon_login.login_and_init()
print(f"[Fubon] {FUBON_MSG}")

# 法人資料實際日期（盤後落後資料，非當日）；取一檔參考股一次
from data import institutional
_inst_date = institutional.latest_date()   # 'YYYY-MM-DD' 或 None
INST_LABEL = f"法人 截至 {_inst_date[5:].replace('-', '/')}" if _inst_date else "法人(盤後)"

# 測試/除錯 hook：MVP_DETAIL=<theme_id> 停在明細頁；MVP_PAGE=<page> 停在指定頁
_DT = os.environ.get("MVP_DETAIL")
_PG = os.environ.get("MVP_PAGE") or ("detail" if _DT else "overview")


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


_RAIL_ITEMS = [("overview", "總覽", False), ("ranking", "排行", False),
               ("quadrant", "象限", True), ("detail", "明細", False), ("watch", "自選", False)]


@ui.page("/")
def index():
    from nicegui import run
    state = {"page": _PG, "theme_id": int(_DT) if _DT else None}   # 每個 client 各自的頁面狀態
    theme.apply_global()
    # app shell 容器底色（Quasar layout/page）
    ui.add_css("""
      .q-layout, .q-page-container, .q-page { background: var(--bg) !important; }
      .nicegui-content { padding: 0; gap: 0; }
    """)
    price_cells = {}   # {code: (price_label, today_label)}，供自動刷新就地更新

    # ----- 固定 HEADER -----
    with ui.header(fixed=True).style("background:var(--bar);border-bottom:0.5px solid var(--line);padding:10px 18px;"):
        with ui.row().style("align-items:center;gap:10px;width:100%;flex-wrap:wrap;"):
            ui.html('<span style="font-size:16px;font-weight:700;"><span style="color:var(--accent)">MVP</span>Tracker</span>')
            n = len(store.list_themes(con))
            for txt in (f"concept_map {n}類", "回看 5日"):
                ui.label(txt).style("font-size:11px;color:var(--t2);background:var(--elev);border-radius:999px;padding:4px 11px;")
            ui.label(INST_LABEL).classes("gold").style("font-size:11px;background:var(--elev);border-radius:999px;padding:4px 11px;")
            src = "富邦即時" if FUBON_OK else "Yahoo(fallback)"
            ui.label(src).style("font-size:11px;color:var(--t2);background:var(--elev);border-radius:999px;padding:4px 11px;")

    # ----- 固定 LEFT DRAWER -----
    drawer = ui.left_drawer(fixed=True, bordered=True).props("width=104 breakpoint=0").style("background:var(--rail);padding:0;")
    with drawer:
        nav_box = ui.column().style("gap:6px;padding:14px 0;width:100%;align-items:stretch;")

    # ----- 單一滿版內容區（只有它會換頁/捲動）-----
    content_box = ui.column().classes("w-full").style("padding:18px;gap:0;min-width:0;align-items:stretch;")
    # 穩定的 timer 容器：不隨換頁清空，避免動態建立的 ui.timer 綁到已刪除的 slot
    timer_host = ui.element("div").style("display:none;")

    def navigate(page, theme_id=None):
        state["page"] = page
        state["theme_id"] = theme_id
        _render_nav()
        _render_content()

    def _render_nav():
        nav_box.clear()
        with nav_box:
            for key, label, disabled in _RAIL_ITEMS:
                active = state["page"] == key
                color = "var(--accent)" if active else ("#4A4F57" if disabled else "#7E848C")
                el = ui.element("div").style(
                    f"display:flex;flex-direction:column;align-items:center;gap:4px;padding:11px 0;color:{color};"
                    f"border-left:3px solid {'var(--accent)' if active else 'transparent'};font-size:11px;"
                    f"{'background:rgba(239,159,39,0.10);' if active else ''}{'opacity:0.5;' if disabled else 'cursor:pointer;'}")
                with el:
                    ui.label(label)
                if not disabled:
                    el.on("click", lambda e, k=key: navigate(k))

    def _open_stock(c, r):
        from ui import stock_modal
        from data import institutional
        stock_modal.open_modal(c, r, get_ohlc=lambda code: fetcher.ohlc_for_echart(code)[1],
                               on_add_watch=_add_watch, on_report=_show_report,
                               get_chip=lambda code: institutional.chip_flow(code, con))

    async def _refresh_overview():
        await _progressive_overview(force=True)   # 重新掃描：同樣走漸進顯示

    def _draw_overview(metrics, status=None, allow_refresh=False):
        if state["page"] != "overview":
            return
        content_box.clear()
        with content_box:
            if status:
                ui.label(status).style("font-size:12px;color:var(--t3);margin-bottom:8px;")
            # on_refresh 透過 ui.timer 啟動（在按鈕仍存在的 context 建立 timer；
            # 其 callback 為 coroutine function 會被 NiceGUI await → 真正執行漸進掃描）
            overview.render(con, on_open_theme=lambda m: navigate("detail", m.theme_id),
                            get_metrics=lambda: metrics, on_theme_changed=lambda: navigate("overview"),
                            on_refresh=(_refresh_overview
                                        if (allow_refresh and os.environ.get("MVP_MOCK") != "1") else None),
                            on_open_stock=(None if os.environ.get("MVP_MOCK") == "1" else _open_stock))

    async def _progressive_overview(force):
        """背景平行掃描 + 漸進顯示：先畫骨架，每完成一個題材即時填入該塊。"""
        import datetime as _dt
        sess = theme_scanner.start_overview_scan(con, force=force)
        themes = sess["themes"]
        skel = {m.theme_id: m for m in theme_scanner.skeleton_metrics(con)}

        def current():
            return [sess["metrics"].get(t["id"]) or skel[t["id"]] for t in themes]

        def tick():
            if state["page"] != "overview":
                return
            with sess["lock"]:
                done, total, fin = sess["done"], sess["total"], sess["finished"]
            # 掃描中不顯示「重新掃描」（避免重複觸發）；完成才開放
            _draw_overview(current(), status=(None if fin else f"分析中… {done}/{total}（背景進行，可先點選已完成題材）"),
                           allow_refresh=fin)
        _draw_overview(current(), status=f"分析中… 0/{sess['total']}", allow_refresh=False)
        with timer_host:                       # 綁到穩定容器，避免 slot 被清空
            poll = ui.timer(0.6, tick)
        await run.io_bound(sess["run"])
        poll.cancel()
        _draw_overview(current(), status=f"更新於 {_dt.datetime.now().strftime('%H:%M')}", allow_refresh=True)

    async def _render_overview():
        content_box.clear()
        if os.environ.get("MVP_MOCK") == "1":
            _draw_overview(theme_scanner.mock_overview())
            return
        from data import cache as _cache
        codes = theme_scanner.all_constituent_codes(con)
        need = [c for c in codes if not (lambda rt: rt[0] and _cache.is_today(rt[1]))(_cache.get(con, c, "row"))]
        if not need:                       # 當日全快取 → 瞬開
            import datetime as _dt
            _draw_overview(theme_scanner.real_overview(con),
                           status=f"更新於 {_dt.datetime.now().strftime('%H:%M')}", allow_refresh=True)
        else:                              # 冷啟動 → 背景漸進掃描
            await _progressive_overview(force=False)

    def _render_content():
        price_cells.clear()
        if state["page"] == "overview":
            with timer_host:                              # 穩定 slot，避免綁到剛清空的導覽列
                ui.timer(0.01, _render_overview, once=True)   # 非同步（含進度）
            return
        content_box.clear()
        with content_box:
            if state["page"] == "ranking":
                from ui import ranking
                ranking.render(con, on_open_theme=lambda tid: navigate("detail", tid), on_open_stock=_open_stock)
            elif state["page"] == "detail":
                from ui import detail
                tid = state["theme_id"]
                if not tid:
                    ui.label("請從『總覽』點選一個題材以查看明細。").style("color:var(--t3);")
                    return
                rows, agg = theme_scanner.scan_theme(con, tid)
                theme_scanner.refresh_prices(rows)
                header = {"momentum_5d": agg["momentum_5d"], "count": len(rows),
                          "inst_buy_count": agg["inst_buy_count"], "inst_avail_count": agg["inst_avail_count"]}

                def do_refresh():
                    theme_scanner.scan_theme(con, tid, force=True)
                    navigate("detail", tid)
                detail.render(con, tid, on_open_stock=_open_stock,
                              on_changed=lambda: navigate("detail", tid),
                              get_row=lambda code: rows.get(code) or detail._mock_row(code),
                              header=header, on_refresh=do_refresh, price_cells=price_cells)
                if os.environ.get("MVP_MODAL"):   # 測試 hook：走真實 _open_stock（含 get_chip）
                    ui.timer(0.4, lambda: _open_stock(
                        {"code": "2049", "name": "上銀", "in_master": 1},
                        rows.get("2049") or {"price": 612, "today_pct": 3.0, "rs": 91}), once=True)
            elif state["page"] == "watch":
                from ui import watchlist
                watchlist.render(con)
            elif state["page"] == "quadrant":
                ui.label("象限圖為 v2 功能（先預留）。").style("color:var(--t3);")

    _render_nav()
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
