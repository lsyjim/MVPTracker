# ui/theme.py — 色彩 token（取自 mockups/MVPTracker_mockup.html）+ 分級 + 全域 CSS
from nicegui import ui

# 取自 mockup :root
BG = "#0E1116"; BAR = "#131820"; RAIL = "#10151C"; CARD = "#1A1F27"; ELEV = "#1C222B"
LINE = "rgba(255,255,255,0.07)"; TEXT = "#E6E8EB"; T2 = "#9AA0A8"; T3 = "#6B7079"
UP = "#F0696A"; DOWN = "#4CB782"; ACCENT = "#EF9F27"; INST = "#E8C45C"; BLUE = "#5FA8E0"
PAGE_BG = "#05070A"
# 訊號 badge 配色（明細列）
BADGE = {"grade_A": ("#B23A36", "#FFE9E8"), "grade_B": ("#2F5C7A", "#D6ECFB"),
         "grade_C": ("#2A2F37", "#9AA0A8"), "grade_sell": ("#327A3C", "#E6F4E8")}


def momentum_color(m: float):
    """5 段動能填色（背景, 前景），取自 mockup momColor。"""
    if m >= 6:
        return ("#B23A36", "#FFE9E8")
    if m >= 3:
        return ("#C5524C", "#FFECEA")
    if m >= 0:
        return ("#7E4A48", "#F6DEDC")
    if m > -3:
        return ("#46714C", "#E2F1E4")
    return ("#327A3C", "#E6F4E8")


def grade_tag(signal_text):
    """純文字分級（移植自 StockGOGOV2/theme.py）。
    調整：明確等級標記（A級/B級/C級）最權威，優先於關鍵字啟發，
    避免如「C 級觀察，列入追蹤」因含「追蹤」被誤判為 B。"""
    if not signal_text:
        return None
    s = str(signal_text)
    # 1) 明確等級標記優先（最權威）
    if ("C級" in s) or ("C 級" in s):
        return "grade_C"
    if ("A級" in s) or ("A 級" in s):
        return "grade_A"
    if ("B級" in s) or ("B 級" in s):
        return "grade_B"
    # 2) 賣出語意
    if any(k in s for k in ("賣出", "避開", "減碼", "出場", "暫緩", "不建議")):
        return "grade_sell"
    # 3) 關鍵字啟發
    if ("主攻" in s) or ("強烈建議買進" in s) or ("強力買進" in s):
        return "grade_A"
    if ("追蹤" in s) or ("建議買進" in s):
        return "grade_B"
    if ("觀察" in s) or ("等待" in s) or ("觀望" in s):
        return "grade_C"
    return None


def apply_global():
    """注入全域 CSS 變數與字體（依 mockup）。在 main.py 啟動時呼叫一次。"""
    ui.add_head_html(f"""
    <style>
      :root {{
        --bg:{BG}; --bar:{BAR}; --rail:{RAIL}; --card:{CARD}; --elev:{ELEV};
        --line:{LINE}; --text:{TEXT}; --t2:{T2}; --t3:{T3};
        --up:{UP}; --down:{DOWN}; --accent:{ACCENT}; --inst:{INST}; --blue:{BLUE};
        --mono:"SF Mono","Menlo","JetBrains Mono","Consolas",monospace;
        --sans:"PingFang TC","Microsoft JhengHei UI","Noto Sans TC","Segoe UI",system-ui,sans-serif;
      }}
      body {{ background:{PAGE_BG}; color:var(--text); font-family:var(--sans); }}
      .mono {{ font-family:var(--mono); }}
      .up {{ color:var(--up); }} .down {{ color:var(--down); }}
      .muted {{ color:var(--t2); }} .gold {{ color:var(--inst); }}
    </style>
    """)
