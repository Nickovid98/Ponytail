"""Agente 4 · Wyckoff y Perfil de Volumen: fase del mercado, POC, HVN y área de valor."""
import numpy as np

import config
from agents import Signal, neutral


class WyckoffAgent:
    name = "wyckoff"
    interval = config.TECH_INTERVAL
    timeframe = config.WYCKOFF_TIMEFRAME
    limit = 240
    rng = 60  # velas que se examinan como posible rango

    async def run(self, symbol, market) -> Signal:
        return self.analyze(symbol, await market.ohlcv(symbol, self.timeframe, self.limit))

    @staticmethod
    def volume_profile(df, bins: int = 40) -> dict | None:
        lo, hi = float(df.low.min()), float(df.high.max())
        if not hi > lo:
            return None
        edges, vol = np.linspace(lo, hi, bins + 1), np.zeros(bins)
        # ponytail: el volumen de cada vela se reparte uniforme entre los
        # precios que tocó. Techo: es una aproximación desde OHLCV; el perfil
        # exacto necesita el tape de operaciones (fetch_trades).
        for bl, bh, v in zip(df.low, df.high, df.volume):
            a = min(int((bl - lo) / (hi - lo) * bins), bins - 1)
            b = min(int((bh - lo) / (hi - lo) * bins), bins - 1)
            vol[a:b + 1] += v / (b - a + 1)
        centers = (edges[:-1] + edges[1:]) / 2
        a = b = int(vol.argmax())
        acc, total = vol[a], vol.sum()
        while acc < 0.7 * total and (a > 0 or b < bins - 1):  # área de valor: 70 % del volumen
            left = vol[a - 1] if a > 0 else -1
            right = vol[b + 1] if b < bins - 1 else -1
            if right >= left:
                b += 1
                acc += vol[b]
            else:
                a -= 1
                acc += vol[a]
        return {"poc": float(centers[int(vol.argmax())]), "val": float(edges[a]), "vah": float(edges[b + 1]),
                "hvn": [float(x) for x in centers[vol > vol.mean() + vol.std()]]}

    @classmethod
    def analyze(cls, symbol, df) -> Signal:
        # ponytail: Wyckoff por heurística (eficiencia del movimiento, sesgo de
        # volumen, spring/upthrust). Techo: no etiqueta eventos finos (SC, AR,
        # ST, LPS...) y confunde rangos de continuación con acumulación. Sirve
        # como sesgo de medio plazo, no como gatillo de entrada.
        r = cls.rng
        vp = cls.volume_profile(df) if len(df) >= r + 30 else None
        if vp is None:
            return neutral(cls.name, symbol, "histórico insuficiente")
        prior, rg = df.iloc[:-r], df.iloc[-r:]
        price = float(df.close.iloc[-1])
        steps = rg.close.diff().abs().sum()
        er = abs(rg.close.iloc[-1] - rg.close.iloc[0]) / steps if steps else 0.0  # eficiencia de Kaufman
        up, dn = rg.volume[rg.close > rg.open].sum(), rg.volume[rg.close < rg.open].sum()
        imb = float((up - dn) / (up + dn)) if up + dn else 0.0
        early, late = rg.iloc[:-10], rg.iloc[-10:]
        spring = bool(late.low.min() < early.low.min() < price)  # perfora el suelo del rango y vuelve
        upthrust = bool(late.high.max() > early.high.max() > price)
        fell = prior.close.iloc[-1] < prior.close.iloc[0]

        score, phase = 0.0, "rango sin sesgo"
        if er < 0.2:  # un paseo aleatorio de 60 pasos ronda 0,13
            if spring != upthrust:
                phase, score = ("acumulación", 1) if spring else ("distribución", -1)
                score *= 0.55 + min(0.3, abs(imb))
            elif fell and imb > 0.05:
                phase, score = "acumulación", 0.4 + min(0.3, imb)
            elif not fell and imb < -0.05:
                phase, score = "distribución", -(0.4 + min(0.3, -imb))
        elif price > vp["vah"]:
            phase, score = "markup", 0.45 + min(0.4, er)
        elif price < vp["val"]:
            phase, score = "markdown", -(0.45 + min(0.4, er))
        else:
            phase, score = "tendencia dentro del área de valor", 0.3 * np.sign(price - vp["poc"])

        levels = [vp["val"], vp["poc"], vp["vah"], *vp["hvn"]]
        conf = int(min(100, abs(score) * 100))
        return Signal(
            agent=cls.name, symbol=symbol, confidence=conf,
            direction="NEUTRAL" if conf < 20 else "LONG" if score > 0 else "SHORT",
            support=max((x for x in levels if x < price), default=None),
            resistance=min((x for x in levels if x > price), default=None),
            rationale=(f"{phase.capitalize()}; POC {vp['poc']:.6g}, VA {vp['val']:.6g}-{vp['vah']:.6g}; "
                       f"vol↑-↓ {imb:+.2f}; ER {er:.2f}{'; spring' if spring else ''}{'; upthrust' if upthrust else ''}"))
