import sys, os, sqlite3
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from concept.db import connect, init_schema


def test_schema_tables(tmp_path):
    con = connect(str(tmp_path / "t.db"))
    init_schema(con)
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"themes", "sub_themes", "constituents", "watchlist", "scan_cache"} <= names


def test_scan_cache_pk(tmp_path):
    con = connect(str(tmp_path / "t.db")); init_schema(con)
    con.execute("INSERT INTO scan_cache(code,kind,payload_json,updated_at) VALUES('2330','grade','{}','t')")
    con.execute("INSERT OR REPLACE INTO scan_cache(code,kind,payload_json,updated_at) VALUES('2330','grade','{\"a\":1}','t2')")
    con.commit()
    rows = con.execute("SELECT payload_json FROM scan_cache WHERE code='2330' AND kind='grade'").fetchall()
    assert len(rows) == 1 and rows[0][0] == '{"a":1}'
