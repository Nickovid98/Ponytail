"""Agente 3 · SMC Intradía: estructura (BOS/CHoCH), order blocks, FVG y liquidez."""
import config
from agents import Signal, neutral
from utils.risk_manager import atr


class SMCAgent:
    name = "smc"
    interval = config.SMC_INTERVAL
    timeframe = config.SMC_TIMEFRAME
    limit = 300
    k = 3  # velas a cada lado para confirmar un swing

    async def run(self, symbol, market) -> Signal:
        return self.analyze(symbol, await market.ohlcv(symbol, self.timeframe, self.limit))

    @classmethod
    def analyze(cls, symbol, df) -> Signal:
        # ponytail: reglas mecánicas sobre fractales de k velas. Techo: un solo
        # timeframe, y un order block es simplemente "la última vela contraria
        # antes de la ruptura". Para afinarlo, añadir el sesgo de 15m/1h.
        n, k = len(df), cls.k
        if n < 60:
            return neutral(cls.name, symbol, "histórico insuficiente")
        o, h, l, c = (df[x].to_numpy(float) for x in ("open", "high", "low", "close"))
        atr_v = atr(df)
        highs = [i for i in range(k, n - k) if h[i] == h[i - k:i + k + 1].max()]
        lows = [i for i in range(k, n - k) if l[i] == l[i - k:i + k + 1].min()]

        # Recorrer las velas en orden; un swing solo "existe" k velas después
        # de formarse, así que no se usa información del futuro.
        hs, ls = set(highs), set(lows)
        trend, hi, lo, ev = 0, None, None, None
        for i in range(n):
            if i - k in hs:
                hi = i - k
            if i - k in ls:
                lo = i - k
            if hi is not None and c[i] > h[hi]:
                ev = (i, "CHoCH" if trend < 0 else "BOS", 1, h[hi])
                trend, hi = 1, None
            elif lo is not None and c[i] < l[lo]:
                ev = (i, "CHoCH" if trend > 0 else "BOS", -1, l[lo])
                trend, lo = -1, None
        if ev is None:
            return neutral(cls.name, symbol, "sin ruptura de estructura")
        i0, kind, d, level = ev
        price, age = c[-1], n - 1 - i0

        # Order block: última vela contraria antes de la ruptura.
        ob = next((j for j in range(i0 - 1, max(i0 - 30, -1), -1) if (c[j] - o[j]) * d < 0), None)
        ob_lo, ob_hi = (l[ob], h[ob]) if ob is not None else (level, level)
        if (price < ob_lo) if d > 0 else (price > ob_hi):
            return neutral(cls.name, symbol, f"{kind} invalidado: precio al otro lado del order block")
        retest = ob_lo - 0.25 * atr_v <= price <= ob_hi + 0.25 * atr_v

        # Fair Value Gap: hueco entre la vela j-2 y la j dejado por el impulso de
        # la ruptura. Solo cuenta si el precio aún no lo ha rellenado.
        fvg = None
        for j in range(n - 1, max(ob if ob is not None else i0 - 10, 2) - 1, -1):
            lo_g, hi_g = (h[j - 2], l[j]) if d > 0 else (h[j], l[j - 2])
            if hi_g > lo_g and ((l[j:].min() > lo_g) if d > 0 else (h[j:].max() < hi_g)):
                fvg = (lo_g, hi_g)
                break

        # Barrido de liquidez antes de la ruptura: mecha que perfora un swing y cierra de vuelta.
        sw = lows if d > 0 else highs

        def swept(b):
            prev = [s for s in sw if s + k <= b][-3:]
            return any((l[b] < l[s] < c[b]) if d > 0 else (h[b] > h[s] > c[b]) for s in prev)

        sweep = any(swept(b) for b in range(max(i0 - 10, 0), i0 + 1))

        # Pools de liquidez: dos swings casi al mismo precio (equal highs / equal lows).
        def pools(idx, arr):
            lv = sorted(arr[i] for i in idx[-12:])
            return [(a + b) / 2 for a, b in zip(lv, lv[1:]) if b - a <= 0.15 * atr_v]

        above = [p for p in pools(highs, h) + [h[i] for i in highs[-6:]] if p > price]
        below = [p for p in pools(lows, l) + [l[i] for i in lows[-6:]] if p < price]

        conf = (58 if kind == "CHoCH" else 50) + 15 * sweep + 15 * retest + 10 * (fvg is not None) - min(35, age * 0.7)
        conf = int(max(0, min(100, conf)))
        tf = cls.timeframe
        why = (f"{kind} {'alcista' if d > 0 else 'bajista'} hace {age} velas {tf}; "
               f"OB {ob_lo:.6g}-{ob_hi:.6g}{' en retest' if retest else ''}"
               + (f"; FVG {fvg[0]:.6g}-{fvg[1]:.6g}" if fvg else "") + ("; barrido previo" if sweep else ""))
        return Signal(
            agent=cls.name, symbol=symbol, confidence=conf, rationale=why,
            direction="NEUTRAL" if conf < 25 else "LONG" if d > 0 else "SHORT",
            support=float(ob_lo if d > 0 else max(below)) if d > 0 or below else None,
            resistance=float(ob_hi if d < 0 else min(above)) if d < 0 or above else None,
            features={"age": float(age), "choch": float(kind == "CHoCH"), "retest": float(retest),
                      "sweep": float(sweep), "fvg": float(fvg is not None)})
