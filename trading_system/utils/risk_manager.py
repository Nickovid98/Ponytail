"""Riesgo y costes de ejecución. Funciones puras: sin red, sin estado, sin LLM.

Las comisiones y el deslizamiento se descuentan del PnL antes de que nadie lo
evalúe: ni el optimizador ni el gestor de evolución ven nunca un PnL bruto.
"""
import math

import pandas as pd

import config

PAPER_TRADING = config.PAPER_TRADING


def atr(df: pd.DataFrame, n: int = 14) -> float:
    """Average True Range con suavizado de Wilder."""
    prev = df.close.shift()
    tr = pd.concat([df.high - df.low, (df.high - prev).abs(), (df.low - prev).abs()], axis=1).max(axis=1)
    return float(tr.ewm(alpha=1 / n, adjust=False).mean().iloc[-1])


def fee(price: float, qty: float) -> float:
    """Comisión de una ejecución (un lado)."""
    return price * qty * config.FEE_PCT / 100


def slip(price: float, buying: bool) -> float:
    """Precio de ejecución proyectado de una orden a mercado: siempre en contra."""
    # ponytail: deslizamiento fijo en % del precio. Techo: no depende del tamaño
    # de la orden ni de la liquidez del momento. Mejora: recorrer el libro de
    # órdenes (fetch_order_book) con la cantidad real.
    return price * (1 + (1 if buying else -1) * config.SLIPPAGE_PCT / 100)


def round_trip_cost_pct() -> float:
    """Coste proyectado de entrar y salir por el stop, en % del nocional."""
    return 2 * (config.FEE_PCT + config.SLIPPAGE_PCT)


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
    cost = entry * round_trip_cost_pct() / 100  # comisiones + deslizamiento por unidad
    # El coste cuenta como riesgo: si salta el stop, la pérdida NETA es el 1 %.
    qty = equity * config.RISK_PER_TRADE_PCT / 100 / (dist + cost)
    qty = min(qty, equity * config.MAX_LEVERAGE / entry)  # tope de apalancamiento
    if qty * entry < config.MIN_NOTIONAL:
        return None
    sign = 1 if side == "LONG" else -1
    return {
        "side": side, "entry": entry, "qty": qty,
        "sl": entry - sign * dist, "tp": entry + sign * dist * config.REWARD_RISK,
        "risk_usd": qty * (dist + cost), "cost_est": qty * cost,
        "notional": qty * entry, "leverage": qty * entry / equity,
    }


def check_limits(total: float, day_start: float, start: float) -> tuple[str, str] | None:
    """Devuelve ("total"|"daily", motivo) si se rompió un límite de pérdida.
    `total` es el capital con el PnL flotante incluido."""
    if start - total >= config.TOTAL_LOSS_LIMIT_USD:
        return "total", f"pérdida total {start - total:.2f} >= límite {config.TOTAL_LOSS_LIMIT_USD:.0f} {config.QUOTE}"
    if day_start - total >= day_start * config.DAILY_LOSS_LIMIT_PCT / 100:
        return "daily", f"pérdida del día {day_start - total:.2f} >= {config.DAILY_LOSS_LIMIT_PCT}% del capital"
    return None
