# scanner/theme_scanner.py
from dataclasses import dataclass


@dataclass
class ThemeMetrics:
    theme_id: int
    key: str
    name: str
    momentum_5d: float      # 5日動能 %
    inst_net: float         # 法人買超強度（-100..100，正=買超）
    count: int              # 成分股數
    up_count: int = 0
    down_count: int = 0
    strong_ratio: float = 0.0
    signal: str = ""
    diverge: bool = False


def is_diverge(m: float, inst: float) -> bool:
    return (m > 0 and inst < -20) or (m < 0 and inst > 20)


def mock_overview():
    """假資料（取自 mockup themes 陣列），步驟 5 換成 real_overview。"""
    raw = [("先進封裝", 8.4, 70, 8), ("AI/伺服器", 6.7, 55, 16), ("機器人", 5.2, 25, 20), ("散熱", 4.1, 60, 6),
           ("半導體設備", 3.3, 20, 7), ("軟體", 2.9, -15, 30), ("光通訊", 2.6, 40, 10), ("IC設計", 1.6, 10, 6),
           ("記憶體", 1.1, -45, 9), ("連接器", 0.8, 5, 4), ("晶圓代工", 0.4, -20, 3), ("電源管理", -0.4, -10, 5),
           ("PCB", -1.2, -55, 16), ("低軌衛星", -1.8, 35, 9), ("被動元件", -3.1, -70, 10)]
    out = []
    for i, (n, m, inst, c) in enumerate(raw):
        out.append(ThemeMetrics(theme_id=i + 1, key=f"mock_{i}", name=n, momentum_5d=m,
                                inst_net=inst, count=c, diverge=is_diverge(m, inst)))
    return out
