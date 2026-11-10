"""Registro de operaciones. Cada fila guarda el voto exacto de cada agente
en el momento de la entrada: es el material del que aprende optimizer.py."""
import json
import time

import config
from utils.cache import q

SIGN = {"LONG": 1, "SHORT": -1}
_COLS = ("id opened_ts closed_ts symbol side qty entry sl tp exit pnl fees "
         "exit_reason score mode votes meta").split()
_ready = False


def _init() -> None:
    global _ready
    if not _ready:
        q("""CREATE TABLE IF NOT EXISTS trades(
            id INTEGER PRIMARY KEY AUTOINCREMENT, opened_ts REAL, closed_ts REAL,
            symbol TEXT, side TEXT, qty REAL, entry REAL, sl REAL, tp REAL,
            exit REAL, pnl REAL, fees REAL DEFAULT 0, exit_reason TEXT, score REAL,
            mode TEXT, votes TEXT, meta TEXT)""")
        _ready = True


def _rows(where: str, params: tuple = ()) -> list[dict]:
    _init()
    out = []
    for r in q(f"SELECT {','.join(_COLS)} FROM trades WHERE {where} ORDER BY id", params):
        d = dict(zip(_COLS, r))
        d["votes"] = json.loads(d["votes"] or "{}")
        d["meta"] = json.loads(d["meta"] or "{}")
        out.append(d)
    return out


def log_open(symbol, side, qty, entry, sl, tp, score, votes: dict, meta: dict | None = None,
             fee: float = 0.0) -> None:
    _init()
    q("INSERT INTO trades(opened_ts,symbol,side,qty,entry,sl,tp,score,mode,votes,meta,fees) "
      "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
      (time.time(), symbol, side, qty, entry, sl, tp, score, config.EXECUTION_MODE,
       json.dumps(votes), json.dumps(meta or {}), fee))


def log_close(trade: dict, exit_price: float, reason: str, fee: float = 0.0) -> float:
    """Cierra la operación y devuelve el PnL neto de comisiones."""
    fees = (trade["fees"] or 0) + fee
    pnl = (exit_price - trade["entry"]) * trade["qty"] * SIGN[trade["side"]] - fees
    q("UPDATE trades SET closed_ts=?, exit=?, pnl=?, fees=?, exit_reason=? WHERE id=?",
      (time.time(), exit_price, pnl, fees, reason, trade["id"]))
    return pnl


def open_trades() -> list[dict]:
    return _rows("closed_ts IS NULL AND mode=?", (config.EXECUTION_MODE,))


def closed_trades(since: float = 0.0, mode: str | None = None) -> list[dict]:
    """Operaciones cerradas desde `since`. mode=None devuelve todos los modos."""
    if mode:
        return _rows("closed_ts>=? AND mode=?", (since, mode))
    return _rows("closed_ts>=?", (since,))


def realized_pnl() -> float:
    _init()
    return q("SELECT COALESCE(SUM(pnl),0) FROM trades WHERE closed_ts IS NOT NULL AND mode=?",
             (config.EXECUTION_MODE,))[0][0]


def last_close_ts(symbol: str) -> float:
    _init()
    return q("SELECT COALESCE(MAX(closed_ts),0) FROM trades WHERE symbol=? AND mode=?",
             (symbol, config.EXECUTION_MODE))[0][0]
