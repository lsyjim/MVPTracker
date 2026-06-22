# ui/recommend.py — 推薦名單：列出所有「建議買進」(A/B 級)個股。資料取自當日掃描快取，不另運算。
from nicegui import ui
from scanner import picks as P
from ui import theme
from ui.detail import _abbr

_GRID = ("display:grid;grid-template-columns:24px 1.5fr 0.85fr 0.7fr 0.5fr 0.9fr 1.1fr 1.4fr;"
         "align-items:center;gap:8px;")
_SHORT = {"grade_A": "A 主攻", "grade_B": "B 追蹤"}
_ORDER = {"grade_A": 0, "grade_B": 1}


def render(con, on_open_stock):
    stocks = P.gather(con)
    buys = [s for s in stocks if s.get("_ok") and s.get("grade") in ("grade_A", "grade_B")]
    ui.label("推薦名單（建議買進 · A/B 級）").style("font-size:15px;font-weight:600;color:#C9CDD2;margin-bottom:8px;")
    if not buys:
        ui.label("目前沒有『建議買進』的個股（市場偏弱，或尚未在『總覽』完成掃描）。").style("font-size:13px;color:var(--t3);")
        return
    # A 級優先，再依 RS 由高到低
    buys.sort(key=lambda s: (_ORDER.get(s["grade"], 9), -s.get("rs", 0)))
    with ui.element("div").style("background:var(--card);border-radius:12px;overflow:hidden;width:100%;box-sizing:border-box;"):
        ui.label(f"共 {len(buys)} 檔").style("font-size:12px;color:var(--t2);padding:10px 16px 4px;display:block;")
        _header()
        for i, s in enumerate(buys):
            _row(i + 1, s, on_open_stock)


def _header():
    cols = [("", "left"), ("代號 / 名稱", "left"), ("現價", "right"), ("今日", "right"),
            ("RS", "right"), ("法人5日", "right"), ("族群", "left"), ("建議", "left")]
    with ui.element("div").style(_GRID + "padding:9px 16px;font-size:11px;color:var(--t3);"):
        for text, align in cols:
            ui.label(text).style(f"text-align:{align};")


def _row(idx, s, on_open_stock):
    dc = "up" if s.get("today_pct", 0) >= 0 else "down"
    rsc = "gold" if s.get("rs", 0) >= 80 else "muted"
    inst = s.get("inst")
    ic = "muted" if (inst is None or inst == 0) else ("gold" if inst > 0 else "down")
    grade = s.get("grade", "grade_C")
    bg, fg = theme.BADGE.get(grade, theme.BADGE["grade_C"])
    flag = "" if s.get("in_master", 1) else " ⚑"
    row = ui.element("div").style(_GRID + "padding:9px 16px;font-size:13px;border-top:0.5px solid rgba(255,255,255,0.04);cursor:pointer;")
    row.on("click", lambda e: on_open_stock(
        {"code": s["code"], "name": s["name"], "in_master": s.get("in_master", 1)}, s))
    with row:
        ui.label(str(idx)).classes("muted")
        ui.html(f'<span><span class="mono muted">{s["code"]}</span> {s["name"]}{flag}</span>')
        ui.label(f'{s.get("price", 0)}').classes("mono").style("text-align:right;")
        ui.label(f'{s.get("today_pct", 0):+.1f}%').classes(f"mono {dc}").style("text-align:right;")
        ui.label(f'{s.get("rs", 0)}').classes(f"mono {rsc}").style("text-align:right;")
        ui.label(_abbr(inst)).classes(f"mono {ic}").style("text-align:right;")
        ui.label(s.get("theme", "")).classes("muted").style("font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;")
        # 建議：等級 badge + 訊號全文（hover）
        short = _SHORT.get(grade, "觀察")
        ui.html(f'<span title="{s.get("signal", "")}" style="font-size:12px;padding:3px 9px;border-radius:7px;font-weight:600;'
                f'white-space:nowrap;background:{bg};color:{fg}">{short}</span>'
                f'<span class="muted" style="font-size:11px;margin-left:6px;">{s.get("signal", "")}</span>')
