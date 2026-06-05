# ui/picks.py — 今日精選股面板（總覽頂部）
from nicegui import ui
from scanner import picks as P


def render(con, on_open_stock):
    stocks = P.gather(con)
    full, near = P.select(stocks)
    with ui.element("div").style("background:var(--card);border-radius:12px;padding:14px 16px;margin-bottom:18px;width:100%;box-sizing:border-box;"):
        ui.html('<span style="font-size:14px;font-weight:700;color:var(--accent)">★ 今日精選股</span>'
                '<span style="font-size:11px;color:var(--t3);margin-left:8px;">五因子全中：A/B級・RS≥80・法人連買・量增・低乖離非背離</span>')
        if not full:
            ui.label("今日符合全條件 0 檔（市場偏弱）").style("font-size:12px;color:var(--t3);margin:8px 0 4px;")
        else:
            for i, s in enumerate(full):
                _row(i + 1, s, on_open_stock, primary=True)
        if near:
            ui.html('<div style="font-size:12px;color:var(--t2);margin:12px 0 2px;">差一項候選（4/5）— 觀察名單</div>')
            for s, miss in near[:10]:
                _row(None, s, on_open_stock, primary=False, miss=miss)


def _row(idx, s, on_open_stock, primary=True, miss=None):
    up = s.get("today_pct", 0) >= 0
    row = ui.element("div").style(
        "display:flex;align-items:center;gap:10px;padding:8px 4px;flex-wrap:wrap;cursor:pointer;"
        "border-top:0.5px solid rgba(255,255,255,0.05);" + ("" if primary else "opacity:0.78;"))
    row.on("click", lambda e: on_open_stock(
        {"code": s["code"], "name": s["name"], "in_master": s.get("in_master", 1)}, s))
    with row:
        ui.label(str(idx) if idx else "›").classes("muted").style("width:18px;text-align:right;font-size:12px;")
        ui.html(f'<span><span class="mono muted">{s["code"]}</span> <b>{s["name"]}</b></span>')
        ui.label(f'{s.get("price", 0)}').classes("mono").style("font-size:12px;")
        ui.label(f'{s.get("today_pct", 0):+.1f}%').classes("mono " + ("up" if up else "down")).style("font-size:12px;")
        if primary:
            for tg in P.tags(s):
                ui.label(tg).style("font-size:10.5px;background:var(--elev);border-radius:5px;padding:2px 7px;color:var(--t2);")
        else:
            ui.label(f'缺：{miss}').style("font-size:10.5px;background:#3A2A14;border-radius:5px;padding:2px 7px;color:#E8C45C;")
        ui.label(s.get("theme", "")).classes("muted").style("font-size:10.5px;margin-left:auto;")
