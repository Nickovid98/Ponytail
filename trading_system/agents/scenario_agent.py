"""Agente 6 · Escenarios: miles de futuros ficticios de cada activo, y vota solo por las 5 mejores apuestas.

Para cada activo rehace el futuro SCENARIOS veces (10.000 por defecto) con sus propias velas de 5 minutos, con el
método de la skill trader-timemachine: cada tramo es una vela pasada, escalada al precio de ahora. En acciones, una
vela de la misma hora de la sesión (±2 velas) de las sesiones completas de los últimos 60 días, hasta 5 minutos
antes del cierre; en cripto, cualquiera de las últimas ~3,5 jornadas, durante SCENARIO_HOURS horas. En cada
escenario prueba la operación que pondría el jefe en largo y en corto (stop por ATR, objetivo a REWARD_RISK veces
el riesgo, con comisiones y deslizamiento) y anota el resultado en R: -1 en el stop (si una vela toca los dos
niveles, cuenta el stop), el objetivo neto de costes, o lo que valga al acabar el tiempo. Ordena las apuestas por
su resultado medio y vota solo por las SCENARIO_TOP mejores con media positiva (el mejor lado de cada activo); a las
demás les da NEUTRAL.

No predice: los escenarios repiten cómo se movió el activo hace poco. Si el mercado cambia de carácter, se quedan
viejos al momento.
"""
import datetime as dt
import time
import zlib

import numpy as np
import pandas as pd

import config
from agents import Signal, neutral
from utils import db
from utils import risk_manager as risk

SESSION_BARS = 78  # velas de 5 min de 09:30 a 16:00
MIN_SESSIONS = 20  # con menos sesiones completas el reparto de cada hora es ruido (como en la skill)
WINDOW = 2  # velas de margen a cada lado de la misma hora del día
SEED = 19  # la de la skill: mismos datos, mismos escenarios


class ScenarioAgent:
    name = "scenarios"
    interval = config.TECH_INTERVAL

    def __init__(self):
        self._signals: dict[str, Signal] = {}
        self._errors: dict[str, Exception] = {}
        self._at = 0.0

    async def run(self, symbol, market) -> Signal:
        # Las 5 mejores salen de comparar todos los activos: se calcula todo de una vez y cada activo recoge lo suyo.
        if time.time() - self._at > self.interval / 2 or symbol not in self._signals and symbol not in self._errors:
            await self.rank(market)
        if symbol in self._errors:
            raise self._errors[symbol]  # sin datos de ese activo: pasajero, como en los demás bots
        return self._signals.get(symbol) or neutral(self.name, symbol, "sin escenarios todavía")

    async def rank(self, market) -> list[dict]:
        stats, self._errors = {}, {}
        for s in config.SYMBOLS:
            try:
                stats[s] = await self.simulate(s, market)
            except ConnectionError as e:
                self._errors[s] = e
        best = {s: max(st["sides"], key=lambda side: st["sides"][side]["er"]) for s, st in stats.items() if "sides" in st}
        order = sorted(best, key=lambda s: -stats[s]["sides"][best[s]]["er"])
        top = [s for s in order if stats[s]["sides"][best[s]]["er"] > 0][:config.SCENARIO_TOP]
        self._signals = {}
        for s, st in stats.items():
            if "sides" not in st:
                self._signals[s] = neutral(self.name, s, st["why"])
                continue
            side, r = best[s], st["sides"][best[s]]
            rank = top.index(s) + 1 if s in top else 0
            feats = {"er": round(r["er"], 4), "ptp": round(r["ptp"], 4), "psl": round(r["psl"], 4), "rank": float(rank)}
            head = f"{st['n']:,} escenarios ({st['horizon']})".replace(",", ".")
            if rank:
                self._signals[s] = Signal(
                    agent=self.name, symbol=s, direction=side, confidence=int(min(100, 50 + 100 * r["er"])),
                    features=feats, rationale=f"{head}: {side} objetivo {r['ptp']:.0%} · stop {r['psl']:.0%} · "
                                              f"media {r['er']:+.2f} R · puesto {rank} de {len(top)}")
            else:
                self._signals[s] = neutral(self.name, s, f"{head}: fuera de las {config.SCENARIO_TOP} mejores "
                                                         f"(su mejor lado, {side}, da {r['er']:+.2f} R)", features=feats)
        self._at = time.time()
        rows = [{"symbol": s, "side": best[s], "rank": top.index(s) + 1 if s in top else 0,
                 "horizon": stats[s]["horizon"], **stats[s]["sides"][best[s]]} for s in order]
        rows += [{"symbol": s, "why": st["why"]} for s, st in stats.items() if "sides" not in st]
        db.set_state("scenarios:ranking", {"ts": self._at, "n": config.SCENARIOS, "top": top, "rows": rows})
        return rows

    async def simulate(self, symbol: str, market) -> dict:
        """{"sides": {"LONG": {...}, "SHORT": {...}}, "n", "horizon"} o {"why": motivo} si no se puede simular."""
        stock = risk.is_stock(symbol)
        if stock and risk.nyse_open_since() is None:
            return {"why": "bolsa de Nueva York cerrada: los escenarios se hacen con la sesión en marcha"}
        if risk.is_fx(symbol) and risk.fx_open_since() is None:
            return {"why": "mercado de divisas cerrado: abre el domingo a las 17:00 de Nueva York"}
        bars = await market.ohlcv(symbol, "5m", 5000 if stock else 1000)
        price = await market.price(symbol)
        atr_v = risk.atr(await market.ohlcv(symbol, config.STOCK_TIMEFRAME if risk.on_yahoo(symbol) else config.ATR_TIMEFRAME, 100))
        if stock:
            pools, why = self.stock_pools(bars)
            if pools is None:
                return {"why": why}
            opens, closes = risk.nyse_hours(dt.datetime.now(dt.timezone.utc).astimezone(risk.NEW_YORK))
            k = int((dt.datetime.now(dt.timezone.utc) - opens).total_seconds() // 300)  # vela de la sesión en curso
            last = int((closes - opens).total_seconds() // 300) - 1 - int(np.ceil(config.STOCK_FLAT_MIN / 5))
            if k + 1 > last:
                return {"why": "queda menos de una vela hasta el cierre de las acciones"}
            steps = list(range(k + 1, last + 1))
            horizon = f"hasta las {(closes - dt.timedelta(minutes=config.STOCK_FLAT_MIN)):%H:%M} de Nueva York"
        else:
            closed = bars[bars.ts + pd.Timedelta(minutes=5) <= pd.Timestamp.now(tz="UTC")]  # sin la vela en curso
            if len(closed) < 300:
                return {"why": f"solo {len(closed)} velas de 5 min: hacen falta 300 para los escenarios"}
            c = closed.close.to_numpy(float)
            pools = np.column_stack([closed[x].to_numpy(float)[1:] / c[:-1] for x in ("open", "high", "low", "close")])
            steps, horizon = [None] * int(config.SCENARIO_HOURS * 12), f"{config.SCENARIO_HOURS:g} h"
            hours = risk.fx_hours(dt.datetime.now(dt.timezone.utc).astimezone(risk.NEW_YORK)) if risk.is_fx(symbol) else None
            if hours:  # divisas: el escenario acaba, como mucho, cuando se cierra todo antes del fin de semana
                left = int((hours[1] - dt.timedelta(minutes=config.FX_FLAT_MIN)
                            - dt.datetime.now(dt.timezone.utc)).total_seconds() // 300)
                if left < 1:
                    return {"why": "queda menos de una vela hasta el cierre de las divisas"}
                if left < len(steps):
                    steps, horizon = steps[:left], f"hasta las {hours[1] - dt.timedelta(minutes=config.FX_FLAT_MIN):%H:%M} del viernes en Nueva York"
        rng = np.random.default_rng(SEED + zlib.crc32(symbol.encode()) % 10_000)
        draws = np.stack([self._draw(rng, pools, i) for i in steps], axis=1)  # (escenarios, tramos, o/h/l/c)
        return {"sides": self.outcomes(price, atr_v, draws, symbol), "n": config.SCENARIOS, "horizon": horizon}

    @staticmethod
    def _draw(rng, pools, i) -> np.ndarray:
        """Una vela pasada por escenario para el tramo i: de la misma hora (±2 velas) en acciones, cualquiera en cripto."""
        if i is None:
            return pools[rng.integers(0, len(pools), config.SCENARIOS)]
        near = np.concatenate([pools[j] for j in range(max(1, i - WINDOW), min(SESSION_BARS - 1, i + WINDOW) + 1)])
        return near[rng.integers(0, len(near), config.SCENARIOS)]

    @staticmethod
    def stock_pools(bars: pd.DataFrame) -> tuple[dict | None, str]:
        """pool[i] = la vela i de cada sesión completa anterior a hoy, como (o, h, l, c) / cierre de la vela i-1."""
        ny = bars.assign(t=bars.ts.dt.tz_convert(risk.NEW_YORK))
        pools = {i: [] for i in range(1, SESSION_BARS)}
        full = 0
        for _, d in ny.groupby(ny.t.dt.date):
            minutes = (d.t.dt.hour * 60 + d.t.dt.minute).to_numpy()
            if len(d) != SESSION_BARS or minutes[0] != 570 or (np.diff(minutes) != 5).any():
                continue  # días a medias, sesiones con huecos y la de hoy (con la bolsa abierta nunca está completa)
            full += 1
            o, h, lo, c = (d[x].to_numpy(float) for x in ("open", "high", "low", "close"))
            for i in range(1, SESSION_BARS):
                pools[i].append((o[i] / c[i - 1], h[i] / c[i - 1], lo[i] / c[i - 1], c[i] / c[i - 1]))
        if full < MIN_SESSIONS:
            return None, f"solo {full} sesiones completas en Yahoo: hacen falta {MIN_SESSIONS}"
        return {i: np.array(v) for i, v in pools.items()}, ""

    @staticmethod
    def outcomes(price: float, atr_v: float, draws: np.ndarray, symbol: str) -> dict:
        """Resultado medio en R de la operación del jefe, en largo y en corto, sobre los mismos escenarios."""
        closes = price * np.cumprod(draws[..., 3], axis=1)
        prev = np.concatenate([np.full((len(draws), 1), price), closes[:, :-1]], axis=1)
        hi, lo, end = prev * draws[..., 1], prev * draws[..., 2], closes[:, -1]
        dist = risk.stop_distance("LONG", price, atr_v)  # por ATR; sin el nivel técnico que podría ajustarlo
        cost = price * risk.round_trip_cost_pct(symbol) / 100
        steps = draws.shape[1]
        out = {}
        for side, sign in (("LONG", 1), ("SHORT", -1)):
            sl, tp = price - sign * dist, price + sign * config.REWARD_RISK * dist
            hit_sl, hit_tp = (lo <= sl, hi >= tp) if sign > 0 else (hi >= sl, lo <= tp)
            first_sl = np.where(hit_sl.any(1), hit_sl.argmax(1), steps)
            first_tp = np.where(hit_tp.any(1), hit_tp.argmax(1), steps)
            stop = (first_sl <= first_tp) & (first_sl < steps)  # una vela que toca los dos niveles cuenta el stop
            target = ~stop & (first_tp < steps)
            r = np.where(stop, -1.0, np.where(target, (config.REWARD_RISK * dist - cost) / (dist + cost),
                                              ((end - price) * sign - cost) / (dist + cost)))
            out[side] = {"er": float(r.mean()), "ptp": float(target.mean()), "psl": float(stop.mean()),
                         "popen": float(1 - target.mean() - stop.mean())}
        return out
