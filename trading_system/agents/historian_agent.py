"""Agente 1 · Historiador y Predictor: regresión, dispersión y estacionalidad horaria."""
import numpy as np

import config
from agents import Signal, make, neutral


class HistorianAgent:
    name = "historian"
    interval = config.TECH_INTERVAL
    timeframe = "1h"  # la estacionalidad por hora del día asume velas de 1h
    limit = 500
    window = 72  # 3 días

    async def run(self, symbol, market) -> Signal:
        return self.analyze(symbol, await market.ohlcv(symbol, self.timeframe, self.limit))

    @classmethod
    def analyze(cls, symbol, df) -> Signal:
        n = cls.window
        if len(df) < n + 24:
            return neutral(cls.name, symbol, "histórico insuficiente")
        logp = np.log(df.close.to_numpy(float))
        x, y = np.arange(n), logp[-n:]
        slope, icpt = np.polyfit(x, y, 1)
        fit = slope * x + icpt
        resid = y - fit
        sd = resid.std(ddof=2) or 1e-12
        r2 = 1 - resid.var() / y.var() if y.var() > 0 else 0.0
        z = resid[-1] / sd  # desviaciones estándar respecto a la recta

        # Estacionalidad: retorno medio de esta misma hora del día (vela en curso excluida).
        ret, hours = np.diff(logp)[:-1], df.ts.dt.hour.to_numpy()[1:-1]
        hour = int(df.ts.dt.hour.iloc[-1])
        same = ret[hours == hour]
        t = same.mean() / (same.std(ddof=1) / np.sqrt(len(same))) if len(same) >= 10 and same.std(ddof=1) > 0 else 0.0

        score = np.sign(slope) * r2
        along = z * np.sign(slope)  # > 0: precio estirado en el sentido de la tendencia
        if along > 2:
            score *= 0.5  # sobre-extendido: no perseguir
        elif along < -1:
            score *= 1.2  # retroceso dentro de la tendencia
        if abs(t) >= 2:
            score += 0.15 * np.sign(t)
        return make(
            cls.name, symbol, float(np.clip(score, -1, 1)),
            f"Regresión {n}h {slope * 2400:+.2f}%/día, R²={r2:.2f}, z={z:+.1f}σ, estacionalidad {hour:02d}h t={t:+.1f}",
            support=float(np.exp(fit[-1] - 2 * sd)), resistance=float(np.exp(fit[-1] + 2 * sd)),
            features={"r2": float(r2), "z": float(z), "t": float(t)})
