import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "analysis"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scanner.picks import select, tags


def mk(**kw):
    base = dict(_ok=True, grade="grade_A", rs=85, inst=5000, cons_buy=3, vol_ratio=1.5,
                bias=8.0, diverge=False, d5_pct=5.0, today_pct=2.0, price=100, code="0001", name="X", theme="T")
    base.update(kw)
    return base


def test_full_pick():
    full, near = select([mk()])
    assert len(full) == 1 and near == []


def test_near_miss_volume():
    full, near = select([mk(vol_ratio=1.0)])     # 只差量能
    assert full == [] and len(near) == 1 and "量能" in near[0][1]


def test_near_miss_grade():
    full, near = select([mk(grade="grade_C")])
    assert full == [] and near[0][1] == "評級未達 A/B"


def test_diverge_blocks_bias_factor():
    full, near = select([mk(diverge=True)])      # 背離 → 乖離/背離因子不過
    assert full == [] and "背離" in near[0][1]


def test_inst_needs_streak_and_positive():
    full, near = select([mk(cons_buy=1)])        # 連買天數不足
    assert full == [] and near[0][1] == "法人買超不足"


def test_two_missing_not_candidate():
    full, near = select([mk(rs=50, vol_ratio=1.0)])  # 缺 2 項 → 既非全中也非候選
    assert full == [] and near == []


def test_暫缺_excluded():
    full, near = select([mk(_ok=False)])
    assert full == [] and near == []


def test_tags():
    t = tags(mk())
    assert any("A主攻" in x for x in t) and any(x.startswith("RS") for x in t)
