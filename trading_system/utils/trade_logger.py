"""Persistencia en SQLite (trades_history.db): operaciones y árbol genealógico.

Cada operación guarda el voto exacto de cada agente al entrar. Cada versión de
cada agente guarda sus instrucciones y reglas: al reiniciar se retoma la última.
"""
import json
import time

import config
from utils.db import q

SIGN = {"LONG": 1, "SHORT": -1}
_T = ("id opened_ts closed_ts symbol side qty entry sl tp exit pnl fees slippage "
      "exit_reason score mode votes meta").split()
_A = ("id agent version name status born_ts died_ts instructions rules "
      "death_kind death_cause post_mortem trade_id parent_id").split()
_ready = False


def _select(cols: list[str], table: str, tail: str, params: tuple, json_cols: tuple) -> list[dict]:
    global _ready
    if not _ready:
        q("""CREATE TABLE IF NOT EXISTS trades(
            id INTEGER PRIMARY KEY AUTOINCREMENT, opened_ts REAL, closed_ts REAL,
            symbol TEXT, side TEXT, qty REAL, entry REAL, sl REAL, tp REAL, exit REAL,
            pnl REAL, fees REAL DEFAULT 0, slippage REAL DEFAULT 0, exit_reason TEXT,
            score REAL, mode TEXT, votes TEXT, meta TEXT)""")
        q("""CREATE TABLE IF NOT EXISTS agent_versions(
            id INTEGER PRIMARY KEY AUTOINCREMENT, agent TEXT, version INTEGER, name TEXT,
            status TEXT, born_ts REAL, died_ts REAL, instructions TEXT, rules TEXT,
            death_kind TEXT, death_cause TEXT, post_mortem TEXT, trade_id INTEGER,
            parent_id INTEGER, UNIQUE(agent, version))""")
        _ready = True
    out = []
    for r in q(f"SELECT {','.join(cols)} FROM {table} {tail}", params):
        d = dict(zip(cols, r))
        for c in json_cols:
            d[c] = json.loads(d[c] or ("[]" if c == "rules" else "{}"))
        out.append(d)
    return out


# --- Operaciones ------------------------------------------------------------
def _trades(tail: str, params: tuple = ()) -> list[dict]:
    return _select(_T, "trades", tail, params, ("votes", "meta"))


def log_open(symbol, side, qty, entry, sl, tp, score, votes: dict, meta: dict | None = None,
             fee: float = 0.0, slippage: float = 0.0) -> None:
    _trades("LIMIT 0")
    q("INSERT INTO trades(opened_ts,symbol,side,qty,entry,sl,tp,score,mode,votes,meta,fees,slippage) "
      "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
      (time.time(), symbol, side, qty, entry, sl, tp, score, config.EXECUTION_MODE,
       json.dumps(votes), json.dumps(meta or {}), fee, slippage))


def log_close(trade: dict, exit_price: float, reason: str, fee: float = 0.0, slippage: float = 0.0) -> dict:
    """Cierra la operación. El PnL que se guarda es NETO: entrada y salida ya
    incluyen el deslizamiento, y se restan las comisiones de ambos lados."""
    fees = (trade["fees"] or 0) + fee
    slippage = (trade["slippage"] or 0) + slippage
    pnl = (exit_price - trade["entry"]) * trade["qty"] * SIGN[trade["side"]] - fees
    now = time.time()
    q("UPDATE trades SET closed_ts=?, exit=?, pnl=?, fees=?, slippage=?, exit_reason=? WHERE id=?",
      (now, exit_price, pnl, fees, slippage, reason, trade["id"]))
    return trade | {"closed_ts": now, "exit": exit_price, "pnl": pnl, "fees": fees,
                    "slippage": slippage, "exit_reason": reason}


def open_trades() -> list[dict]:
    return _trades("WHERE closed_ts IS NULL AND mode=? ORDER BY id", (config.EXECUTION_MODE,))


def closed_trades(since: float = 0.0, mode: str | None = None) -> list[dict]:
    """Operaciones cerradas desde `since`. mode=None devuelve todos los modos."""
    if mode:
        return _trades("WHERE closed_ts>=? AND mode=? ORDER BY id", (since, mode))
    return _trades("WHERE closed_ts>=? ORDER BY id", (since,))


def realized_pnl() -> float:
    _trades("LIMIT 0")  # el jefe lo pide en cada ciclo: se suma en SQL, sin cargar las operaciones
    return q("SELECT COALESCE(SUM(pnl),0) FROM trades WHERE closed_ts IS NOT NULL AND mode=?",
             (config.EXECUTION_MODE,))[0][0]


def last_close_ts(symbol: str) -> float:
    _trades("LIMIT 0")
    return q("SELECT COALESCE(MAX(closed_ts),0) FROM trades WHERE symbol=? AND mode=?",
             (symbol, config.EXECUTION_MODE))[0][0]


# --- Árbol genealógico de agentes -------------------------------------------
def _versions(tail: str, params: tuple = ()) -> list[dict]:
    return _select(_A, "agent_versions", tail, params, ("rules",))


def genealogy() -> list[dict]:
    return _versions("ORDER BY id")


def active_version(agent: str) -> dict | None:
    """La versión más alta alcanzada por el agente (viva o no)."""
    rows = _versions("WHERE agent=? ORDER BY version DESC LIMIT 1", (agent,))
    return rows[0] if rows else None


def insert_version(agent: str, version: int, name: str, instructions: str, rules: list,
                   parent_id: int | None) -> dict:
    _versions("LIMIT 0")
    q("INSERT INTO agent_versions(agent,version,name,status,born_ts,instructions,rules,parent_id) "
      "VALUES(?,?,?,?,?,?,?,?)",
      (agent, version, name, "alive", time.time(), instructions, json.dumps(rules, ensure_ascii=False), parent_id))
    return active_version(agent)


def mark_dead(row_id: int, kind: str, cause: str, trade_id: int | None = None) -> None:
    q("UPDATE agent_versions SET status='dead', died_ts=?, death_kind=?, death_cause=?, trade_id=? WHERE id=?",
      (time.time(), kind, cause, trade_id, row_id))


def set_post_mortem(row_id: int, text: str) -> None:
    q("UPDATE agent_versions SET post_mortem=? WHERE id=?", (text, row_id))
