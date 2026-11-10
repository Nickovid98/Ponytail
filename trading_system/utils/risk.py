"""Cálculos de riesgo. Funciones puras: sin red, sin estado, sin LLM."""
import math

import pandas as pd

import config


def atr(df: pd.DataFrame, n: int = 14) -> float:
    """Average True Range con suavizado de Wilder."""
    prev = df.close.shift()
    tr = pd.concat([df.high - df.low, (df.high - prev).abs(), (df.low - prev).abs()], axis=1).max(axis=1)
    return float(tr.ewm(alpha=1 / n, adjust=False).mean().iloc[-1])


def stop_distance(side: str, entry: float, atr_v: float, structure: float | None = None) -> float:
    """Distancia del stop: estructura técnica si es razonable, si no ATR.
    Nunca más lejos que MAX_SL_PCT de la entrada."""
    dist = config.ATR_SL_MULT * atr_v
    if structure:
        # Stop al otro lado del nivel (soporte en largos, resistencia en cortos),
        # con 0,2 ATR de margen. Solo si queda entre 0,5 y 3 ATR: más cerca es
        # ruido, más lejos deja la operación sin relación riesgo/beneficio.
        d = (entry - structure if side == "LONG" else structure - entry) + 0.2 * atr_v
        if 0.5 * atr_v <= d <= 3 * atr_v:
            dist = d
    return min(dist, entry * config.MAX_SL_PCT / 100)


def build_plan(side: str, entry: float, atr_v: float, equity: float,
               structure: float | None = None) -> dict | None:
    """Plan completo de la operación, o None si no se puede operar con seguridad."""
    if side not in ("LONG", "SHORT") or not all(
            isinstance(x, (int, float)) and math.isfinite(x) and x > 0 for x in (entry, atr_v, equity)):
        return None
    dist = stop_distance(side, entry, atr_v, structure)
    risk_usd = equity * config.RISK_PER_TRADE_PCT / 100
    qty = risk_usd / dist  # si salta el stop se pierde risk_usd
    qty = min(qty, equity * config.MAX_LEVERAGE / entry)  # tope de apalancamiento
    if qty * entry < config.MIN_NOTIONAL:
        return None
    sign = 1 if side == "LONG" else -1
    return {
        "side": side, "entry": entry, "qty": qty,
        "sl": entry - sign * dist, "tp": entry + sign * dist * config.REWARD_RISK,
        "risk_usd": qty * dist, "notional": qty * entry, "leverage": qty * entry / equity,
    }


def check_limits(total: float, day_start: float, start: float) -> tuple[str, str] | None:
    """Devuelve ("total"|"daily", motivo) si se rompió un límite de pérdida.
    `total` es el capital con el PnL flotante incluido."""
    if start - total >= config.TOTAL_LOSS_LIMIT_USD:
        return "total", f"pérdida total {start - total:.2f} >= límite {config.TOTAL_LOSS_LIMIT_USD:.0f} {config.QUOTE}"
    if day_start - total >= day_start * config.DAILY_LOSS_LIMIT_PCT / 100:
        return "daily", f"pérdida del día {day_start - total:.2f} >= {config.DAILY_LOSS_LIMIT_PCT}% del capital"
    return None
