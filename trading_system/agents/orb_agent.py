"""Ruptura del rango de apertura (ORB), con las reglas de las skills trader-entry y trader-exit.

El rango es el máximo y el mínimo de los primeros minutos de la sesión. La entrada es la primera
vela posterior que CIERRA fuera del rango: una mecha no cuenta y un cierre justo en el nivel
tampoco. Solo vale la primera ruptura del día y solo en la vela en que ocurre: una entrada tardía
es otra operación. El stop va al punto medio del rango (o a su extremo opuesto).

Dos genes vienen del estudio de Zarattini, Barbon y Aziz (2024) sobre "acciones en juego": operar solo
cuando el rango de apertura trae un volumen anormal (`rvol`, casi siempre por una noticia) y solo en el
sentido de la vela de apertura (`dir`). La confianza crece con ese volumen relativo, de modo que si hay
más señales que huecos entra primero la acción más "en juego".
"""
import itertools
import math

import numpy as np

import config
from agents import Signal, make, neutral

GAP_S = 4 * 3600  # un hueco mayor entre velas marca el inicio de una sesión
# Los valores por defecto son los de la skill: 15 minutos, stop en el punto medio, objetivo 2R, solo largos.
DEFAULT = {"or_min": 15, "stop": "mid", "vol_x": 0.0, "sides": "long", "rr": 2.0, "rvol": 0.0, "dir": "break"}
# stop "atr" deja el stop al gestor de riesgo (ATR_SL_MULT x ATR). rr 10 equivale a mantener hasta el cierre.
GRID = {"or_min": (5, 15, 30, 60), "stop": ("mid", "atr"), "rvol": (0.0, 1.0, 2.0), "sides": ("long", "both"),
        "dir": ("break", "candle"), "rr": (2.0, 10.0)}


def genomes(tf_s: int) -> list[dict]:
    """La población: todas las combinaciones de GRID cuyo rango cabe en velas enteras de `tf_s` segundos."""
    return [DEFAULT | g for g in (dict(zip(GRID, c)) for c in itertools.product(*GRID.values()))
            if g["or_min"] * 60 % tf_s == 0]


class ORBAgent:
    name = "orb"
    interval = config.SMC_INTERVAL
    every_bar = True  # se evalúa en cada vela: la señal solo vive en la vela de la ruptura

    def __init__(self, genes: dict | None = None):
        self.genes = DEFAULT | (genes or {})

    async def run(self, symbol, market) -> Signal:
        tf = config.SMC_TIMEFRAME
        tf_s = int(tf[:-1]) * {"m": 60, "h": 3600, "d": 86400}[tf[-1]]
        bars = max(150, 15 * math.ceil(23400 / tf_s) + 20)  # 15 sesiones de bolsa: las que usa el volumen relativo
        return self.analyze(symbol, await market.ohlcv(symbol, tf, bars), self.genes, tf_s)

    @classmethod
    def analyze(cls, symbol, df, genes: dict, tf_s: int) -> Signal:
        secs = df.ts.values.astype("datetime64[s]").astype("int64")
        opens = np.flatnonzero(np.diff(secs) > GAP_S)
        if not len(opens):
            return neutral(cls.name, symbol, "mercado continuo: sin rango de apertura")
        day, n = df.iloc[opens[-1] + 1:], max(1, int(genes["or_min"] * 60 // tf_s))
        if len(day) <= n:
            return neutral(cls.name, symbol, "rango de apertura en formación")
        rng, after = day.iloc[:n], day.iloc[n:]
        hi, lo = float(rng.high.max()), float(rng.low.min())
        close = after.close.to_numpy()
        candle = float(rng.close.iloc[-1] - rng.open.iloc[0])  # sentido de la vela de apertura
        up = (close > hi) & (genes["dir"] != "candle" or candle > 0)
        down = (close < lo) & (genes["sides"] == "both") & (genes["dir"] != "candle" or candle < 0)
        hits = np.flatnonzero(up | down)
        if not len(hits):
            return neutral(cls.name, symbol, f"esperando ruptura: cierre {close[-1]:.2f}, rango {lo:.2f}-{hi:.2f}",
                           support=lo, resistance=hi)
        if hits[0] != len(after) - 1:
            return neutral(cls.name, symbol, "la ruptura de hoy ya pasó")
        long, bar, mean_vol = bool(up[-1]), after.iloc[-1], float(rng.volume.mean())
        volx = float(bar.volume) / mean_vol if mean_vol else 0.0
        if volx < genes["vol_x"]:
            return neutral(cls.name, symbol, f"ruptura sin volumen: {volx:.1f}x la media del rango")
        # Volumen relativo: el del rango de hoy frente a la media de los rangos de las sesiones anteriores.
        starts, vol = np.r_[opens + 1, len(df)], df.volume.to_numpy()
        prev = [vol[a:a + n].sum() for a, b in zip(starts[:-2], starts[1:-1]) if b - a > n][-14:]
        rvol = float(rng.volume.sum()) / float(np.mean(prev)) if len(prev) >= 5 and np.mean(prev) > 0 else 0.0
        if rvol < genes["rvol"]:
            return neutral(cls.name, symbol, f"no está en juego: volumen relativo {rvol:.1f}x")
        strength = (close[-1] - hi if long else lo - close[-1]) / (hi - lo) if hi > lo else 0.0
        score = 0.5 + 0.35 * min(1.0, rvol / 5) + 0.15 * min(1.0, strength)
        stop = {"mid": (hi + lo) / 2, "low": lo if long else hi}.get(genes["stop"])  # "atr": lo pone el gestor de riesgo
        return make(cls.name, symbol, score if long else -score,
                    f"{'Cierre sobre' if long else 'Cierre bajo'} el rango {lo:.2f}-{hi:.2f}; volumen relativo {rvol:.1f}x",
                    support=stop if long else None, resistance=None if long else stop,
                    features={"strength": round(strength, 3), "volx": round(volx, 2), "rvol": round(rvol, 2),
                              "width": round((hi - lo) / close[-1] * 100, 3), "bars": float(len(after))})
