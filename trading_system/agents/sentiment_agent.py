"""Agente 5 · Tendencia y Sentimiento: EMA, MACD y RSI en 4H/1D + flujo del libro."""
import asyncio

import numpy as np

import config
from agents import Signal, make, neutral


def rsi(close, n: int = 14):
    d = close.diff()
    gain = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + gain / loss.replace(0, 1e-12))


def macd_hist(close):
    macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    return macd - macd.ewm(span=9, adjust=False).mean()


class SentimentAgent:
    name = "sentiment"
    interval = config.TECH_INTERVAL

    async def run(self, symbol, market) -> Signal:
        d4, d1 = await asyncio.gather(market.ohlcv(symbol, "4h", 300), market.ohlcv(symbol, "1d", 300))
        try:
            book = await market.book_imbalance(symbol)
        except Exception:
            book = None  # sin libro de órdenes el agente sigue con los indicadores
        return self.analyze(symbol, d4, d1, book)

    @staticmethod
    def tf_score(df) -> tuple[float, float]:
        c = df.close
        p, e50, e200 = c.iloc[-1], c.ewm(span=50, adjust=False).mean().iloc[-1], c.ewm(span=200, adjust=False).mean().iloc[-1]
        trend = 1 if p > e50 > e200 else -1 if p < e50 < e200 else 0
        hist = macd_hist(c)
        mom = 0.5 * np.sign(hist.iloc[-1]) + 0.5 * np.sign(hist.iloc[-1] - hist.iloc[-2])
        r = float(rsi(c).iloc[-1])
        # RSI como impulso, salvo en extremos, donde cuenta como agotamiento.
        osc = -0.5 if r >= 75 else 0.5 if r <= 25 else float(np.clip((r - 50) / 25, -1, 1))
        return 0.45 * trend + 0.35 * mom + 0.20 * osc, r

    @classmethod
    def analyze(cls, symbol, d4, d1, book: float | None = None) -> Signal:
        if len(d4) < 60 or len(d1) < 60:
            return neutral(cls.name, symbol, "histórico insuficiente")
        (s4, r4), (s1, r1) = cls.tf_score(d4), cls.tf_score(d1)
        # ponytail: el "order flow" es el desequilibrio del libro en una foto.
        # Techo: es manipulable (spoofing) y no ve agresores. Mejora: delta de
        # volumen acumulado (CVD) a partir del tape.
        score = 0.4 * s4 + 0.4 * s1 + 0.2 * book if book is not None else 0.5 * s4 + 0.5 * s1
        return make(
            cls.name, symbol, float(score),
            f"4H {s4:+.2f} (RSI {r4:.0f}) · 1D {s1:+.2f} (RSI {r1:.0f}) · libro "
            + (f"{book:+.2f}" if book is not None else "n/d"),
            support=float(d4.low.iloc[-20:].min()), resistance=float(d4.high.iloc[-20:].max()),
            features={"s4": float(s4), "s1": float(s1), "rsi4": r4, "rsi1": r1, "book": float(book or 0)})
