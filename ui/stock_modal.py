# ui/stock_modal.py
from nicegui import ui


def _mock_ohlc(n=40):
    import random
    random.seed(7)
    p = 400
    out = []
    for _ in range(n):
        o = p
        c = max(5, o + (random.random() - 0.45) * 16)
        h = max(o, c) + random.random() * 8
        l = min(o, c) - random.random() * 8
        out.append([round(o, 1), round(c, 1), round(l, 1), round(h, 1)])  # ECharts: [open,close,low,high]
        p = c
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
                "grid": {"left": 40, "right": 12, "top": 10, "bottom": 20},
                "xAxis": {"type": "category", "data": x, "axisLabel": {"show": False}},
                "yAxis": {"type": "value", "scale": True, "axisLabel": {"color": "#6B7079"}},
                "series": [{
                    "type": "candlestick", "data": ohlc,
                    "itemStyle": {"color": "#F0696A", "color0": "#4CB782", "borderColor": "#F0696A", "borderColor0": "#4CB782"}
                }]
            }).style("height:180px;width:100%;")
            chips = [f'RS {r["rs"]}', "KD 黃金交叉", "均線 多頭排列", "量增 +35%", "法人連買 4 日"]
            with ui.element("div").style("display:flex;gap:8px;flex-wrap:wrap;margin:12px 0;"):
                for ch in chips:
                    ui.label(ch).style("font-size:11px;background:var(--elev);border-radius:6px;padding:4px 10px;color:var(--t2);")
            with ui.element("div").style("display:flex;gap:10px;margin-top:14px;"):
                ui.button("＋ 加入自選股", on_click=lambda: (on_add_watch and on_add_watch(c))).props("flat no-caps").style(
                    "flex:1;background:#1C222B !important;color:#E6E8EB !important;")
                ui.button("產生完整分析報告", on_click=lambda: (on_report and on_report(c))).props("unelevated no-caps").style(
                    "flex:1;background:#EF9F27 !important;color:#0E1116 !important;font-weight:600;")
    dlg.open()
