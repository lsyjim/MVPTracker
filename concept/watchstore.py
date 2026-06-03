# concept/watchstore.py — 極簡自選股
import datetime


def add(con, code, name):
    exists = con.execute("SELECT id FROM watchlist WHERE code=?", (code,)).fetchone()
    if exists:
        return exists["id"]
    con.execute("INSERT INTO watchlist(code,name,added_at) VALUES(?,?,?)",
                (code, name, datetime.datetime.now().isoformat()))
    con.commit()
    return con.execute("SELECT id FROM watchlist WHERE code=?", (code,)).fetchone()["id"]


def list_all(con):
    return [dict(r) for r in con.execute("SELECT * FROM watchlist ORDER BY added_at DESC")]


def remove(con, wid):
    con.execute("DELETE FROM watchlist WHERE id=?", (wid,))
    con.commit()
