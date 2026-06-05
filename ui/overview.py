# ui/overview.py
from nicegui import ui
from ui import components
from concept import store
from data import fetcher


def render(con, on_open_theme, get_metrics, on_theme_changed, on_refresh=None):
    """總覽頁。get_metrics() → List[ThemeMetrics]；on_open_theme(theme_metrics)。"""
    metrics = get_metrics()
    if not metrics:
        ui.label("尚無題材資料").style("color:var(--t3);")
        return
    # KPI 只統計已掃完（非 pending）的題材
    real = [m for m in metrics if not getattr(m, "pending", False)]
    strongest = max(real, key=lambda m: m.momentum_5d) if real else None
    most_inst = max(real, key=lambda m: m.inst_net) if real else None
    diverge_n = sum(1 for m in real if m.diverge)
    idx = fetcher.get_index()
    if idx:
        idx_val = f'{idx["value"]:,.0f}'
        idx_sub = f'{idx["change_pct"]:+.2f}%'
        idx_cls = "up" if idx["change_pct"] >= 0 else "down"
    else:
        idx_val, idx_sub, idx_cls = "—", "取得中…", "muted"
    with ui.element("div").style("display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:18px;"):
        if strongest:
            components.kpi_card("動能最強", strongest.name, f"5日 {strongest.momentum_5d:+.1f}%",
                                "up" if strongest.momentum_5d >= 0 else "down")
        else:
            components.kpi_card("動能最強", "—", "掃描中…", "muted")
        if most_inst:
            components.kpi_card("法人最買超", most_inst.name, "外資+投信 淨買", "gold")
        else:
            components.kpi_card("法人最買超", "—", "掃描中…", "muted")
        components.kpi_card("加權指數", idx_val, idx_sub, idx_cls)
        components.kpi_card("背離警示", f"{diverge_n} 個題材", "動能與法人方向相反", "muted")
    # section header
    with ui.element("div").style("display:flex;align-items:center;justify-content:space-between;margin:6px 0 12px;"):
        ui.label("題材熱度 ＋ 法人").style("font-size:13px;color:#C9CDD2;font-weight:600;")
        if on_refresh:
            ui.button("🔄 重新掃描", on_click=on_refresh).props("flat dense no-caps").style("color:var(--t2);font-size:12px;")
    # heatmap
    with ui.element("div").style("display:flex;flex-wrap:wrap;gap:7px;"):
        for m in metrics:
            components.heat_tile(m, on_open_theme)
        components.add_tile(lambda: _new_theme_dialog(con, on_theme_changed))
    ui.label("填色=5日動能(紅漲綠跌)；塊底金條=法人買超；⚠=背離。點題材→明細；點「＋ 新題材」就地新增。"
             ).style("font-size:11px;color:var(--t3);margin-top:12px;line-height:1.6;")


def _new_theme_dialog(con, on_theme_changed):
    with ui.dialog() as dlg, ui.card().style("background:var(--card);"):
        ui.label("新增題材").style("font-weight:600;")
        name = ui.input("題材名稱")
        subs = ui.input("子題材（逗號分隔，可空）")
        with ui.row():
            ui.button("取消", on_click=dlg.close).props("flat")

            def save():
                if not name.value:
                    return
                tid = store.add_theme(con, name=name.value, is_custom=True)
                for s in [x.strip() for x in (subs.value or "").split(",") if x.strip()]:
                    store.add_sub_theme(con, tid, name=s)
                dlg.close()
                on_theme_changed()
            ui.button("新增", on_click=save).props("color=amber")
    dlg.open()
