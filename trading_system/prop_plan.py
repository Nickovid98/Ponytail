"""¿Se puede pagar una cuenta de fondeo, pasarla y cobrar 500-1.000 en una o dos semanas con estos bots?

Reglas de cada empresa verificadas el 25-11-2026 en sus páginas oficiales (resumen y fuentes en ESTADO.md, "Cobrar
rápido en una cuenta de fondeo"). Cada intento: se paga; cada sesión sale el resultado de un día de los bots, sacado
al azar (con reemplazo) de una simulación de `backtest.py --save` y multiplicado por `scale` si se arriesga más por
operación; se aplican las reglas; si aprueba, se paga la activación y en la cuenta financiada se pide cada cobro en
cuanto las reglas lo permiten. Neto = lo cobrado (tras el reparto y la transferencia) - lo pagado.

    python prop_plan.py data/lab/bt_stocks_365d.json

ponytail: el suelo se mira al cierre del día (las empresas lo vigilan también dentro del día) y los costes de los
futuros no se cuentan aparte (van dentro del resultado de los bots de acciones). Techo: las cifras son optimistas.
"""
import json
import sys

import numpy as np
import pandas as pd

MAX_LOSS = 2000.0  # las seis cuentas de 50K: 2.000 que siguen al mejor cierre
FIRMS = {
    # Topstep: 49/mes + 149 de activación (o 95/mes sin activación) + API 14,50/mes con su código. Cobro por la vía
    # de consistencia: cada 3 días, el mejor día <= 40 % del beneficio desde el último cobro, hasta el 50 % del saldo
    # con tope de 3.000; transferencia internacional 30. Tras el primer cobro el suelo es 0.
    "Topstep 50K estándar": dict(fees=49 + 149 + 14.5, cons=0.55, min_days=2, lock=0, payout="topstep", wire=30),
    "Topstep 50K sin activación": dict(fees=95 + 14.5, cons=0.55, min_days=2, lock=0, payout="topstep", wire=30),
    # Topstep Labs "Daily Payouts+" (anunciada en noviembre de 2026; venta sin confirmar): 119 + API, límite diario
    # 1.000, colchón de 2.000 que se queda; cobros de 250 a 1.500 al día, cada uno tras un día nuevo en positivo.
    "Topstep Labs cobro diario": dict(fees=119 + 14.5, cons=0.55, min_days=2, lock=0, dll=1000, payout="labs", wire=30),
    # Las tres siguientes necesitarían un conector nuevo (Tradovate o Rithmic): el sistema solo opera en TopstepX.
    "Lucid 50K diario": dict(fees=94, cons=0.40, min_days=2, lock=100, dll=1200, payout="buffer", pmin=500, wire=0),
    "Tradeify Select Daily 50K": dict(fees=174, cons=0.40, min_days=3, lock=100, dll_funded=1000, payout="tradeify", wire=0),
    "MyFundedFutures Rapid 50K": dict(fees=209, cons=0.50, min_days=2, lock=100, payout="buffer", pmin=500, wire=0),
}
TARGET, BUFFER = 3000.0, 2100.0


def daily_pnl(path: str) -> np.ndarray:
    """Resultado de cada sesión de la simulación (las sesiones sin operar cuentan como 0)."""
    t = pd.DataFrame(json.load(open(path, encoding="utf-8"))["trades"])
    day = pd.to_datetime(t.closed_ts, unit="s", utc=True).dt.tz_convert("America/New_York").dt.normalize()
    pnl = t.groupby(day.dt.tz_localize(None)).pnl.sum()
    return pnl.reindex(pd.bdate_range(pnl.index.min(), pnl.index.max()), fill_value=0.0).to_numpy()


def attempt(draw, f: dict, horizon: int) -> tuple[float, bool]:
    """Un intento de `horizon` sesiones; `draw()` da el resultado de cada día. Devuelve (neto, aprobó)."""
    got, bal, peak, n, best, stage, since, paid_once = 0.0, 0.0, 0.0, 0, 0.0, "eval", [], False
    for _ in range(horizon):
        pnl = draw()
        cap = f.get("dll") or (f.get("dll_funded") if stage == "funded" else None)
        if cap:
            pnl = max(pnl, -cap)  # límite diario: para el día, la cuenta sigue
        bal += pnl
        if stage == "eval":
            n, best = n + 1, max(best, pnl)
            if bal <= min(peak - MAX_LOSS, f["lock"]):
                return got - f["fees"], False
            peak = max(peak, bal)
            if bal >= TARGET and n >= f["min_days"] and best <= f["cons"] * bal:
                stage, bal, peak, since = "funded", 0.0, 0.0, []
            continue
        floor = 0.0 if f["payout"] in ("topstep", "labs") and paid_once else min(peak - MAX_LOSS, f["lock"])
        if bal <= floor:
            return got - f["fees"], True
        peak = max(peak, bal)
        since.append(pnl)
        amount, prof = 0.0, sum(since)
        if f["payout"] == "topstep" and len(since) >= 3 and prof > 0 and max(since) <= 0.40 * prof:
            amount = min(0.5 * bal, 3000.0) if min(0.5 * bal, 3000.0) >= 125 else 0.0
        elif f["payout"] == "labs" and bal > 2000 and pnl > 0:
            amount = min(1500.0, bal - 2000) if bal - 2000 >= 250 else 0.0
        elif f["payout"] == "buffer" and bal - BUFFER >= f["pmin"] and prof > 0:
            amount = bal - BUFFER
        elif f["payout"] == "tradeify" and prof > 0:
            amount = max(0.0, min(1250.0, 2 * prof, bal - BUFFER))
        if amount > 0:
            got += 0.9 * amount - f["wire"]
            bal -= amount
            since, paid_once = [], True
    return got - f["fees"], stage == "funded"


def odds(days: np.ndarray, f: dict, horizon: int, scale: float = 1.0, n: int = 20000, seed: int = 11) -> dict:
    rng = np.random.default_rng(seed)
    sample = days * scale
    res = [attempt(lambda: float(rng.choice(sample)), f, horizon) for _ in range(n)]
    net = np.array([r[0] for r in res])
    return {"aprueba": float(np.mean([r[1] for r in res])), "cobra": float(np.mean(net > -f["fees"] + 1e-9)),
            "neto>=500": float(np.mean(net >= 500)), "neto>=1000": float(np.mean(net >= 1000)), "media": float(net.mean())}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    days = daily_pnl(sys.argv[1] if len(sys.argv) > 1 else "data/lab/bt_stocks_365d.json")
    print(f"{len(days)} días de los bots: media {days.mean():+.1f}, desviación {days.std():.0f}")
    for name, f in FIRMS.items():
        print(f"\n== {name} · pagado {f['fees']:.0f}")
        for horizon in (10, 20):
            for scale in (1, 3, 5):
                r = odds(days, f, horizon, scale)
                print(f"  {horizon:>2} sesiones · riesgo x{scale}: aprueba {r['aprueba']:6.1%} · cobra algo {r['cobra']:6.1%} · "
                      f"neto >= 500 {r['neto>=500']:6.1%} · neto >= 1000 {r['neto>=1000']:6.1%} · neto medio {r['media']:+7.0f}")
