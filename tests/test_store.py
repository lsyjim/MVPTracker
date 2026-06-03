import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from concept.db import connect, init_schema
from concept import store

SEED = {"version": 1, "themes": [
    {"key": "01_ai_server", "name": "AI/伺服器", "is_custom": False, "sub_themes": [],
     "constituents": [{"code": "2382", "name": "廣達"}]},
    {"key": "18_robot", "name": "機器人", "is_custom": False, "constituents": [],
     "sub_themes": [{"key": "servo_motor", "name": "伺服馬達/驅動器",
                     "constituents": [{"code": "2308", "name": "台達電"}]}]}]}


def _con(tmp_path):
    con = connect(str(tmp_path / "t.db")); init_schema(con); return con


def test_import_seed(tmp_path):
    con = _con(tmp_path); store.import_concept_map(con, SEED)
    themes = store.list_themes(con)
    assert len(themes) == 2
    robot = store.get_theme_by_key(con, "18_robot")
    subs = store.list_sub_themes(con, robot["id"])
    assert subs[0]["name"] == "伺服馬達/驅動器"
    cons = store.list_constituents(con, robot["id"], subs[0]["id"])
    assert cons[0]["code"] == "2308"


def test_add_theme_and_constituent(tmp_path):
    con = _con(tmp_path); store.import_concept_map(con, SEED)
    tid = store.add_theme(con, name="新題材", is_custom=True)
    store.add_constituent(con, theme_id=tid, code="9999", name="測試", in_master=0)
    cons = store.list_constituents(con, tid, None)
    assert cons[0]["code"] == "9999" and cons[0]["in_master"] == 0


def test_remove_constituent(tmp_path):
    con = _con(tmp_path); store.import_concept_map(con, SEED)
    ai = store.get_theme_by_key(con, "01_ai_server")
    cons = store.list_constituents(con, ai["id"], None)
    store.remove_constituent(con, cons[0]["id"])
    assert store.list_constituents(con, ai["id"], None) == []


def test_export_roundtrip(tmp_path):
    con = _con(tmp_path); store.import_concept_map(con, SEED)
    exported = store.export_concept_map(con)
    assert {t["key"] for t in exported["themes"]} == {"01_ai_server", "18_robot"}
