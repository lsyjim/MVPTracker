import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from gen_seed import parse_key, build_concept_map


def test_parse_key():
    assert parse_key("01_ai_server") == ("01_ai_server", None)
    assert parse_key("18_robot/servo_motor") == ("18_robot", "servo_motor")


def test_build_structure():
    rows = [
        {"theme_key": "01_ai_server", "theme": "AI/伺服器", "sub_theme": "", "code": "2382", "name": "廣達"},
        {"theme_key": "18_robot/servo_motor", "theme": "機器人", "sub_theme": "伺服馬達/驅動器", "code": "2308", "name": "台達電"},
    ]
    cm = build_concept_map(rows)
    keys = {t["key"] for t in cm["themes"]}
    assert keys == {"01_ai_server", "18_robot"}
    robot = next(t for t in cm["themes"] if t["key"] == "18_robot")
    assert robot["sub_themes"][0]["key"] == "servo_motor"
    assert robot["sub_themes"][0]["constituents"][0]["code"] == "2308"
    ai = next(t for t in cm["themes"] if t["key"] == "01_ai_server")
    assert ai["sub_themes"] == [] and ai["constituents"][0]["code"] == "2382"
