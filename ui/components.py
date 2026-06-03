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
    tile = ui.element("div").style(
        f"position:relative;height:72px;border-radius:9px;padding:9px 11px 12px;flex:{flex} 1 102px;"
        f"min-width:104px;background:{bg};cursor:pointer;overflow:hidden;display:flex;flex-direction:column;justify-content:space-between;"
    )
    tile.on("click", lambda e, t=m: on_click(t))
    with tile:
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
