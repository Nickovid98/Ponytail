"""Examen del bot 6 (escenarios): ¿ganan de verdad las apuestas que elige?

En cada momento de decisión el bot ve solo las velas ya cerradas, hace sus escenarios y elige como en vivo (el mejor
lado de cada activo, solo con media positiva, las SCENARIO_TOP mejores). Cada apuesta elegida se cierra después
con las mismas reglas (stop 1,5 ATR, objetivo REWARD_RISK, costes) sobre las velas que vinieron de verdad. El bot
no tiene nada que ajustar, así que todo el periodo es examen fuera de muestra.

Cripto: velas de 5 min de CCXT (EXCHANGE_ID, por defecto binanceusdm, que solo responde desde el PC del usuario),
una decisión cada SCENARIO_HOURS horas. Acciones: velas de 5 min de Yahoo (solo 60 días), una decisión al día a
las 10:30 de Nueva York.

    python scenario_exam.py --days 730                      # cripto de config.SYMBOLS
    python scenario_exam.py --stocks NVDA,META,GOOGL        # acciones (60 días: no llega para aprobar)
"""
import argparse
import asyncio
import datetime as dt
import json
import os
import pathlib
import zlib

os.environ.setdefault("SCENARIOS", "2000")  # ponytail: 2.000 escenarios por decisión, no 10.000. Error de la media ~0,02 R.

import numpy as np
import pandas as pd

import config
from agents import scenario_agent as sa
from backtest import resample
from utils import risk_manager as risk
from utils.exchange import make_exchange, yahoo_chart

CACHE = pathlib.Path(__file__).parent / "data" / "cache"
BAR = pd.Timedelta(minutes=5)
R_USD = 200  # riesgo por operación de la cuenta de fondeo (0,4 % de 50.000)
LOST_R = 10  # 2.000 bajo el máximo pierde la cuenta


async def crypto_bars(symbol: str, days: int) -> pd.DataFrame:
    path = CACHE / f"exam_{config.EXCHANGE_ID}_{symbol.replace('/', '-').replace(':', '-')}_5m_{days}d_{dt.date.today():%Y%m%d}.csv"
    if path.exists():
        return pd.read_csv(path, parse_dates=["ts"])
    ex = make_exchange()
    try:
        end = ex.milliseconds()
        since, rows = end - (days + 4) * 86_400_000, []  # 4 días más para los escenarios de la primera decisión
        while since < end:
            batch = await ex.fetch_ohlcv(symbol, "5m", since=since, limit=1000)
            if not batch or batch[-1][0] < since:
                break
            rows += batch
            since = batch[-1][0] + 300_000
    finally:
        await ex.close()
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"]).drop_duplicates("ts")
    df["ts"] = pd.to_datetime(df.ts, unit="ms", utc=True)
    df = df.sort_values("ts").reset_index(drop=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return df


def ratios(bars: pd.DataFrame, price: float) -> np.ndarray:
    """Velas reales como (o, h, l, c) / cierre anterior; la primera, sobre el precio de entrada."""
    prev = np.concatenate([[price], bars.close.to_numpy(float)[:-1]])
    return np.column_stack([bars[x].to_numpy(float) / prev for x in ("open", "high", "low", "close")])


def bet(symbol: str, t, price: float, atr_v: float, draws: np.ndarray, real: np.ndarray) -> dict:
    """Lo que el bot espera de cada lado y lo que pasó de verdad, en R netos de costes."""
    exp = sa.ScenarioAgent.outcomes(price, atr_v, draws, symbol)
    got = sa.ScenarioAgent.outcomes(price, atr_v, real[None], symbol)
    side = max(exp, key=lambda s: exp[s]["er"])
    return {"symbol": symbol, "t": t, "side": side, "er": exp[side]["er"], "r": got[side]["er"],
            "r_other": got["SHORT" if side == "LONG" else "LONG"]["er"]}


def rng_for(symbol: str):
    return np.random.default_rng(sa.SEED + zlib.crc32(symbol.encode()) % 10_000)


def crypto_bets(symbol: str, m5: pd.DataFrame, days: int) -> list[dict]:
    m15 = resample(m5, "15min")
    steps = int(config.SCENARIO_HOURS * 12)
    ts = m5.ts.to_numpy("datetime64[ns]")
    first = m5.ts.iloc[-1].normalize() - pd.Timedelta(days=days)
    out, rng = [], rng_for(symbol)
    for t in pd.date_range(first, m5.ts.iloc[-1] - pd.Timedelta(hours=config.SCENARIO_HOURS),
                           freq=f"{config.SCENARIO_HOURS:g}h"):
        i = int(np.searchsorted(ts, np.datetime64(t.tz_localize(None), "ns")))  # primera vela que abre en t o después
        if i < 1000 or i + steps > len(m5) or m5.ts.iloc[i] != t or m5.ts.iloc[i + steps - 1] != t + (steps - 1) * BAR:
            continue  # sin historia suficiente o con un hueco en las velas que deciden el resultado
        past = m5.iloc[i - 1000:i]  # lo que ve el bot: las últimas ~3,5 jornadas, todas cerradas
        c = past.close.to_numpy(float)
        pools = np.column_stack([past[x].to_numpy(float)[1:] / c[:-1] for x in ("open", "high", "low", "close")])
        draws = np.stack([pools[rng.integers(0, len(pools), config.SCENARIOS)] for _ in range(steps)], axis=1)
        price = float(m5.open.iloc[i])
        atr_v = risk.atr(m15[m15.ts + pd.Timedelta(minutes=15) <= t].iloc[-100:])
        out.append(bet(symbol, t, price, atr_v, draws, ratios(m5.iloc[i:i + steps], price)))
    return out


async def yahoo_bars(symbol: str, interval: str, span: str) -> pd.DataFrame:
    rows = (await yahoo_chart(symbol, interval, span))["rows"]
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df.ts, unit="ms", utc=True)
    return df.sort_values("ts").reset_index(drop=True)


async def stock_bets(symbol: str) -> list[dict]:
    m5 = await yahoo_bars(symbol, "5m", "60d")
    h1 = await yahoo_bars(symbol, "1h", "730d")
    ny = m5.ts.dt.tz_convert(risk.NEW_YORK)
    out, rng = [], rng_for(symbol)
    for day, d in m5.groupby(ny.dt.date):
        t = pd.Timestamp(dt.datetime.combine(day, dt.time(10, 30)), tz=risk.NEW_YORK)
        hours = risk.nyse_hours(t.to_pydatetime())
        if hours is None or len(d) != sa.SESSION_BARS:
            continue  # solo días con la sesión entera, para poder cerrar la operación
        opens, closes = hours
        k = int((t - pd.Timestamp(opens)).total_seconds() // 300)  # 12: la vela de las 10:30
        last = int((closes - opens).total_seconds() // 300) - 1 - int(np.ceil(config.STOCK_FLAT_MIN / 5))
        # ponytail: decide justo al abrir la vela k y simula desde ella; en vivo el bot simula desde k+1 porque la k
        # ya ha empezado. Techo: la primera vela simulada difiere en una.
        pools, why = sa.ScenarioAgent.stock_pools(m5[m5.ts < t.tz_convert("UTC").normalize()].iloc[-5000:])
        if pools is None:
            continue
        draws = np.stack([sa.ScenarioAgent._draw(rng, pools, i) for i in range(k, last + 1)], axis=1)
        price = float(d.open.iloc[k])
        atr_v = risk.atr(h1[h1.ts + pd.Timedelta(hours=1) <= t].iloc[-100:])
        out.append(bet(symbol, t.tz_convert("UTC"), price, atr_v, draws, ratios(d.iloc[k:last + 1], price)))
    return out


def chosen(bets: list[dict]) -> list[dict]:
    """Como en vivo: en cada momento, las SCENARIO_TOP apuestas con mayor media, solo si es positiva."""
    out = []
    for _, group in pd.DataFrame(bets).groupby("t"):
        out += group[group.er > 0].sort_values("er", ascending=False).head(config.SCENARIO_TOP).to_dict("records")
    return out


def report(bets: list[dict], title: str) -> dict:
    allb, take = pd.DataFrame(bets), pd.DataFrame(chosen(bets))
    lines = [f"\n== {title} ==",
             f"Momentos de decisión: {allb.t.nunique()} · apuestas posibles: {len(allb)} · elegidas: {len(take)}"]
    if take.empty:
        print("\n".join(lines + ["El bot no eligió ninguna apuesta."]))
        return {"n": 0}
    take = take.sort_values("t")
    cum = take.r.cumsum()
    dd = float((cum.cummax().clip(lower=0) - cum).max())
    q = take.groupby(take.t.dt.tz_convert(None).dt.to_period("Q")).r.sum()
    res = {"n": len(take), "mean_r": float(take.r.mean()), "net_r": float(take.r.sum()), "dd_r": dd,
           "quarters_pos": int((q > 0).sum()), "quarters": len(q), "win": float((take.r > 0).mean()),
           "expected_r": float(take.er.mean()), "other_side_r": float(take.r_other.mean()),
           "all_best_side_r": float(allb.r.mean())}
    lines += [
        f"Operaciones: {res['n']} · ganadoras {res['win']:.0%} · media {res['mean_r']:+.3f} R "
        f"(el bot esperaba {res['expected_r']:+.3f} R) · total {res['net_r']:+.1f} R = {res['net_r'] * R_USD:+,.0f}",
        f"Peor caída desde el máximo: {dd:.1f} R = {dd * R_USD:,.0f}"
        + (" · CUENTA PERDIDA" if dd >= LOST_R else ""),
        f"Trimestres en positivo: {res['quarters_pos']} de {res['quarters']} · "
        + " ".join(f"{p}: {v:+.1f}R" for p, v in q.items()),
        f"Comparación: el lado contrario de las mismas apuestas da {res['other_side_r']:+.3f} R de media; "
        f"todas las apuestas (elegidas o no, por su mejor lado) {res['all_best_side_r']:+.3f} R.",
    ]
    for s, g in take.groupby("symbol"):
        lines.append(f"  {s:<16} {len(g):>5} ops · media {g.r.mean():+.3f} R · total {g.r.sum():+.1f} R")
    print("\n".join(lines))
    return res


async def main() -> None:
    ap = argparse.ArgumentParser(description="Examen del bot de escenarios fuera de muestra.")
    ap.add_argument("--days", type=int, default=730, help="días de examen en cripto")
    ap.add_argument("--stocks", metavar="LISTA", help="acciones separadas por comas (Yahoo, 60 días)")
    ap.add_argument("--save", metavar="ARCHIVO", help="guarda las apuestas en JSON")
    a = ap.parse_args()
    out = {}
    print(f"{config.SCENARIOS:,} escenarios por decisión · R:R {config.REWARD_RISK} · stop {config.ATR_SL_MULT} ATR")
    if a.stocks:
        bets = [b for s in a.stocks.split(",") for b in await stock_bets(s.strip())]
        out["stocks"] = {"bets": bets, "result": report(bets, "Acciones (Yahoo, 60 días: no basta para aprobar)")}
    else:
        bets = []
        for s in [s for s in config.SYMBOLS if "/" in s]:
            bets += crypto_bets(s, await crypto_bars(s, a.days), a.days)
            print(f"  {s}: {sum(b['symbol'] == s for b in bets)} decisiones simuladas", flush=True)
        out["crypto"] = {"bets": bets, "result": report(bets, f"Cripto ({config.EXCHANGE_ID}, {a.days} días)")}
    if a.save:
        pathlib.Path(a.save).write_text(json.dumps(out, default=str, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
