# concept/store.py — 題材/子題材/成分股 CRUD + concept_map.json import/export
import datetime


def import_concept_map(con, cm: dict):
    for ti, t in enumerate(cm.get("themes", [])):
        con.execute("INSERT OR IGNORE INTO themes(key,name,sort,is_custom) VALUES(?,?,?,?)",
                    (t["key"], t["name"], ti, 1 if t.get("is_custom") else 0))
        theme_id = con.execute("SELECT id FROM themes WHERE key=?", (t["key"],)).fetchone()["id"]
        for ci, c in enumerate(t.get("constituents", [])):
            con.execute("INSERT INTO constituents(theme_id,sub_theme_id,code,name,in_master,sort) VALUES(?,?,?,?,1,?)",
                        (theme_id, None, c["code"], c["name"], ci))
        for si, s in enumerate(t.get("sub_themes", [])):
            con.execute("INSERT INTO sub_themes(theme_id,key,name,sort) VALUES(?,?,?,?)",
                        (theme_id, s["key"], s["name"], si))
            sub_id = con.execute("SELECT id FROM sub_themes WHERE theme_id=? AND key=?",
                                 (theme_id, s["key"])).fetchone()["id"]
            for ci, c in enumerate(s.get("constituents", [])):
                con.execute("INSERT INTO constituents(theme_id,sub_theme_id,code,name,in_master,sort) VALUES(?,?,?,?,1,?)",
                            (theme_id, sub_id, c["code"], c["name"], ci))
    con.commit()


def list_themes(con):
    return [dict(r) for r in con.execute("SELECT * FROM themes ORDER BY sort, id")]


def get_theme_by_key(con, key):
    r = con.execute("SELECT * FROM themes WHERE key=?", (key,)).fetchone()
    return dict(r) if r else None


def get_theme(con, theme_id):
    r = con.execute("SELECT * FROM themes WHERE id=?", (theme_id,)).fetchone()
    return dict(r) if r else None


def list_sub_themes(con, theme_id):
    return [dict(r) for r in con.execute("SELECT * FROM sub_themes WHERE theme_id=? ORDER BY sort, id", (theme_id,))]


def list_constituents(con, theme_id, sub_theme_id):
    if sub_theme_id is None:
        rows = con.execute("SELECT * FROM constituents WHERE theme_id=? AND sub_theme_id IS NULL ORDER BY sort, id", (theme_id,))
    else:
        rows = con.execute("SELECT * FROM constituents WHERE theme_id=? AND sub_theme_id=? ORDER BY sort, id", (theme_id, sub_theme_id))
    return [dict(r) for r in rows]


def add_theme(con, name, key=None, is_custom=True):
    key = key or ("custom_" + datetime.datetime.now().strftime("%Y%m%d%H%M%S%f"))
    sort = con.execute("SELECT COALESCE(MAX(sort),0)+1 FROM themes").fetchone()[0]
    con.execute("INSERT INTO themes(key,name,sort,is_custom) VALUES(?,?,?,?)", (key, name, sort, 1 if is_custom else 0))
    con.commit()
    return con.execute("SELECT id FROM themes WHERE key=?", (key,)).fetchone()["id"]


def add_sub_theme(con, theme_id, name, key=None):
    key = key or ("sub_" + datetime.datetime.now().strftime("%Y%m%d%H%M%S%f"))
    sort = con.execute("SELECT COALESCE(MAX(sort),0)+1 FROM sub_themes WHERE theme_id=?", (theme_id,)).fetchone()[0]
    con.execute("INSERT INTO sub_themes(theme_id,key,name,sort) VALUES(?,?,?,?)", (theme_id, key, name, sort))
    con.commit()
    return con.execute("SELECT id FROM sub_themes WHERE theme_id=? AND key=?", (theme_id, key)).fetchone()["id"]


def add_constituent(con, theme_id, code, name, sub_theme_id=None, in_master=1):
    sort = con.execute("SELECT COALESCE(MAX(sort),0)+1 FROM constituents WHERE theme_id=?", (theme_id,)).fetchone()[0]
    con.execute("INSERT INTO constituents(theme_id,sub_theme_id,code,name,in_master,sort) VALUES(?,?,?,?,?,?)",
                (theme_id, sub_theme_id, code, name, in_master, sort))
    con.commit()


def remove_constituent(con, constituent_id):
    con.execute("DELETE FROM constituents WHERE id=?", (constituent_id,)); con.commit()


def export_concept_map(con):
    out = {"version": 1, "exported_at": datetime.date.today().isoformat(), "themes": []}
    for t in list_themes(con):
        td = {"key": t["key"], "name": t["name"], "is_custom": bool(t["is_custom"]),
              "sub_themes": [], "constituents": [
                  {"code": c["code"], "name": c["name"]} for c in list_constituents(con, t["id"], None)]}
        for s in list_sub_themes(con, t["id"]):
            td["sub_themes"].append({"key": s["key"], "name": s["name"],
                "constituents": [{"code": c["code"], "name": c["name"]} for c in list_constituents(con, t["id"], s["id"])]})
        out["themes"].append(td)
    return out
