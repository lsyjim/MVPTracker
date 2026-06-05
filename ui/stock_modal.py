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


def _abbr(v):
    if v is None:
        return "—"
    sign = "+" if v >= 0 else "-"
    a = abs(v)
    return f"{sign}{a / 1000:.1f}k" if a >= 1000 else f"{sign}{a}"


def _color(v):
    v = v or 0
    return "var(--inst)" if v > 0 else ("var(--down)" if v < 0 else "var(--t2)")


def _inst_table(chip):
    """三大法人（近 5 日）小表：列=日期，欄=外/投/自，末列=5日合計。"""
    if not chip or not chip.get("available") or not chip.get("items"):
        ui.label("三大法人：資料暫缺").style("font-size:11px;color:var(--t3);margin-top:8px;")
        return
    ui.label("三大法人（近 5 日，張）").style("font-size:12px;color:#C9CDD2;font-weight:600;margin:10px 0 4px;")
    grid = "display:grid;grid-template-columns:1.2fr 1fr 1fr 1fr;gap:2px 8px;"
    with ui.element("div").style(grid):
        ui.label("日期").style("color:var(--t3);font-size:11px;")
        for h in ("外資", "投信", "自營"):
            ui.label(h).style("color:var(--t3);font-size:11px;text-align:right;")
        for it in chip["items"][:5]:
            ui.label(it["date"][5:].replace("-", "/")).classes("mono").style("color:var(--t2);font-size:11px;")
            for k, dim in (("foreign", False), ("trust", False), ("dealer", True)):
                ui.html(f'<span class="mono" style="display:block;text-align:right;font-size:12px;'
                        f'color:{_color(it[k])};{"opacity:0.8;" if dim else ""}">{_abbr(it[k])}</span>')
        ui.label("5日合計").style("color:#C9CDD2;font-size:11px;font-weight:600;border-top:0.5px solid var(--line);padding-top:3px;")
        for k, dim in (("foreign_5d", False), ("trust_5d", False), ("dealer_5d", True)):
            v = chip.get(k)
            ui.html(f'<span class="mono" style="display:block;text-align:right;font-size:12px;font-weight:600;'
                    f'border-top:0.5px solid var(--line);padding-top:3px;color:{_color(v)};{"opacity:0.8;" if dim else ""}">{_abbr(v)}</span>')
    fc = chip.get("foreign_consecutive_days", 0) or 0
    tc = chip.get("trust_consecutive_days", 0) or 0
    parts = ([f"外資連{fc}買"] if fc >= 2 else []) + ([f"投信連{tc}買"] if tc >= 2 else [])
    if parts:
        ui.label(" / ".join(parts)).style("font-size:11px;color:var(--inst);margin-top:5px;")


def _chips(r):
    out = [f'RS {r.get("rs", "—")}']
    vr = r.get("vol_ratio")
    if vr:
        out.append(f'量比 {vr}')
    fc, tc = r.get("fcons", 0) or 0, r.get("tcons", 0) or 0
    if fc >= 2:
        out.append(f'外資連{fc}買')
    elif tc >= 2:
        out.append(f'投信連{tc}買')
    bias = r.get("bias")
    if bias is not None:
        out.append(f'乖離 {bias:.0f}%')
    return out


def open_modal(c, r, get_ohlc=None, on_add_watch=None, on_report=None, get_chip=None):
    ohlc = (get_ohlc(c["code"]) if get_ohlc else None) or _mock_ohlc()
    x = [str(i) for i in range(len(ohlc))]
    up = r["today_pct"] >= 0
    chip = get_chip(c["code"]) if get_chip else None
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
            with ui.element("div").style("display:flex;gap:8px;flex-wrap:wrap;margin:12px 0;"):
                for ch in _chips(r):
                    ui.label(ch).style("font-size:11px;background:var(--elev);border-radius:6px;padding:4px 10px;color:var(--t2);")
            _inst_table(chip)
            with ui.element("div").style("display:flex;gap:10px;margin-top:14px;"):
                ui.button("＋ 加入自選股", on_click=lambda: (on_add_watch and on_add_watch(c))).props("flat no-caps").style(
                    "flex:1;background:#1C222B !important;color:#E6E8EB !important;")
                ui.button("產生完整分析報告", on_click=lambda: (on_report and on_report(c))).props("unelevated no-caps").style(
                    "flex:1;background:#EF9F27 !important;color:#0E1116 !important;font-weight:600;")
    dlg.open()
