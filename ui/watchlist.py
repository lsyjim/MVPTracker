# ui/watchlist.py
from nicegui import ui
from concept import watchstore


def render(con, on_open_stock=None):
    items = watchstore.list_all(con)
    if not items:
        ui.label("自選股清單為空。於個股彈窗按「加入自選股」加入。").style("font-size:13px;color:var(--t3);")
        return
    for it in items:
        with ui.element("div").style("display:flex;align-items:center;gap:12px;background:var(--card);border-radius:10px;padding:10px 14px;margin-bottom:8px;"):
            ui.html(f'<span><span class="mono muted">{it["code"]}</span> {it["name"]}</span>')
            ui.element("div").style("flex:1;")
            ui.button("移除", on_click=lambda e, w=it["id"]: (watchstore.remove(con, w), ui.navigate.to("/"))).props("flat dense")
