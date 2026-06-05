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


def render(con, theme_id, on_open_stock, on_changed, get_row=_mock_row, header=None, on_refresh=None, price_cells=None):
    t = store.get_theme(con, theme_id)
    if not t:
        ui.label("題材不存在")
        return
    ui.html(f'<div style="font-size:12px;color:var(--t2);margin-bottom:12px;">題材總覽 › <b style="color:var(--text)">{t["name"]}</b></div>')
    # 題材標頭（header 由 scanner 聚合提供；無則顯示占位）
    with ui.element("div").style("display:flex;align-items:center;gap:18px;background:var(--card);border-radius:12px;padding:14px 18px;margin-bottom:16px;width:100%;box-sizing:border-box;"):
        ui.label(t["name"]).style("font-size:18px;font-weight:700;")
        if header:
            mom = header.get("momentum_5d", 0)
            mom_cls = "up" if mom >= 0 else "down"
            buy_n = header.get("inst_buy_count", 0)
            cnt = header.get("count", 0)
            avail = header.get("inst_avail_count", cnt)
            ui.html(f'<span style="font-size:12px;color:var(--t2)">5日動能<b class="{mom_cls}" style="font-size:15px;display:block;font-family:var(--mono)">{mom:+.1f}%</b></span>')
            ui.html(f'<span title="近5日法人買超家數 / 有法人資料家數" style="font-size:12px;color:var(--t2)">法人買超(5日)<b class="gold" style="font-size:15px;display:block;font-family:var(--mono)">{buy_n}/{avail}</b></span>')
            ui.html(f'<span style="font-size:12px;color:var(--t2)">家數<b style="font-size:15px;display:block;font-family:var(--mono)">{cnt}</b></span>')
        else:
            ui.html('<span style="font-size:12px;color:var(--t2)">5日動能<b class="muted" style="font-size:15px;display:block;font-family:var(--mono)">—</b></span>')
        ui.element("div").style("flex:1;")
        if on_refresh:
            ui.button("🔄 重新評估", on_click=on_refresh).props("flat dense no-caps").style("color:var(--t2);font-size:12px;")
    subs = store.list_sub_themes(con, theme_id)
    if subs:
        for i, s in enumerate(subs):
            _group(con, theme_id, s["id"], s["name"], on_open_stock, on_changed, get_row, open_default=(i == 0), price_cells=price_cells)
    else:
        _group(con, theme_id, None, t["name"], on_open_stock, on_changed, get_row, open_default=True, price_cells=price_cells)


def _group(con, theme_id, sub_id, title, on_open_stock, on_changed, get_row, open_default, price_cells=None):
    cons = store.list_constituents(con, theme_id, sub_id)
    with ui.element("div").style("background:var(--card);border-radius:12px;margin-bottom:10px;overflow:hidden;width:100%;box-sizing:border-box;"):
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
                _stock_row(i + 1, c, get_row(c["code"]), on_open_stock, price_cells)
            _add_row(con, theme_id, sub_id, title, on_changed)

        def toggle():
            opened["v"] = not opened["v"]
            rows_box.style("display:block;" if opened["v"] else "display:none;")
            chev.style("transform:rotate(90deg);" if opened["v"] else "transform:rotate(0deg);")
        head.on("click", lambda e: toggle())


_GRID = ("display:grid;grid-template-columns:22px 1.5fr 0.8fr 0.7fr 0.7fr 0.5fr 0.7fr 0.7fr 0.7fr 1fr;"
         "align-items:center;gap:6px;")


def _header_row():
    # 外/投/自 = 三大法人近5日各別買賣超（張）
    cols = [("", "left", None), ("代號 / 名稱", "left", None), ("現價", "right", None), ("今日", "right", None),
            ("5日", "right", None), ("RS", "right", None),
            ("外", "right", "外資（近5日買賣超，張）"), ("投", "right", "投信（近5日買賣超，張）"),
            ("自", "right", "自營商（近5日買賣超，張）"), ("訊號", "left", None)]
    with ui.element("div").style(_GRID + "padding:9px 16px;font-size:11px;color:var(--t3);"):
        for text, align, tip in cols:
            if tip:
                ui.html(f'<span title="{tip}" style="text-align:{align};display:block;cursor:help;">{text}</span>')
            else:
                ui.label(text).style(f"text-align:{align};")


def _abbr(v):
    """大數縮寫：≥1000 → +1.2k；否則 +860；None → —。"""
    if v is None:
        return "—"
    sign = "+" if v >= 0 else "-"
    a = abs(v)
    return f"{sign}{a / 1000:.1f}k" if a >= 1000 else f"{sign}{a}"


def _inst_cell(val, cons=0, dim=False):
    """法人單欄：買超金/賣超綠；自營略淡；連買≥2 掛小標。在當前 grid context 建立。"""
    cls = "muted" if (val is None or val == 0) else ("gold" if val > 0 else "down")
    extra = "opacity:0.8;" if dim else ""
    if cons and cons >= 2:
        with ui.element("div").style("display:flex;flex-direction:column;align-items:flex-end;line-height:1.05;" + extra):
            ui.label(_abbr(val)).classes(f"mono {cls}").style("text-align:right;")
            ui.label(f"連{cons}買").style("font-size:9px;color:var(--inst);")
    else:
        ui.label(_abbr(val)).classes(f"mono {cls}").style("text-align:right;" + extra)


_SHORT = {"grade_A": "A 主攻", "grade_B": "B 追蹤", "grade_C": "觀察", "grade_sell": "賣出"}


def _stock_row(idx, c, r, on_open_stock, price_cells=None):
    dc = "up" if r["today_pct"] >= 0 else "down"
    fc = "up" if r["d5_pct"] >= 0 else "down"
    rsc = "gold" if r["rs"] >= 80 else "muted"
    sig = r["signal"]
    tag = theme.grade_tag(sig) or "grade_C"
    bg, fg = theme.BADGE[tag]
    label = "暫缺" if sig == "資料暫缺" else _SHORT.get(tag, "觀察")
    flag = "" if c["in_master"] else " ⚑"
    row = ui.element("div").style(_GRID + "padding:9px 16px;font-size:13px;border-top:0.5px solid rgba(255,255,255,0.04);cursor:pointer;")
    row.on("click", lambda e: on_open_stock(c, r))
    with row:
        ui.label(str(idx)).classes("muted")
        ui.html(f'<span><span class="mono muted">{c["code"]}</span> {c["name"]}{flag}</span>')
        price_label = ui.label(f'{r["price"]}').classes("mono").style("text-align:right;")
        today_label = ui.label(f'{r["today_pct"]:+.1f}%').classes(f"mono {dc}").style("text-align:right;")
        ui.label(f'{r["d5_pct"]:+.1f}%').classes(f"mono {fc}").style("text-align:right;")
        ui.label(f'{r["rs"]}').classes(f"mono {rsc}").style("text-align:right;")
        # 三大法人近5日：外 / 投 / 自
        _fc, _tc = r.get("fcons", 0) or 0, r.get("tcons", 0) or 0
        _inst_cell(r.get("foreign_5d"), cons=(_fc if _fc >= 2 else 0))
        _inst_cell(r.get("trust_5d"), cons=(_tc if _tc >= 2 else 0))
        _inst_cell(r.get("dealer_5d"), dim=True)
        # 短標籤 badge（完整訊號滑鼠移上顯示）
        ui.html(f'<span title="{sig}" style="font-size:12px;padding:4px 10px;border-radius:7px;font-weight:600;'
                f'white-space:nowrap;background:{bg};color:{fg}">{label}</span>')
    if price_cells is not None:
        price_cells[c["code"]] = (price_label, today_label)


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
