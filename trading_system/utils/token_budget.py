"""Mensajes compactos entre agentes y auditoría del gasto de tokens.

Dos contadores distintos:
  - payload: tokens ESTIMADOS de los JSON que los sub-agentes pasan al jefe.
  - llm:     tokens REALES facturados por Claude (response.usage).
El presupuesto diario se aplica a los reales: al agotarse, Claude queda
desactivado hasta el siguiente día UTC y el sistema sigue con sus respaldos.
"""
import datetime as dt
import json
import math

import config
from agents import Signal
from utils import db

_DIR = {"LONG": "L", "SHORT": "S", "NEUTRAL": "N"}
_RID = {v: k for k, v in _DIR.items()}


def estimate(text: str) -> int:
    # ponytail: 1 token ~ 3 bytes, a propósito pesimista para JSON con números.
    # Techo: es una estimación. El conteo exacto es client.messages.count_tokens,
    # pero cuesta una llamada de red por mensaje.
    return math.ceil(len(text.encode("utf-8")) / 3)


def _r(x: float) -> float:
    return float(f"{x:.6g}")


def pack(sig: Signal) -> str:
    """Signal -> JSON minificado que nunca supera PAYLOAD_TOKEN_LIMIT."""
    d = {"a": sig.agent, "s": sig.symbol, "d": _DIR[sig.direction], "c": sig.confidence,
         "t": round(sig.ts), "n": sig.version}
    if sig.support is not None:
        d["sp"] = _r(sig.support)
    if sig.resistance is not None:
        d["rs"] = _r(sig.resistance)
    if sig.veto:
        d["v"] = 1
    if sig.features:
        d["f"] = {k: _r(v) for k, v in sig.features.items()}
    d["r"] = sig.rationale
    while True:
        s = json.dumps(d, separators=(",", ":"), ensure_ascii=False)
        if estimate(s) <= config.PAYLOAD_TOKEN_LIMIT:
            return s
        if d["r"]:  # primero se recorta la justificación...
            d["r"] = d["r"][:-12]
        elif "f" in d:  # ...y si no basta, sobran las features
            del d["f"]
        else:
            return s


def unpack(payload: str) -> Signal:
    d = json.loads(payload)
    return Signal(agent=d["a"], symbol=d["s"], direction=_RID[d["d"]], confidence=d["c"], ts=d["t"],
                  version=d.get("n", ""), support=d.get("sp"), resistance=d.get("rs"),
                  veto=bool(d.get("v")), features=d.get("f", {}), rationale=d.get("r", ""))


def _key() -> str:
    return f"tokens:{dt.datetime.now(dt.timezone.utc).date()}"


def today() -> dict:
    return db.get_state(_key(), {"llm": 0, "llm_calls": 0, "payload": 0, "cycles": 0,
                                 "last_cycle": 0, "peak_cycle": 0, "by_kind": {}})


def record_llm(kind: str, usage) -> None:
    """Anota el consumo real de una respuesta de Claude (response.usage)."""
    n = int(getattr(usage, "input_tokens", 0) or 0) + int(getattr(usage, "output_tokens", 0) or 0)
    t = today()
    t["llm"] += n
    t["llm_calls"] += 1
    t["by_kind"][kind] = t["by_kind"].get(kind, 0) + n
    db.set_state(_key(), t)


def record_cycle(payload_tokens: int) -> None:
    """Anota los tokens de los mensajes que el jefe leyó en un ciclo."""
    t = today()
    t["payload"] += payload_tokens
    t["cycles"] += 1
    t["last_cycle"] = payload_tokens
    t["peak_cycle"] = max(t["peak_cycle"], payload_tokens)
    db.set_state(_key(), t)


def llm_allowed() -> bool:
    return today()["llm"] < config.DAILY_TOKEN_BUDGET
