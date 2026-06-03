import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ui.theme import grade_tag


def test_grade_tag():
    assert grade_tag("A級主攻") == "grade_A"
    assert grade_tag("建議買進 追蹤") == "grade_B"
    assert grade_tag("觀察") == "grade_C"
    assert grade_tag("暫緩買進") == "grade_sell"
    assert grade_tag("") is None
