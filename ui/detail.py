# ui/detail.py
from nicegui import ui
from concept import store
from ui import theme


def _mock_row(code):
    import random
    random.seed(hash(code) % 9999)
    t = round(random.uniform(-3, 4), 1)
    d5 = round(random.uniform(-5, 9), 1)
    rs = random.randint(40, 95)
    inst = random.randint(-800, 1500)
    sig = "可進場 A" if rs >= 80 else ("偏多 續抱" if rs >= 65 else "觀察")
    return {"price": round(random.uniform(40, 1000), 1), "today_pct": t, "d5_pct": d5, "rs": rs, "inst": inst, "signal": sig}


def render(con, theme_id, on_open_stock, on_changed, get_row=_mock_row, header=None):
    t = store.get_theme(con, theme_id)
    if not t:
        ui.label("題材不存在")
        return
    ui.html(f'<div style="font-size:12px;color:var(--t2);margin-bottom:12px;">題材總覽 › <b style="color:var(--text)">{t["name"]}</b></div>')
    # 題材標頭（header 由 scanner 聚合提供；無則顯示占位）
    with ui.element("div").style("display:flex;align-items:center;gap:18px;background:var(--card);border-radius:12px;padding:14px 18px;margin-bottom:16px;"):
        ui.label(t["name"]).style("font-size:18px;font-weight:700;")
        if header:
            mom = header.get("momentum_5d", 0)
            mom_cls = "up" if mom >= 0 else "down"
            buy_n = header.get("inst_buy_count", 0)
            cnt = header.get("count", 0)
            ui.html(f'<span style="font-size:12px;color:var(--t2)">5日動能<b class="{mom_cls}" style="font-size:15px;display:block;font-family:var(--mono)">{mom:+.1f}%</b></span>')
            ui.html(f'<span style="font-size:12px;color:var(--t2)">法人買超<b class="gold" style="font-size:15px;display:block;font-family:var(--mono)">{buy_n}/{cnt}</b></span>')
            ui.html(f'<span style="font-size:12px;color:var(--t2)">家數<b style="font-size:15px;display:block;font-family:var(--mono)">{cnt}</b></span>')
        else:
            ui.html('<span style="font-size:12px;color:var(--t2)">5日動能<b class="muted" style="font-size:15px;display:block;font-family:var(--mono)">—</b></span>')
    subs = store.list_sub_themes(con, theme_id)
    if subs:
        for i, s in enumerate(subs):
            _group(con, theme_id, s["id"], s["name"], on_open_stock, on_changed, get_row, open_default=(i == 0))
    else:
        _group(con, theme_id, None, t["name"], on_open_stock, on_changed, get_row, open_default=True)


def _group(con, theme_id, sub_id, title, on_open_stock, on_changed, get_row, open_default):
    cons = store.list_constituents(con, theme_id, sub_id)
    with ui.element("div").style("background:var(--card);border-radius:12px;margin-bottom:10px;overflow:hidden;"):
        opened = {"v": open_default}
        head = ui.element("div").style("display:flex;align-items:center;gap:12px;padding:12px 16px;cursor:pointer;")
        with head:
            chev = ui.label("▶").style(f"color:var(--t3);display:inline-block;{'transform:rotate(90deg);' if open_default else ''}")
            ui.label(title).style("font-size:14px;font-weight:600;")
            ui.label(f"{len(cons)} 檔").style("font-size:12px;color:var(--t2);font-family:var(--mono);")
        rows_box = ui.element("div").style(f"border-top:0.5px solid var(--line);{'' if open_default else 'display:none;'}")
        with rows_box:
            _header_row()
            for i, c in enumerate(cons):
                _stock_row(i + 1, c, get_row(c["code"]), on_open_stock)
            _add_row(con, theme_id, sub_id, title, on_changed)

        def toggle():
            opened["v"] = not opened["v"]
            rows_box.style("display:block;" if opened["v"] else "display:none;")
            chev.style("transform:rotate(90deg);" if opened["v"] else "transform:rotate(0deg);")
        head.on("click", lambda e: toggle())


def _header_row():
    cols = ["", "代號 / 名稱", "現價", "今日", "5日", "RS", "法人(張)", "訊號"]
    with ui.element("div").style("display:grid;grid-template-columns:24px 1.5fr 0.8fr 0.8fr 0.7fr 0.5fr 1fr 1.1fr;padding:9px 16px;gap:6px;font-size:11px;color:var(--t3);"):
        for c in cols:
            ui.label(c)


def _stock_row(idx, c, r, on_open_stock):
    dc = "up" if r["today_pct"] >= 0 else "down"
    fc = "up" if r["d5_pct"] >= 0 else "down"
    ic = "down" if r["inst"] < 0 else "gold"
    rsc = "gold" if r["rs"] >= 80 else "muted"
    tag = theme.grade_tag(r["signal"]) or "grade_C"
    bg, fg = theme.BADGE[tag]
    flag = "" if c["in_master"] else " ⚑"
    row = ui.element("div").style("display:grid;grid-template-columns:24px 1.5fr 0.8fr 0.8fr 0.7fr 0.5fr 1fr 1.1fr;align-items:center;padding:9px 16px;gap:6px;font-size:13px;border-top:0.5px solid rgba(255,255,255,0.04);cursor:pointer;")
    row.on("click", lambda e: on_open_stock(c, r))
    with row:
        ui.label(str(idx)).classes("muted")
        ui.html(f'<span><span class="mono muted">{c["code"]}</span> {c["name"]}{flag}</span>')
        ui.label(f'{r["price"]}').classes("mono").style("text-align:right;")
        ui.label(f'{r["today_pct"]:+.1f}%').classes(f"mono {dc}").style("text-align:right;")
        ui.label(f'{r["d5_pct"]:+.1f}%').classes(f"mono {fc}").style("text-align:right;")
        ui.label(f'{r["rs"]}').classes(f"mono {rsc}").style("text-align:right;")
        ui.label(f'{r["inst"]:+,}').classes(f"mono {ic}").style("text-align:right;")
        ui.html(f'<span style="font-size:12px;padding:4px 10px;border-radius:7px;font-weight:600;background:{bg};color:{fg}">{r["signal"]}</span>')


def _add_row(con, theme_id, sub_id, title, on_changed):
    el = ui.element("div").style("padding:10px 16px;font-size:12px;color:var(--t2);cursor:pointer;border-top:0.5px solid rgba(255,255,255,0.04);")
    with el:
        ui.label(f"＋ 新增個股到「{title}」")
    el.on("click", lambda e: _add_stock_dialog(con, theme_id, sub_id, on_changed))


def _add_stock_dialog(con, theme_id, sub_id, on_changed):
    from concept import master
    with ui.dialog() as dlg, ui.card().style("background:var(--card);"):
        ui.label("新增個股").style("font-weight:600;")
        code = ui.input("代號")
        name = ui.input("名稱（查不到母清單時手動填）")

        def lookup():
            nm = master.lookup_name(code.value)
            if nm:
                name.value = nm
        code.on("blur", lambda e: lookup())
        with ui.row():
            ui.button("取消", on_click=dlg.close).props("flat")

            def save():
                if not code.value:
                    return
                in_master = 1 if master.lookup_name(code.value) else 0
                nm = name.value or master.lookup_name(code.value) or code.value
                store.add_constituent(con, theme_id, code.value, nm, sub_theme_id=sub_id, in_master=in_master)
                dlg.close()
                on_changed()
            ui.button("新增", on_click=save).props("color=amber")
    dlg.open()
