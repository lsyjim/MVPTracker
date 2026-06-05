# data/cache.py — scan_cache 讀寫（thread-safe，供平行掃描共用單一連線）
import json, datetime, threading

_lock = threading.RLock()   # 序列化 DB 存取（分析在鎖外平行跑，DB op 很短）


def get(con, code, kind):
    with _lock:
        r = con.execute("SELECT payload_json, updated_at FROM scan_cache WHERE code=? AND kind=?", (code, kind)).fetchone()
        return (json.loads(r["payload_json"]), r["updated_at"]) if r else (None, None)


def put(con, code, kind, payload):
    with _lock:
        con.execute("INSERT OR REPLACE INTO scan_cache(code,kind,payload_json,updated_at) VALUES(?,?,?,?)",
                    (code, kind, json.dumps(payload, ensure_ascii=False), datetime.datetime.now().isoformat()))
        con.commit()


def is_today(updated_at) -> bool:
    if not updated_at:
        return False
    return updated_at[:10] == datetime.date.today().isoformat()
