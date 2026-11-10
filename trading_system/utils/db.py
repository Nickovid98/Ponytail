"""SQLite (trades_history.db): conexión compartida y estado motor <-> dashboard.

Modo WAL: el motor escribe y el dashboard lee a la vez sin bloquearse.
"""
import json
import sqlite3
import threading

import config

_conn: sqlite3.Connection | None = None
_lock = threading.Lock()


def q(sql: str, params: tuple = ()) -> list[tuple]:
    """Ejecuta una sentencia y devuelve las filas. Seguro entre hilos (Streamlit)."""
    global _conn
    with _lock:
        if _conn is None:
            config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(config.DB_PATH, timeout=10, check_same_thread=False,
                                    isolation_level=None)
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT)")
        return _conn.execute(sql, params).fetchall()


def get_state(key: str, default=None):
    rows = q("SELECT value FROM kv WHERE key=?", (key,))
    return json.loads(rows[0][0]) if rows else default


def set_state(key: str, value) -> None:
    q("INSERT OR REPLACE INTO kv VALUES(?,?)", (key, json.dumps(value)))


def del_state(key: str) -> None:
    q("DELETE FROM kv WHERE key=?", (key,))
