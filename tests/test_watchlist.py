import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from concept.db import connect, init_schema
from concept import watchstore


def test_add_list_remove(tmp_path):
    con = connect(str(tmp_path / "t.db")); init_schema(con)
    watchstore.add(con, "2330", "台積電")
    watchstore.add(con, "2330", "台積電")   # 不重複
    items = watchstore.list_all(con)
    assert len(items) == 1 and items[0]["code"] == "2330"
    watchstore.remove(con, items[0]["id"])
    assert watchstore.list_all(con) == []
