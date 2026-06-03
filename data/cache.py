# data/cache.py — scan_cache 讀寫
import json, datetime


def get(con, code, kind):
    r = con.execute("SELECT payload_json, updated_at FROM scan_cache WHERE code=? AND kind=?", (code, kind)).fetchone()
    return (json.loads(r["payload_json"]), r["updated_at"]) if r else (None, None)


def put(con, code, kind, payload):
    con.execute("INSERT OR REPLACE INTO scan_cache(code,kind,payload_json,updated_at) VALUES(?,?,?,?)",
                (code, kind, json.dumps(payload, ensure_ascii=False), datetime.datetime.now().isoformat()))
    con.commit()


def is_today(updated_at) -> bool:
    if not updated_at:
        return False
    return updated_at[:10] == datetime.date.today().isoformat()
