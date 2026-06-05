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


def _price_table(recent):
    """近 5 日股價：日期 / 收盤 / 漲跌%（前一交易日比較；紅漲綠跌）。"""
    ui.label("近 5 日股價").style("font-size:12px;color:#C9CDD2;font-weight:600;margin:0 0 4px;")
    if not recent:
        ui.label("資料暫缺").style("font-size:11px;color:var(--t3);")
        return
    with ui.element("div").style("display:grid;grid-template-columns:1.1fr 1fr 1fr;gap:2px 8px;"):
        ui.label("日期").style("color:var(--t3);font-size:11px;")
        ui.label("收盤").style("color:var(--t3);font-size:11px;text-align:right;")
        ui.label("漲跌").style("color:var(--t3);font-size:11px;text-align:right;")
        for it in list(reversed(recent))[:5]:
            ui.label(it["date"][5:].replace("-", "/")).classes("mono").style("color:var(--t2);font-size:11px;")
            ui.label(f'{it["close"]}').classes("mono").style("text-align:right;font-size:12px;")
            cls = "up" if it["pct"] >= 0 else "down"
            ui.label(f'{it["pct"]:+.1f}%').classes(f"mono {cls}").style("text-align:right;font-size:12px;")


def _inst_table(chip, loading=False):
    """三大法人（近 5 日）小表：列=日期，欄=外/投/自，末列=5日合計。"""
    if not chip or not chip.get("available") or not chip.get("items"):
        ui.label("三大法人：資料暫缺").style("font-size:11px;color:var(--t3);margin-top:8px;")
        return
    src = "・TWSE 完整" if chip.get("source") == "TWSE" else ("・補完整中…" if loading else "")
    ui.label(f"三大法人（近 5 日，張{src}）").style("font-size:12px;color:#C9CDD2;font-weight:600;margin:0 0 4px;")
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


def open_modal(c, r, get_ohlc=None, on_add_watch=None, on_report=None, get_chip=None, get_full=None):
    from nicegui import run
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
            # 兩欄並排：左=近5日股價、右=三大法人近5日（填滿底部）
            with ui.element("div").style("display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:6px;align-items:start;"):
                with ui.element("div").style("min-width:0;"):
                    _price_table(r.get("recent"))
                inst_box = ui.element("div").style("min-width:0;")
                with inst_box:
                    _inst_table(chip, loading=bool(get_full))
            # on-demand 補爬 TWSE 完整法人（連續日期 + 正確連買），完成後替換 wukong 初值
            if get_full:
                async def _load_full():
                    full = await run.io_bound(get_full, c["code"])
                    if full and full.get("available"):
                        inst_box.clear()
                        with inst_box:
                            _inst_table(full)
                ui.timer(0.05, _load_full, once=True)
            with ui.element("div").style("display:flex;gap:10px;margin-top:14px;"):
                ui.button("＋ 加入自選股", on_click=lambda: (on_add_watch and on_add_watch(c))).props("flat no-caps").style(
                    "flex:1;background:#1C222B !important;color:#E6E8EB !important;")
                ui.button("產生完整分析報告", on_click=lambda: (on_report and on_report(c))).props("unelevated no-caps").style(
                    "flex:1;background:#EF9F27 !important;color:#0E1116 !important;font-weight:600;")
    dlg.open()
