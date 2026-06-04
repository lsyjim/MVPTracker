# ui/ranking.py — 今日漲幅排行（題材 + 個股），讀當日快取，不重新掃描
from nicegui import ui
from concept import store
from data import cache


def _gather(con):
    stocks = []        # {code,name,in_master,theme,row}
    themes = []        # {id,name,today,n}
    for t in store.list_themes(con):
        cons = store.list_constituents(con, t["id"], None)
        for s in store.list_sub_themes(con, t["id"]):
            cons += store.list_constituents(con, t["id"], s["id"])
        todays = []
        for c in cons:
            cached, ts = cache.get(con, c["code"], "row")
            if cached and cached.get("_ok") and cache.is_today(ts):
                stocks.append({"code": c["code"], "name": c["name"], "in_master": c["in_master"],
                               "theme": t["name"], "row": cached})
                todays.append(cached["today_pct"])
        if todays:
            themes.append({"id": t["id"], "name": t["name"], "today": sum(todays) / len(todays), "n": len(todays)})
    return stocks, themes


def render(con, on_open_theme, on_open_stock):
    stocks, themes = _gather(con)
    if not stocks:
        ui.label("尚無資料：請先到『總覽』完成一次掃描（會建立當日快取）。").style("color:var(--t3);")
        return
    ui.label("今日漲幅排行").style("font-size:15px;font-weight:600;color:#C9CDD2;margin-bottom:8px;")
    with ui.element("div").style("display:grid;grid-template-columns:1fr 1fr;gap:16px;align-items:start;"):
        _theme_panel(sorted(themes, key=lambda x: x["today"], reverse=True), on_open_theme)
        _stock_panel(sorted(stocks, key=lambda x: x["row"]["today_pct"], reverse=True), on_open_stock)


def _panel(title):
    box = ui.element("div").style("background:var(--card);border-radius:12px;overflow:hidden;")
    with box:
        ui.label(title).style("font-size:13px;font-weight:600;padding:12px 16px;display:block;")
    return box


def _theme_panel(themes, on_open_theme):
    box = _panel("題材排行")
    with box:
        for i, t in enumerate(themes):
            cls = "up" if t["today"] >= 0 else "down"
            row = ui.element("div").style(
                "display:grid;grid-template-columns:28px 1fr auto 64px;align-items:center;gap:8px;"
                "padding:9px 16px;font-size:13px;border-top:0.5px solid rgba(255,255,255,0.04);cursor:pointer;")
            row.on("click", lambda e, tid=t["id"]: on_open_theme(tid))
            with row:
                ui.label(str(i + 1)).classes("muted")
                ui.label(t["name"])
                ui.label(f'{t["n"]} 檔').classes("muted").style("font-size:11px;")
                ui.label(f'{t["today"]:+.1f}%').classes(f"mono {cls}").style("text-align:right;")


def _stock_panel(stocks, on_open_stock):
    box = _panel(f"個股排行（前 {min(30, len(stocks))}）")
    with box:
        for i, s in enumerate(stocks[:30]):
            r = s["row"]
            cls = "up" if r["today_pct"] >= 0 else "down"
            flag = "" if s["in_master"] else " ⚑"
            row = ui.element("div").style(
                "display:grid;grid-template-columns:28px 1.4fr 1fr 70px;align-items:center;gap:8px;"
                "padding:9px 16px;font-size:13px;border-top:0.5px solid rgba(255,255,255,0.04);cursor:pointer;")
            row.on("click", lambda e, c=s: on_open_stock(
                {"code": c["code"], "name": c["name"], "in_master": c["in_master"]}, c["row"]))
            with row:
                ui.label(str(i + 1)).classes("muted")
                ui.html(f'<span><span class="mono muted">{s["code"]}</span> {s["name"]}{flag}</span>')
                ui.label(s["theme"]).classes("muted").style("font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;")
                ui.label(f'{r["today_pct"]:+.1f}%').classes(f"mono {cls}").style("text-align:right;")
