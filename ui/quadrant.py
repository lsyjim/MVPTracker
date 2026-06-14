# ui/quadrant.py — 動能 × 法人 象限散佈圖（沿用 get_metrics 的 ThemeMetrics，不另抓資料）
from nicegui import ui
from ui import theme


def render(metrics, on_open_theme):
    """metrics: List[ThemeMetrics]；on_open_theme(theme_id)。"""
    real = [m for m in metrics if not getattr(m, "pending", False)]
    if not real:
        ui.label("請先在『總覽』完成掃描，再查看象限圖。").style("color:var(--t3);font-size:13px;")
        return

    data = []
    for m in real:
        bg, _ = theme.momentum_color(m.momentum_5d)     # 沿用熱圖紅綠
        data.append({
            "value": [round(m.momentum_5d, 2), round(m.inst_net, 1)],
            "name": m.name,
            "theme_id": m.theme_id,
            "count": m.count,
            "symbolSize": min(60, 14 + m.count * 1.3),  # 泡泡大小依家數
            "itemStyle": {"color": bg + "cc", "borderColor": "rgba(255,255,255,0.22)"},  # 半透明
        })

    ui.label("動能 × 法人 四象限").style("font-size:13px;color:#C9CDD2;font-weight:600;margin-bottom:8px;")
    with ui.element("div").style("position:relative;background:var(--card);border-radius:12px;padding:10px;width:100%;box-sizing:border-box;"):
        # 四象限標籤（絕對定位疊在圖上）
        ui.label("量價籌俱揚").style("position:absolute;top:18px;right:26px;color:#F0696A;font-size:11px;z-index:5;")
        ui.label("漲但法人賣 ⚠").style("position:absolute;bottom:48px;right:26px;color:#E8C45C;font-size:11px;z-index:5;")
        ui.label("跌但法人買 ⚠").style("position:absolute;top:18px;left:74px;color:#E8C45C;font-size:11px;z-index:5;")
        ui.label("弱勢續跌").style("position:absolute;bottom:48px;left:74px;color:#4CB782;font-size:11px;z-index:5;")
        ui.label("泡泡大小＝家數").style("position:absolute;bottom:12px;right:26px;color:var(--t3);font-size:10px;z-index:5;")

        chart = ui.echart({
            "backgroundColor": "transparent",
            "grid": {"left": 58, "right": 26, "top": 30, "bottom": 46},
            # 不用 JS formatter（需 pyecharts）：預設 item tooltip 顯示題材名 + (動能, 法人)；
            # 家數由泡泡大小呈現。
            "tooltip": {"trigger": "item"},
            "xAxis": {"type": "value", "name": "動能 →", "nameLocation": "end", "scale": True,
                      "axisLine": {"onZero": True, "lineStyle": {"color": "#6B7079"}},
                      "splitLine": {"show": False}, "axisLabel": {"color": "#6B7079"}, "nameTextStyle": {"color": "#8B9099"}},
            "yAxis": {"type": "value", "name": "↑ 法人買超", "scale": True,
                      "axisLine": {"onZero": True, "lineStyle": {"color": "#6B7079"}},
                      "splitLine": {"show": False}, "axisLabel": {"color": "#6B7079"}, "nameTextStyle": {"color": "#8B9099"}},
            "series": [{
                "type": "scatter",
                "data": data,
                "label": {"show": True, "position": "top", "formatter": "{b}", "color": "#C9CDD2", "fontSize": 11},
                "markLine": {"silent": True, "symbol": "none",
                             "lineStyle": {"color": "rgba(255,255,255,0.18)", "type": "dashed"},
                             "data": [{"xAxis": 0}, {"yAxis": 0}]},
            }],
        }).style("height:480px;width:100%;")

        def _click(e):
            idx = getattr(e, "data_index", None)
            if idx is None and getattr(e, "args", None):
                idx = e.args.get("dataIndex")
            if idx is not None and 0 <= idx < len(data):
                on_open_theme(data[idx]["theme_id"])
        chart.on_point_click(_click)
