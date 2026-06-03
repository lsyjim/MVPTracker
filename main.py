# main.py
import os, sys, json
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)                              # config 等頂層模組
sys.path.insert(0, os.path.join(ROOT, "analysis"))   # 讓 vendored 後端扁平 import 解析

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
    items = [("overview", "總覽", False), ("quadrant", "象限", True), ("detail", "明細", False), ("watch", "自選", False)]
    with ui.element("div").style("width:80px;background:var(--rail);padding:14px 0;display:flex;flex-direction:column;gap:6px;"):
        for key, label, disabled in items:
            active = state["page"] == key
            color = "var(--accent)" if active else ("#4A4F57" if disabled else "#7E848C")
            el = ui.element("div").style(
                f"display:flex;flex-direction:column;align-items:center;gap:4px;padding:11px 0;color:{color};"
                f"border-left:3px solid {'var(--accent)' if active else 'transparent'};font-size:10.5px;"
                f"{'background:rgba(239,159,39,0.10);' if active else ''}{'opacity:0.5;' if disabled else 'cursor:pointer;'}")
            with el:
                ui.label(label)
            if not disabled:
                el.on("click", lambda e, k=key: _go(k))


def _go(page, theme_id=None):
    state["page"] = page
    state["theme_id"] = theme_id
    ui.navigate.to("/")


def _render_page(content):
    with content:
        if state["page"] == "overview":
            overview.render(con, on_open_theme=lambda m: _go("detail", m.theme_id),
                            get_metrics=get_metrics, on_theme_changed=lambda: _go("overview"))
        elif state["page"] == "detail":
            ui.label("明細頁（Task 11 實作）")
        elif state["page"] == "watch":
            ui.label("自選頁（Task 19 實作）")


if os.environ.get("MVP_WEB") == "1":
    # 測試/驗證用：瀏覽器模式（headless 可截圖）
    ui.run(native=False, port=int(os.environ.get("MVP_PORT", "8111")), reload=False, show=False)
else:
    ui.run(native=True, title="MVPTracker", window_size=(1240, 860), reload=False)
