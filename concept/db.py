# concept/db.py
import os, sqlite3

DEFAULT_DB = os.path.join(os.path.dirname(__file__), "..", "storage", "app.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS themes(
  id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE, name TEXT,
  sort INTEGER DEFAULT 0, is_custom INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS sub_themes(
  id INTEGER PRIMARY KEY AUTOINCREMENT, theme_id INTEGER, key TEXT, name TEXT, sort INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS constituents(
  id INTEGER PRIMARY KEY AUTOINCREMENT, theme_id INTEGER, sub_theme_id INTEGER,
  code TEXT, name TEXT, in_master INTEGER DEFAULT 1, sort INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS watchlist(
  id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT, name TEXT, added_at TEXT);
CREATE TABLE IF NOT EXISTS scan_cache(
  code TEXT, kind TEXT, payload_json TEXT, updated_at TEXT, PRIMARY KEY(code, kind));
"""


def connect(db_path: str = DEFAULT_DB) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    con = sqlite3.connect(db_path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def init_schema(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA)
    con.commit()


def is_empty(con: sqlite3.Connection) -> bool:
    return con.execute("SELECT COUNT(*) FROM themes").fetchone()[0] == 0
