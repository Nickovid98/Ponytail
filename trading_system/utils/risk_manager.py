"""Riesgo y costes de ejecución. Funciones puras: sin red, sin estado, sin LLM.

Las comisiones y el deslizamiento se descuentan del PnL antes de que nadie lo
evalúe: ni el optimizador ni el gestor de evolución ven nunca un PnL bruto.
"""
import datetime as dt
import math
from zoneinfo import ZoneInfo

import pandas as pd

import config

PAPER_TRADING = config.PAPER_TRADING
CHICAGO = ZoneInfo("America/Chicago")
NEW_YORK = ZoneInfo("America/New_York")
# Calendario de la NYSE copiado de la skill trader-alerts (nyse.com/markets/hours-calendars, leído el 13-10-2026).
NYSE_HOLIDAYS = {"2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03",
                 "2026-09-07", "2026-11-26", "2026-12-25", "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26",
                 "2027-05-31", "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24"}
NYSE_EARLY_CLOSE = {"2026-11-27", "2026-12-24", "2027-11-26"}  # cierra a las 13:00


def is_stock(symbol: str) -> bool:
    """Acción de EE. UU.: un símbolo sin "/" fuera de Topstep (allí MNQ o MES son contratos de futuros)."""
    return config.BROKER == "ccxt" and bool(symbol) and "/" not in symbol


def trading_day(now: dt.datetime | None = None) -> str:
    """Día operativo. Cripto: fecha UTC. Topstep: empieza a las 17:00 de Chicago del día anterior."""
    now = now or dt.datetime.now(dt.timezone.utc)
    if config.BROKER != "topstepx":
        return now.astimezone(dt.timezone.utc).date().isoformat()
    return (now.astimezone(CHICAGO) + dt.timedelta(hours=7)).date().isoformat()


def day_open(now: dt.datetime | None = None) -> dt.datetime:
    """Inicio del día operativo de futuros en curso: las 17:00 de Chicago más recientes."""
    t = (now or dt.datetime.now(dt.timezone.utc)).astimezone(CHICAGO)
    d = t.replace(hour=17, minute=0, second=0, microsecond=0)
    return d if t >= d else d - dt.timedelta(days=1)


def session(now: dt.datetime | None = None) -> tuple[bool, bool]:
    """(se permiten entradas nuevas, hay que cerrarlo todo). Fuera de Topstep siempre (True, False)."""
    if config.BROKER != "topstepx":
        return True, False
    t = (now or dt.datetime.now(dt.timezone.utc)).astimezone(CHICAGO)
    hm, wd = t.strftime("%H:%M"), t.weekday()
    if wd == 5 or (wd == 4 and hm >= "17:00") or (wd == 6 and hm < "17:00"):
        return False, False  # fin de semana: mercado cerrado
    return hm >= "17:00" or hm < config.PROP_LAST_ENTRY_CT, config.PROP_FLAT_BY_CT <= hm < "17:00"


def nyse_hours(t: dt.datetime) -> tuple[dt.datetime, dt.datetime] | None:
    """Apertura y cierre de la sesión regular del día de `t` (hora de Nueva York), o None si no abre."""
    # ponytail: festivos de 2026 y 2027. Techo: desde 2028 solo descuenta los fines de semana; añadir el año nuevo.
    day = t.date().isoformat()
    if t.weekday() >= 5 or day in NYSE_HOLIDAYS:
        return None
    return (t.replace(hour=9, minute=30, second=0, microsecond=0),
            t.replace(hour=13 if day in NYSE_EARLY_CLOSE else 16, minute=0, second=0, microsecond=0))


def nyse_open_since(now: dt.datetime | None = None) -> float | None:
    """Si la bolsa de Nueva York está abierta, el momento (epoch) en que abrió hoy; si está cerrada, None."""
    t = (now or dt.datetime.now(dt.timezone.utc)).astimezone(NEW_YORK)
    h = nyse_hours(t)
    return h[0].timestamp() if h and h[0] <= t < h[1] else None


def nyse_session(now: dt.datetime | None = None) -> tuple[bool, bool]:
    """Acciones: (se permiten entradas nuevas, toca cerrar las abiertas). Sesión regular de Nueva York, 09:30-16:00
    (13:00 los días de cierre temprano): se entra desde que cierra la primera vela de 1 h hasta media hora antes del
    cierre, y todo se cierra en los últimos 5 minutos. Con la bolsa cerrada no se puede operar: lo que quedara
    abierto (el motor estaba apagado al cierre) se cierra en la apertura siguiente, ver BossAgent.tick."""
    t = (now or dt.datetime.now(dt.timezone.utc)).astimezone(NEW_YORK)
    h = nyse_hours(t)
    if not h:
        return False, False
    opens, closes = h
    m = dt.timedelta(minutes=1)
    entries = opens + config.STOCK_OPEN_WAIT_MIN * m <= t < closes - config.STOCK_LAST_ENTRY_MIN * m
    return entries, closes - config.STOCK_FLAT_MIN * m <= t < closes


def atr(df: pd.DataFrame, n: int = 14) -> float:
    """Average True Range con suavizado de Wilder."""
    prev = df.close.shift()
    tr = pd.concat([df.high - df.low, (df.high - prev).abs(), (df.low - prev).abs()], axis=1).max(axis=1)
    return float(tr.ewm(alpha=1 / n, adjust=False).mean().iloc[-1])


def costs(symbol: str | None = None) -> tuple[float, float]:
    """(comisión, deslizamiento) por lado en % del nocional: los de las acciones o los de cripto."""
    if symbol and is_stock(symbol):
        return config.STOCK_FEE_PCT, config.STOCK_SLIPPAGE_PCT
    return config.FEE_PCT, config.SLIPPAGE_PCT


def fee(price: float, qty: float, symbol: str | None = None) -> float:
    """Comisión de una ejecución (un lado)."""
    return price * qty * costs(symbol)[0] / 100


def slip(price: float, buying: bool, symbol: str | None = None) -> float:
    """Precio de ejecución proyectado de una orden a mercado: siempre en contra."""
    # ponytail: deslizamiento fijo en % del precio. Techo: no depende del tamaño
    # de la orden ni de la liquidez del momento. Mejora: recorrer el libro de
    # órdenes (fetch_order_book) con la cantidad real.
    return price * (1 + (1 if buying else -1) * costs(symbol)[1] / 100)


def round_trip_cost_pct(symbol: str | None = None) -> float:
    """Coste proyectado de entrar y salir por el stop, en % del nocional."""
    return 2 * sum(costs(symbol))


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
               structure: float | None = None, symbol: str | None = None) -> dict | None:
    """Plan completo de la operación, o None si no se puede operar con seguridad."""
    if side not in ("LONG", "SHORT") or not all(
            isinstance(x, (int, float)) and math.isfinite(x) and x > 0 for x in (entry, atr_v, equity)):
        return None
    dist = stop_distance(side, entry, atr_v, structure)
    cost = entry * round_trip_cost_pct(symbol) / 100  # comisiones + deslizamiento por unidad
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
    if config.TOTAL_LOSS_LIMIT_USD > 0 and start - total >= config.TOTAL_LOSS_LIMIT_USD:
        return "total", f"pérdida total {start - total:.2f} >= límite {config.TOTAL_LOSS_LIMIT_USD:.0f} {config.QUOTE}"
    if config.PROP_DAILY_LOSS_LIMIT_USD > 0:
        # Prop firm: el límite es en dólares y el cierre salta ANTES de tocarlo, para que el
        # deslizamiento del cierre no lo rebase. Si la cuenta tiene un suelo (Max Loss Limit),
        # manda lo que quede hasta él cuando sea menos que el límite diario.
        limit = config.PROP_DAILY_LOSS_LIMIT_USD
        if config.PROP_ACCOUNT_FLOOR_USD > 0:
            limit = min(limit, day_start - config.PROP_ACCOUNT_FLOOR_USD)
        if day_start - total >= limit * config.PROP_KILL_AT_PCT / 100:
            return "daily", (f"kill switch: pérdida del día {day_start - total:.2f} >= {config.PROP_KILL_AT_PCT:g}% "
                             f"del límite de {limit:.2f} USD")
        return None
    if day_start - total >= day_start * config.DAILY_LOSS_LIMIT_PCT / 100:
        return "daily", f"pérdida del día {day_start - total:.2f} >= {config.DAILY_LOSS_LIMIT_PCT}% del capital"
    return None
