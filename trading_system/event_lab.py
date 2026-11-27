"""Laboratorio de estrategias con datos públicos oficiales: H1 a H4 de ESTADO.md ("Estrategias con datos públicos
oficiales", con su enmienda del 23-11). Las reglas, los periodos y el criterio de aprobado están escritos allí antes
de mirar ningún resultado; este archivo los aplica tal cual.

    python event_lab.py download    # baja todo: la primera vez una hora o más; después, de la caché de data/public/
    python event_lab.py train       # H1-H3: todas las variantes en 2010-2017 y elige una de cada (o la descarta)
    python event_lab.py exam        # examen 2018-2022 de las elegidas, y H4 en sus velas de 1 hora
    python event_lab.py holdout     # una sola vez: del 01-01-2023 a hoy, solo las que aprobaron el examen
    python event_lab.py train --round 2   # lo mismo con la segunda ronda (H5-H7), con su propio umbral
    python event_lab.py download --round 3   # tercera ronda (H8 políticos, H9 grandes inversores): sus datos
    python event_lab.py train --round 3

Medida: una acción solo se opera si al entrar era del tamaño del S&P 500, 400 o 600 (según la SEC en ese momento), y
se mide contra la media de las acciones de ese mismo grupo en los mismos días, neta de costes. La cartera mensual
reúne todas las posiciones abiertas con el mismo peso. Entrada siempre al cierre de una sesión posterior a que el
dato sea público.
"""
import argparse
import datetime as dt
import json
import math
import re
import sys
from bisect import bisect_left

import numpy as np
import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar

from utils import public_data as pdx

TRAIN, EXAM = ("2010-01-01", "2017-12-31"), ("2018-01-01", "2022-12-31")
HOLD_START = "2023-01-01"
FEE = 0.005 / 100
SLIP = {"500": 0.02 / 100, "400": 0.05 / 100, "600": 0.10 / 100}
HEDGE = 0.01 / 100
COST = {g: 2 * (FEE + s) + 2 * HEDGE for g, s in SLIP.items()}  # ida y vuelta, acción y cobertura
GROUPS = ("", "500", "400", "600")  # código de grupo -> nombre; 0 = sin grupo (no se opera)
COST_BY_CODE = np.array([0.0] + [COST[g] for g in GROUPS[1:]])
RANKS = (500, 900, 1500)  # puestos por valor en manos del público: 1-500, 501-900 y 901-1500
OUT = pdx.DIR / "lab"
SNAPSHOT = 3650  # días: durante el estudio los datos no se renuevan (la misma foto para entrenar, examinar y reservar)
ADJ_GAP = 0.10  # el ajustado y el cierre solo se separan por un dividendo; más de 10 puntos es un ajuste roto
BAD_DAY = 0.75  # un movimiento de más del 75 % que se deshace en 3 sesiones es un error: fuera la ida y la vuelta
REVERT = math.log(1.15)

BORROW = 0.03  # segunda ronda: préstamo de las acciones vendidas en corto, 3 % al año por sesión abierta

H1_GRID = [{"insiders": n, "usd": v, "hold": h} for n in (1, 2) for v in (0, 100_000) for h in (5, 21, 63)]
H2_GRID = [{"tail": t, "hold": h} for t in (0.10, 0.20) for h in (5, 21, 63)]
H3_GRID = [{"before": k} for k in (3, 5, 10)]
H5_GRID = [{"late": k, "max": m} for k in (2, 5) for m in (10, 30)]
H6_GRID = [{"hold": h} for h in (5, 21, 63)]
H7_GRID = [{"insiders": n, "usd": 1_000_000, "hold": h} for n in (2, 3) for h in (21, 63)]
# Tercera ronda (ESTADO.md, "Tercera ronda"): copiar las compras declaradas por los políticos (H8) y por los grandes
# inversores en sus 13F (H9). Variantes sacadas de los estudios publicados, no de mirar los datos.
H8_GRID = [{"who": "all", "hold": 63}, {"who": "leaders", "hold": 250}, {"who": "home", "hold": 250}]
H9_GRID = [{"who": "berkshire", "grow": 0.10, "hold": 252}, {"who": "consensus", "min": 3, "hold": 63},
           {"who": "new_top10", "hold": 63}]
EXAM_T = {1: 2.5, 2: 2.8, 3: 3.0}  # 4 hipótesis en la primera ronda; 7 con la segunda; 9 con la tercera (ESTADO.md)
H4_RULES = {"gap": 0.03, "rr": 2.0, "risk_usd": 200, "max_per_day": 3, "capital": 50_000, "max_loss": 2_000,
            "daily_limit": 1_000, "kill_pct": 75, "max_notional": 100_000}


def say(*a):
    print(*a, flush=True)


# --- Datos -----------------------------------------------------------------------------------------------------

def download() -> None:
    u, ciks = pdx.universe(days=0), pdx.ciks(days=0)
    pdx.floats(days=0)
    for i, t in enumerate(u.ticker):
        for name, fn in (("diario", lambda: pdx.daily(t, days=0.5)), ("1 h", lambda: pdx.hourly(t, days=0.5)),
                         ("SEC", lambda: pdx.filings(ciks[t], days=0.5))):
            try:
                fn()
            except (ConnectionError, FileNotFoundError, KeyError, ValueError, TypeError) as e:
                say(f"  {t}: sin datos {name} ({type(e).__name__})")
        if i % 100 == 0:
            say(f"{i}/{len(u)} acciones", dt.datetime.now().strftime("%H:%M"))
    year, q = 2009, 3
    while True:
        try:
            pdx.insider(year, q)
            say(f"formularios 4 de {year}T{q}")
        except FileNotFoundError:
            break  # el trimestre siguiente todavía no está publicado
        year, q = (year + 1, 1) if q == 4 else (year, q + 1)


def clean(adj: pd.DataFrame, close: pd.DataFrame) -> pd.DataFrame:
    """Rentabilidades diarias sin los errores demostrables de los datos: precios por debajo de 1 USD, días en que el
    ajustado se separa del cierre más de ADJ_GAP y saltos de más de BAD_DAY que se deshacen en 1-3 sesiones (se
    quitan la ida y la vuelta). Un salto grande que se queda, aunque sea enorme, cuenta: es lo que habría vivido
    quien tuviera la posición."""
    ok = close >= 1
    adj, close = adj.where(ok), close.where(ok)
    r = adj.pct_change(fill_method=None)
    bad = ((r - close.pct_change(fill_method=None)).abs() > ADJ_GAP).to_numpy(copy=True)
    big = (r.abs() > BAD_DAY).to_numpy(copy=True)
    lg = np.log1p(r.fillna(0.0).clip(lower=-0.999)).to_numpy()
    c = np.vstack([np.zeros((1, lg.shape[1])), np.cumsum(lg, axis=0)])  # c[i] = suma de lg[0..i-1]
    n = len(lg)
    for k in (1, 2, 3):  # la vuelta más corta: un salto que ya se deshizo en k sesiones no marca días de más
        s = np.full_like(lg, np.inf)
        s[:n - k] = c[k + 1:] - c[:n - k]  # suma de lg[t..t+k]
        back = big & (np.abs(s) < REVERT)
        big &= ~back
        for j in range(k + 1):
            bad[j:] |= back[:n - j]
    return r.mask(bad)


def pit_groups(fl: pd.DataFrame, dates: pd.DatetimeIndex, ciks: list) -> np.ndarray:
    """Grupo de cada acción cada día según su tamaño EN ESE MOMENTO: a cada fin de mes se ordenan todas las empresas
    de la SEC por su último valor en manos del público de los 15 meses anteriores, y el puesto vale para el mes
    siguiente. 1 = S&P 500, 2 = 400, 3 = 600, 0 = fuera (o sin dato)."""
    fl = fl[(fl.val > 1e6) & (fl.val < 6e12)].sort_values("end")  # fuera valores imposibles (errores de unidades)
    out = np.zeros((len(dates), len(ciks)), np.int8)
    for m in pd.date_range(dates[0] - pd.offsets.MonthEnd(1), dates[-1], freq="ME"):
        recent = fl[(fl.end <= m) & (fl.end > m - pd.DateOffset(months=15))]
        rank = recent.groupby("cik").val.last().rank(ascending=False, method="first")
        code = pd.Series(np.select([rank <= RANKS[0], rank <= RANKS[1], rank <= RANKS[2]], [1, 2, 3], 0), rank.index)
        rows = (dates > m) & (dates <= m + pd.offsets.MonthEnd(1))
        out[rows] = np.array([code.get(c, 0) if c is not None else 0 for c in ciks], np.int8)
    return out


class Panel:
    """Precios diarios del S&P 1500 de la foto descargada, con el grupo de cada acción en cada fecha."""

    def __init__(self):
        u = pdx.universe(days=SNAPSHOT)
        cik_of = pdx.ciks(days=SNAPSHOT)
        adj, close = {}, {}
        for t in u.ticker:
            f = pdx.price_file("daily", t)
            if f.exists():
                d = pd.read_parquet(f)
                adj[t], close[t] = d.adj, d.close
        adj = pd.DataFrame(adj).sort_index()
        adj = adj[adj.index < pd.Timestamp.now(tz=pdx.NY).normalize().tz_localize(None)]  # sesiones terminadas
        full = adj.notna().sum(1)
        adj = adj[full >= 0.5 * full.max()]
        close = pd.DataFrame(close).reindex(index=adj.index, columns=adj.columns)
        r = clean(adj, close)
        tickers = list(adj.columns)
        grp = pit_groups(pdx.floats(days=SNAPSHOT), adj.index, [cik_of.get(t) for t in tickers])
        f = pdx.price_file("daily", "SPY")  # tercera ronda: también contra el S&P 500 ponderado por tamaño
        spy = pd.read_parquet(f).adj.reindex(adj.index).pct_change(fill_method=None).to_numpy() if f.exists() else None
        self.build(adj.index, tickers, r.to_numpy(), (adj.notna() & (close >= 1)).to_numpy(), grp,
                   dict(zip(u.ticker, u.group)), {cik_of[t]: t for t in reversed(u.ticker) if t in cik_of and t in tickers},
                   spy)

    def build(self, dates, tickers, r, has, grp, group, cik, spy=None):
        """`r`: rentabilidades diarias (NaN = sin dato); `grp`: grupo en cada fecha; `group`: el de hoy (H4); `spy`: las
        del SPY, el S&P 500 ponderado por tamaño (sin dato = 0)."""
        self.dates, self.tickers, self.group, self.has, self.grp = pd.DatetimeIndex(dates), list(tickers), group, has, grp
        self.spy = np.zeros(len(self.dates)) if spy is None else np.nan_to_num(np.asarray(spy, float))
        self.col = {t: i for i, t in enumerate(self.tickers)}
        self.cik = cik  # una CIK con dos clases de acciones (GOOGL y GOOG) se queda con la primera de la lista
        valid = ~np.isnan(r)
        self.r = np.where(valid, r, 0.0)
        self.bench = np.zeros((len(self.dates), 4))  # media de cada grupo ese día
        for g in (1, 2, 3):
            m = valid & (grp == g)
            self.bench[:, g] = np.where(m.sum(1) > 0, (self.r * m).sum(1) / np.maximum(m.sum(1), 1), 0.0)
        self.cum = np.cumsum(self.r, axis=0)  # suma de (e, x] = cum[x] - cum[e]
        self.cum_bench = np.cumsum(self.bench, axis=0)
        return self

    def abnormal(self, c: int, g: int, a: int, b: int) -> float:
        """Rentabilidad de la columna c menos la media de su grupo g, sumada en las sesiones a+1..b."""
        return (self.cum[b, c] - self.cum[a, c]) - (self.cum_bench[b, g] - self.cum_bench[a, g])

    def after(self, day) -> int:
        """Índice de la primera sesión estrictamente posterior a `day`."""
        return int(np.searchsorted(self.dates.values, np.datetime64(pd.Timestamp(day)), side="right"))

    def at_or_after(self, day) -> int:
        return int(np.searchsorted(self.dates.values, np.datetime64(pd.Timestamp(day)), side="left"))

    def day0(self, accepted: pd.Timestamp) -> int | None:
        """Primera sesión en la que el precio puede reaccionar a un comunicado aceptado en `accepted` (UTC): la misma
        si salió antes de las 16:00 de Nueva York en día de sesión, si no la siguiente. None si cae fuera de los
        precios."""
        t = accepted.tz_convert(pdx.NY)
        d = pd.Timestamp(t.date())
        if d < self.dates[0]:
            return None
        i = self.at_or_after(d)
        if i < len(self.dates) and self.dates[i] == d and t.hour < 16:
            return i
        return self.after(d) if self.after(d) < len(self.dates) else None


def earnings_events(p: Panel) -> pd.DataFrame:
    rows = []
    for cik, t in p.cik.items():
        if not (pdx.DIR / "sec" / f"{cik}.parquet").exists():
            continue
        for acc in pdx.earnings(cik, days=SNAPSHOT):
            i = p.day0(acc)
            if i:  # 0: el primer día de precios no tiene cierre anterior
                rows.append({"ticker": t, "accepted": acc, "d0": i})
    return pd.DataFrame(rows, columns=["ticker", "accepted", "d0"])


def insider_trades(p: Panel, code: str) -> pd.DataFrame:
    """Compras (P) o ventas (S) en bolsa de consejeros y directivos de las empresas del panel, por fecha de
    presentación."""
    files = sorted((pdx.DIR / "insider").glob("*.parquet"))
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df = df[(df.code == code) & df.insider & df.issuer.isin(p.cik.keys())]
    return df.assign(ticker=df.issuer.map(p.cik)).sort_values("filed").reset_index(drop=True)


def insider_buys(p: Panel) -> pd.DataFrame:
    return insider_trades(p, "P")


def bad_news(p: Panel) -> pd.DataFrame:
    """Avisos de mala noticia en la SEC: NT 10-K y NT 10-Q (las cuentas no llegarán a tiempo) y 8-K con el punto
    4.02 (las cuentas anteriores ya no son fiables), con la primera sesión en que el precio puede reaccionar."""
    rows = []
    for cik, t in p.cik.items():
        path = pdx.DIR / "sec" / f"{cik}.parquet"
        if not path.exists():
            continue
        f = pd.read_parquet(path)
        hit = f.form.isin(["NT 10-K", "NT 10-Q"]) | ((f.form == "8-K") & f["items"].fillna("").str.contains(r"(?:^|,)\s*4\.02"))
        for acc in f[hit].accepted.dropna():
            i = p.day0(acc)
            if i:
                rows.append({"ticker": t, "accepted": acc, "d0": i})
    return pd.DataFrame(rows, columns=["ticker", "accepted", "d0"])


# --- Operaciones y estadísticas ------------------------------------------------------------------------------------

def trades_from(p: Panel, signals: list[tuple[str, int, int, int]], borrow: float = 0.0) -> pd.DataFrame:
    """signals: (ticker, entrada, salida, lado) con índices de sesión. Solo si al entrar la acción tenía grupo (su
    tamaño de entonces), con precio en la entrada y la salida, y una posición por acción a la vez. El grupo de la
    entrada es el de toda la operación: salir del grupo después no la anula. `borrow`: coste anual del préstamo en
    las ventas en corto, cobrado por sesión abierta."""
    out, busy = [], {}
    for t, e, x, side in sorted(signals, key=lambda s: (s[1], s[0])):
        c = p.col[t]
        if e <= busy.get(t, -1) or e < 1 or x <= e or x >= len(p.dates) or not (p.has[e, c] and p.has[x, c]) \
                or not p.grp[e, c]:
            continue
        busy[t] = x
        g = int(p.grp[e, c])
        fee_day = borrow / 252 if side < 0 else 0.0
        out.append({"ticker": t, "e": e, "x": x, "side": side, "entry": p.dates[e], "exit": p.dates[x], "g": g,
                    "fee_day": fee_day, "ret": side * p.abnormal(c, g, e, x) - COST_BY_CODE[g] - fee_day * (x - e)})
    return pd.DataFrame(out, columns=["ticker", "e", "x", "side", "entry", "exit", "g", "fee_day", "ret"])


def period(df: pd.DataFrame, start, end) -> pd.DataFrame:
    return df[(df.entry >= pd.Timestamp(start)) & (df.exit <= pd.Timestamp(end))]


def stats(p: Panel, df: pd.DataFrame, start, end, vs_spy: bool = False) -> dict:
    """Por operación y, sobre todo, por cartera: cada sesión, la media de las posiciones abiertas (el coste se cobra
    el primer día); cada mes, la suma. El t-estadístico es el de la media mensual. Con `vs_spy`, la cartera se mide
    contra el SPY en vez de contra el grupo de cada acción (la media por operación sigue siendo la del grupo)."""
    lo, hi = p.at_or_after(start), min(p.after(end), len(p.dates)) - 1
    tot, n = np.zeros(len(p.dates)), np.zeros(len(p.dates))
    for t, e, x, side, g, fee in zip(df.ticker, df.e, df.x, df.side, df.g, df.fee_day):
        c = p.col[t]
        bench = p.spy[e + 1:x + 1] if vs_spy else p.bench[e + 1:x + 1, g]
        tot[e + 1:x + 1] += side * (p.r[e + 1:x + 1, c] - bench) - fee
        n[e + 1:x + 1] += 1
        tot[e + 1] -= COST_BY_CODE[g]
    daily = pd.Series(np.where(n > 0, tot / np.maximum(n, 1), 0.0)[lo:hi + 1], index=p.dates[lo:hi + 1])
    monthly = daily.groupby(daily.index.to_period("M")).sum()
    yearly = daily.groupby(daily.index.year).sum()
    curve = daily.cumsum()
    t = monthly.mean() / monthly.std(ddof=1) * math.sqrt(len(monthly)) if len(monthly) > 1 and monthly.std() > 0 else 0.0
    return {"n": int(len(df)), "mean_trade": float(df.ret.mean()) if len(df) else 0.0,
            "win": float((df.ret > 0).mean()) if len(df) else 0.0, "t": float(t),
            "monthly_mean": float(monthly.mean()), "annual": float(monthly.mean() * 12),
            "vol": float(monthly.std(ddof=1) * math.sqrt(12)) if len(monthly) > 1 else 0.0,
            "max_dd": float((curve.cummax() - curve).max()) if len(curve) else 0.0,
            "years": {int(y): round(float(v), 4) for y, v in yearly.items()},
            "years_pos": int((yearly > 0).sum()), "n_years": int(len(yearly))}


# --- Hipótesis -------------------------------------------------------------------------------------------------

def cluster(p: Panel, rows: pd.DataFrame, v: dict, side: int) -> list[tuple[str, int, int, int]]:
    """Señales de directivos: en cada fecha de presentación d, al menos v["insiders"] personas distintas y v["usd"]
    en total en los 30 días hasta d. Público el día d (quizá tras el cierre): entra al cierre de la sesión siguiente."""
    sig = []
    for t, g in rows.groupby("ticker"):
        filed, owner, value = g.filed.to_numpy(), g.owner.to_numpy(), g.value.fillna(0).to_numpy()
        for d in np.unique(filed):
            lo = np.searchsorted(filed, d - np.timedelta64(30, "D"), side="right")
            hi = np.searchsorted(filed, d, side="right")
            if len(set(owner[lo:hi])) >= v["insiders"] and value[lo:hi].sum() >= v["usd"]:
                e = p.after(pd.Timestamp(d))
                sig.append((t, e, e + v["hold"], side))
    return sig


def h1(p: Panel, buys: pd.DataFrame, v: dict) -> pd.DataFrame:
    return trades_from(p, cluster(p, buys, v, 1))


def h2(p: Panel, ev: pd.DataFrame, v: dict) -> pd.DataFrame:
    """Reacción = del cierre anterior al comunicado al cierre del día siguiente al comunicado (para uno tras el
    cierre, solo la sesión siguiente), frente a su grupo de ese momento. Se entra al cierre de la sesión posterior."""
    rows = []
    for t, acc, d0 in zip(ev.ticker, ev.accepted, ev.d0):
        c, known = p.col[t], p.after(acc.tz_convert(pdx.NY).date())
        if known + 1 < len(p.dates) and d0 >= 1 and p.grp[d0, c] and known >= d0:
            rows.append((t, known, p.abnormal(c, int(p.grp[d0, c]), d0 - 1, known)))
    rows.sort(key=lambda x: x[1])
    days = p.dates[[k for _, k, _ in rows]].to_numpy() if rows else np.array([], "datetime64[ns]")
    ears = np.array([x for _, _, x in rows])
    sig = []
    for k, (t, known, ear) in enumerate(rows):
        lo = bisect_left(days, days[k] - np.timedelta64(365, "D"))
        hi = bisect_left(days, days[k])  # solo reacciones ya conocidas antes de ese día
        if hi - lo < 200:
            continue
        past = ears[lo:hi]
        side = 1 if ear >= np.quantile(past, 1 - v["tail"]) else -1 if ear <= np.quantile(past, v["tail"]) else 0
        if side:
            sig.append((t, known + 1, known + 1 + v["hold"], side))
    return trades_from(p, sig)


def h3(p: Panel, ev: pd.DataFrame, v: dict) -> pd.DataFrame:
    sig = []
    for t, d0 in zip(ev.ticker, ev.d0):
        due = p.at_or_after(p.dates[d0] + pd.Timedelta(days=364))  # fecha esperada: la misma de hace un año
        if due + 1 < len(p.dates):
            sig.append((t, due - v["before"], due + 1, 1))
    return trades_from(p, sig)


def reports(p: Panel) -> pd.DataFrame:
    """Informes trimestrales y anuales (10-Q y 10-K, sin enmiendas), con la primera sesión en que el precio puede
    reaccionar."""
    rows = []
    for cik, t in p.cik.items():
        path = pdx.DIR / "sec" / f"{cik}.parquet"
        if not path.exists():
            continue
        f = pd.read_parquet(path)
        for acc in f[f.form.isin(["10-Q", "10-K"])].accepted.dropna():
            i = p.day0(acc)
            if i:
                rows.append({"ticker": t, "accepted": acc, "d0": i})
    return pd.DataFrame(rows, columns=["ticker", "accepted", "d0"])


def h5(p: Panel, data: tuple[pd.DataFrame, pd.DataFrame], v: dict) -> pd.DataFrame:
    """Resultados que se retrasan (con la enmienda del 24-11): v["late"] sesiones después de la fecha esperada (la del
    mismo trimestre del año anterior + 364 días) sin aviso de ese trimestre, corto al cierre. Aviso = un 8-K 2.02
    desde 45 días antes de la fecha esperada o su 10-Q/10-K desde 15 días antes, lo primero que llegue; se cierra al
    cierre del día siguiente, o a las v["max"] sesiones. Un 2.02 con otro a menos de 50 días (un avance, una
    actualización) no sirve de referencia."""
    ev, rep = data
    filed = {t: np.sort(g.d0.to_numpy()) for t, g in rep.groupby("ticker")}
    sig = []
    for t, g in ev.groupby("ticker"):
        d0s = np.sort(g.d0.to_numpy())
        q = filed.get(t, np.array([], int))
        for i, d in enumerate(d0s):
            gap_prev = (p.dates[d] - p.dates[d0s[i - 1]]).days if i else 999
            gap_next = (p.dates[d0s[i + 1]] - p.dates[d]).days if i + 1 < len(d0s) else 999
            if min(gap_prev, gap_next) < 50:
                continue
            due = p.at_or_after(p.dates[d] + pd.Timedelta(days=364))
            s = due + v["late"]
            if s + 1 >= len(p.dates):
                continue
            j = np.searchsorted(d0s, p.at_or_after(p.dates[due] - pd.Timedelta(days=45)), side="left")
            jq = np.searchsorted(q, p.at_or_after(p.dates[due] - pd.Timedelta(days=15)), side="left")
            ends = [int(a[k]) for a, k in ((d0s, j), (q, jq)) if k < len(a)]
            first = min(ends) if ends else None  # primer aviso de ese trimestre, llegue cuando llegue
            if first is not None and first <= s:
                continue  # ya llegó (o se adelantó): no hay retraso que vender
            sig.append((t, s, min(first + 1, s + v["max"]) if first is not None else s + v["max"], -1))
    return trades_from(p, sig, BORROW)


def h6(p: Panel, ev: pd.DataFrame, v: dict) -> pd.DataFrame:
    """Avisos de mala noticia: corto al cierre de la primera sesión en que el precio puede reaccionar."""
    return trades_from(p, [(t, d0, d0 + v["hold"], -1) for t, d0 in zip(ev.ticker, ev.d0)], BORROW)


def h7(p: Panel, sales: pd.DataFrame, v: dict) -> pd.DataFrame:
    """Ventas en racimo de directivos: corto al cierre de la sesión siguiente a la presentación."""
    return trades_from(p, cluster(p, sales, v, -1), BORROW)


# --- Tercera ronda: copiar a los políticos (H8) y a los grandes inversores (H9) -------------------------------------

def flat(symbol) -> str:
    """BRK.B, BRK-B y BRKB son lo mismo."""
    return re.sub(r"[^A-Z0-9]", "", str(symbol).upper())


class Names:
    """CUSIP o símbolo de una fecha → ticker del panel. Primero el CUSIP de hoy (State Street); si no está (cambió o
    la empresa ya no está en el índice), el símbolo que tenía ese día según las entregas fallidas de la SEC, que
    también traduce los cambios de ticker (FB pasó a META con el mismo CUSIP)."""

    def __init__(self, tickers, now: dict[str, str], ftd: dict[str, pd.DataFrame]):
        self.flat = {flat(t): t for t in tickers}
        self.now = {c: t for c, t in now.items() if t in set(tickers)}
        self.months = sorted(ftd)
        self.stamps = [pd.Timestamp(f"{m[:4]}-{m[4:]}-08") for m in self.months]  # mitad de la primera quincena
        self.sym = {m: dict(zip(d.cusip, d.symbol.map(flat))) for m, d in ftd.items()}
        self.cus = {m: dict(zip(d.symbol.map(flat), d.cusip)) for m, d in ftd.items()}

    def _month(self, when) -> str | None:
        w = pd.Timestamp(when)
        w = w.tz_convert(None) if w.tzinfo else w
        i = bisect_left(self.stamps, w)
        near = [j for j in (i - 1, i) if 0 <= j < len(self.months)]
        return self.months[min(near, key=lambda j: abs(self.stamps[j] - w))] if near else None

    @staticmethod
    def _same(ticker: str | None, when) -> str | None:
        """Un ticker al que solo se llega por el símbolo: no vale si entonces era de otra empresa (REUSED)."""
        w = pd.Timestamp(when)
        w = w.tz_convert(None) if w.tzinfo else w
        return None if ticker in REUSED and w < pd.Timestamp(REUSED[ticker]) else ticker

    def of_cusip(self, cusip: str, when) -> str | None:
        if cusip in self.now:
            return self.now[cusip]
        m = self._month(when)
        return self._same(self.flat.get(self.sym[m].get(cusip, "")), when) if m else None

    def of_symbol(self, symbol: str, when) -> str | None:
        m = self._month(when)
        cusip = self.cus[m].get(flat(symbol)) if m else None
        return self.now.get(cusip) or self._same(self.flat.get(flat(symbol)), when)


def names(p: Panel) -> Names:
    ftd = {f.stem: pd.read_parquet(f) for f in sorted((pdx.DIR / "ftd").glob("[0-9]*.parquet"))}
    return Names(p.tickers, pdx.cusips(days=SNAPSHOT), ftd)


# Los 12 cargos de dirección del Congreso (Wei y Zhou, 2025): presidente de la Cámara; líderes y "whips" de la mayoría
# y de la minoría en las dos cámaras; presidentes del caucus demócrata y de la conferencia republicana de la Cámara, y de
# la conferencia republicana del Senado (la demócrata la preside su líder). Quién los tuvo de 2013 a hoy, encadenando
# cargos seguidos: (cámara, nombre, desde, hasta).
LEADERS = [
    ("house", r"Boehner", "2011-01-05", "2015-10-29"), ("house", r"Paul.*Ryan", "2015-10-29", "2019-01-03"),
    ("house", r"Pelosi", "2011-01-03", "2023-01-03"), ("house", r"Kevin.*McCarthy", "2011-01-03", "2023-10-03"),
    ("house", r"Mike.*Johnson|James Michael Johnson", "2023-10-25", "2099-01-01"),
    ("house", r"Cantor", "2011-01-03", "2014-07-31"), ("house", r"Hoyer", "2011-01-03", "2023-01-03"),
    ("house", r"Scalise", "2014-08-01", "2099-01-01"), ("house", r"Clyburn", "2019-01-03", "2023-01-03"),
    ("house", r"Jeffries", "2019-01-03", "2099-01-01"), ("house", r"Emmer", "2023-01-03", "2099-01-01"),
    ("house", r"Katherine.*Clark", "2023-01-03", "2099-01-01"), ("house", r"Becerra", "2013-01-03", "2017-01-03"),
    ("house", r"Crowley", "2017-01-03", "2019-01-03"), ("house", r"Aguilar", "2023-01-03", "2099-01-01"),
    ("house", r"McMorris|Rodgers", "2013-01-03", "2019-01-03"), ("house", r"Cheney", "2019-01-03", "2021-05-12"),
    ("house", r"Stefanik", "2021-05-14", "2025-01-03"), ("house", r"Lisa.*McClain", "2025-01-03", "2099-01-01"),
    ("senate", r"Reid", "2007-01-04", "2017-01-03"), ("senate", r"McConnell", "2007-01-04", "2025-01-03"),
    ("senate", r"Schumer", "2017-01-03", "2099-01-01"), ("senate", r"Durbin", "2005-01-04", "2099-01-01"),
    ("senate", r"Cornyn", "2013-01-03", "2019-01-03"), ("senate", r"Thune", "2012-01-24", "2099-01-01"),
    ("senate", r"Barrasso", "2019-01-03", "2099-01-01"), ("senate", r"Cotton", "2025-01-03", "2099-01-01"),
]
NOT_STOCK = r"option|bond|\bnotes?\b|fund|\betf\b|treasury|t-bill|warrant|preferred|%|municipal"  # sin tipo: el nombre
# Aunque lleven el código de acción ([ST]), son opciones, bonos o preferentes (sin "option" ni "strike" a secas, que
# también son Option Care Health y CrowdStrike).
DERIVATIVE = (r"call options?|put options?|\bcalls?\b|\bputs?\b|strike price|preferred (?:security|stock|shares)|"
              r"corporate bonds?|\bbonds?\b|depositary shares|perpetual|warrants?")
# ponytail: tickers que hoy son de otra empresa (comprobados a mano el 26-11-2026, con la fecha desde la que son de la
# de hoy). Si un CUSIP o un símbolo de antes solo se traduce por el símbolo, no vale para estas. Techo: puede haber
# más casos, del orden del 1 % de las operaciones.
REUSED = {"WTW": "2022-01-10", "BBT": "2025-09-02", "COHR": "2022-09-08", "DOC": "2024-03-01", "IR": "2020-03-02",
          "BGC": "2023-07-03", "COR": "2023-08-30", "GEN": "2022-11-08", "ESI": "2019-02-01", "SGI": "2025-02-18",
          "AXON": "2022-01-01", "P": "2019-02-01"}


def leader(chamber, name, when) -> bool:
    """¿Tenía uno de los 12 cargos de dirección ese día?"""
    return not pd.isna(when) and any(
        c == chamber and re.search(pat, name or "", re.I) and pd.Timestamp(a) <= when < pd.Timestamp(b)
        for c, pat, a, b in LEADERS)


def congress_buys(p: Panel, nm: Names) -> pd.DataFrame:
    """Compras de acciones declaradas por los miembros del Congreso, una por operación aunque salga en varias
    declaraciones (cuenta la primera), con el ticker del panel, si el comprador tenía un cargo de dirección y si la
    sede de la empresa está en su estado. Sin opciones, bonos ni fondos."""
    df = pdx.congress()
    typ = df.asset_type.fillna("").str.upper().str.strip()
    named = df.asset.str.extract(r"\[([A-Za-z]{2})\]", expand=False).fillna("").str.upper()  # "(AAPL) [sT]"
    typ = typ.where(~typ.isin(["", "NONE"]), named)
    stock = typ.isin(["ST", "STOCK"]) | ((typ == "") & ~df.asset.str.contains(NOT_STOCK, case=False, regex=True))
    stock &= ~df.asset.str.contains(DERIVATIVE, case=False, regex=True)
    # El ticker de la ficha tiene que ser el del texto, si el texto trae uno: a veces se toma del nombre de la cuenta.
    said = df.asset.str.extract(r"\(([A-Z][A-Z0-9]{0,5}(?:[.\-/][A-Z])?)\)", expand=False)
    stock &= said.isna() | (said.map(flat) == df.ticker.map(flat))
    df = df[(df.kind == "Purchase") & stock & df.ticker.str.fullmatch(r"[A-Z]{1,5}([.\-/][A-Z])?")]
    df = df.sort_values("filed").drop_duplicates(["filer", "traded", "ticker"])  # la misma, redeclarada: la primera
    df = df.assign(t=[nm.of_symbol(s, d) for s, d in zip(df.ticker, df.filed)]).dropna(subset=["t"])
    cik_of = pdx.ciks(days=SNAPSHOT)
    home = pdx.states(sorted({cik_of[t] for t in p.tickers if t in cik_of}), days=SNAPSHOT)
    df["home"] = [bool(s) and home.get(cik_of.get(t), "") == s for t, s in zip(df.t, df.state)]
    df["leader"] = [leader(c, n, d) for c, n, d in zip(df.chamber, df.name, df.traded)]
    return df.reset_index(drop=True)


def h8(p: Panel, buys: pd.DataFrame, v: dict) -> pd.DataFrame:
    """Compra al cierre de la sesión siguiente a la declaración (no a la operación: hasta entonces nadie la ve)."""
    rows = buys[buys.leader] if v["who"] == "leaders" else buys[buys.home] if v["who"] == "home" else buys
    return trades_from(p, [(t, e, e + v["hold"], 1) for t, e in zip(rows.t, (p.after(d) for d in rows.filed))])


def ny_day(ts: pd.Timestamp) -> pd.Timestamp:
    """Fecha de Nueva York de un instante UTC."""
    return ts.tz_convert(pdx.NY).tz_localize(None).normalize()


def fund_frames(p: Panel, nm: Names) -> tuple[pd.DataFrame, pd.DataFrame]:
    """13F de los gestores de pdx.MANAGERS. `orig`: una fila por gestor, trimestre y CUSIP de sus 13F originales (si
    presentó con dos CIK, sumadas; si una CIK presentó dos originales del mismo trimestre, el primero), con el ticker
    del panel, el valor, cuándo fue público todo el trimestre, cuántas posiciones tenía y el puesto de cada una por
    valor. `amend`: las posiciones que salieron después en enmiendas ("NEW HOLDINGS"), con su propia fecha."""
    frames = [h.assign(manager=m, cik=cik) for m, ciks in pdx.MANAGERS.items() for cik in ciks
              if len(h := pdx.holdings13f(cik, days=SNAPSHOT))]
    df = pd.concat(frames, ignore_index=True).dropna(subset=["period", "accepted"])
    df = df[df.value > 0].assign(q=lambda d: d.period.dt.to_period("Q"))
    df["t"] = [nm.of_cusip(c, a) for c, a in zip(df.cusip, df.accepted)]
    o = df[~df.amendment.astype(bool)]
    first = o.sort_values("accepted").groupby(["cik", "q"]).accession.first()
    o = o[o.accession.isin(set(first))]
    orig = o.groupby(["manager", "q", "cusip"], as_index=False).agg(t=("t", "first"), value=("value", "sum"))
    orig = orig.join(o.groupby(["manager", "q"]).accepted.max(), on=["manager", "q"])
    orig["n"] = orig.groupby(["manager", "q"]).cusip.transform("count")
    orig["rank"] = orig.groupby(["manager", "q"]).value.rank(ascending=False, method="first")
    amend = df[df.amendment.astype(bool)][["manager", "q", "cusip", "t", "value", "accepted"]].reset_index(drop=True)
    return orig.reset_index(drop=True), amend


BERKSHIRE = "Berkshire Hathaway (Warren Buffett)"


def _adj_at(t: str, day: pd.Timestamp) -> float | None:
    f = pdx.price_file("daily", t)
    if not f.exists():
        return None
    s = pd.read_parquet(f).adj.dropna()
    s = s[s.index <= day]
    return float(s.iloc[-1]) if len(s) else None


def held(prev: pd.DataFrame) -> tuple[set, set]:
    """Lo que un gestor ya tenía: tickers y emisores (los 6 primeros caracteres del CUSIP). Una acción que cambió de
    CUSIP, o una clase nueva del mismo emisor (GOOG y GOOGL), no es una apuesta nueva."""
    return set(prev.t.dropna()), set(prev.cusip.str[:6])


def is_new(x: pd.DataFrame, before: tuple[set, set]) -> pd.Series:
    return ~x.t.isin(before[0]) & ~x.cusip.str[:6].isin(before[1])


def berkshire(p: Panel, orig: pd.DataFrame, amend: pd.DataFrame, v: dict) -> list[tuple[str, int, int, int]]:
    """Cada 13F de Berkshire (y cada enmienda con posiciones que eran secretas): las acciones nuevas frente a lo que
    se sabía del trimestre anterior en ese momento, o las que crecen al menos un v["grow"] en número de acciones
    (sin contar los splits: el valor crece más que el precio ajustado)."""
    pub: dict = {}
    for q, g in orig[orig.manager == BERKSHIRE].groupby("q"):
        pub.setdefault(q, []).append((g.accepted.max(), g))
    for (q, acc), g in amend[amend.manager == BERKSHIRE].groupby(["q", "accepted"]):
        pub.setdefault(q, []).append((acc, g))
    sig = []
    for q in sorted(pub):
        if q - 1 not in pub:
            continue  # sin el trimestre anterior no se sabe qué es nuevo
        end, end0 = q.end_time.normalize().tz_localize(None), (q - 1).end_time.normalize().tz_localize(None)
        for acc, g in pub[q]:
            known = [g0 for acc0, g0 in pub[q - 1] if acc0 < acc]  # lo que se sabía del trimestre anterior
            prev = pd.concat(known) if known else g.iloc[:0]
            before = prev.dropna(subset=["t"]).groupby("t").value.sum()
            new = is_new(g, held(prev))
            e = p.after(ny_day(acc))
            for t, val in g.dropna(subset=["t"]).groupby("t").value.sum().items():
                old = float(before.get(t, 0.0))
                if old > 0:
                    a, a0 = _adj_at(t, end), _adj_at(t, end0)
                    grow = val / old / (a / a0) if a and a0 else val / old
                    if grow < 1 + v["grow"]:
                        continue
                elif not new[g.t == t].all():
                    continue  # el emisor ya estaba con otro CUSIP: no se sabe si crece
                sig.append((t, e, e + v["hold"], 1))
    return sig


HOLIDAYS = set(USFederalHolidayCalendar().holidays("2013-01-01", "2030-12-31"))


def deadline13f(q: pd.Period) -> pd.Timestamp:
    """Último día para presentar el 13F de un trimestre: 45 días después, o el siguiente día hábil de la SEC."""
    day = q.end_time.normalize().tz_localize(None) + pd.Timedelta(days=45)
    while day.weekday() >= 5 or day in HOLIDAYS:
        day += pd.Timedelta(days=1)
    return day


def consensus(p: Panel, orig: pd.DataFrame, v: dict) -> list[tuple[str, int, int, int]]:
    """Cada trimestre, el día después del plazo: las acciones que al menos v["min"] gestores tienen entre sus 10
    mayores posiciones, con los 13F públicos hasta el plazo. Solo cuentan los gestores con 10 a 200 posiciones (los
    de miles, como Renaissance, no apuestan por unas pocas). Si una acción sigue en el consenso en la revisión
    siguiente, su posición se alarga en vez de cerrarse y volver a abrirse."""
    pos: list = []  # [ticker, entrada, salida]
    last: dict = {}
    for q, g in sorted(orig[(orig.n >= 10) & (orig.n <= 200)].groupby("q"), key=lambda x: x[0]):
        deadline = deadline13f(q)
        cut = (deadline + pd.Timedelta(days=1)).tz_localize(pdx.NY)  # todo el día del plazo
        top = g[(g.accepted <= cut) & (g["rank"] <= 10)].dropna(subset=["t"])
        count = top.groupby("t").manager.nunique()
        e = p.after(deadline)
        for t in count[count >= v["min"]].index:
            if t in last and last[t][2] >= e:
                last[t][2] = e + v["hold"]
            else:
                last[t] = [t, e, e + v["hold"]]
                pos.append(last[t])
    return [(t, e, x, 1) for t, e, x in pos]


def new_top10(p: Panel, orig: pd.DataFrame, v: dict) -> list[tuple[str, int, int, int]]:
    """Apuestas nuevas: una acción entra entre las 10 mayores posiciones de un gestor sin estar en su 13F del
    trimestre anterior, ni ella ni su emisor. Se compra al cierre de la sesión siguiente a ese 13F."""
    sig = []
    for _, g in orig.groupby("manager"):
        by_q = dict(tuple(g.groupby("q")))
        for q, x in by_q.items():
            prev = by_q.get(q - 1)
            if prev is None:
                continue
            fresh = x[(x["rank"] <= 10) & x.t.notna() & is_new(x, held(prev))]
            e = p.after(ny_day(x.accepted.max()))
            sig += [(t, e, e + v["hold"], 1) for t in fresh.t]
    return sig


def h9(p: Panel, data: tuple[pd.DataFrame, pd.DataFrame], v: dict) -> pd.DataFrame:
    orig, amend = data
    if v["who"] == "berkshire":
        return trades_from(p, berkshire(p, orig, amend, v))
    return trades_from(p, consensus(p, orig, v) if v["who"] == "consensus" else new_top10(p, orig, v))


def without_top(df: pd.DataFrame, k: int = 3) -> float:
    """Media por operación sin las k acciones que más aportan: que una o dos estrellas no lo expliquen todo."""
    if df.empty:
        return 0.0
    rest = df[~df.ticker.isin(df.groupby("ticker").ret.sum().nlargest(k).index)]
    return float(rest.ret.mean()) if len(rest) else 0.0


ROUNDS = {1: {"H1": (H1_GRID, h1), "H2": (H2_GRID, h2), "H3": (H3_GRID, h3)},
          2: {"H5": (H5_GRID, h5), "H6": (H6_GRID, h6), "H7": (H7_GRID, h7)},
          3: {"H8": (H8_GRID, h8), "H9": (H9_GRID, h9)}}
GRIDS = {name: g for r in ROUNDS.values() for name, g in r.items()}


def need3() -> None:
    """La tercera ronda no se pasa con datos a medias: sin el SPY, la prueba contra el SPY sería contra cero; sin las
    entregas fallidas, solo se traducirían los CUSIP de hoy."""
    if not pdx.price_file("daily", "SPY").exists() or not list((pdx.DIR / "ftd").glob("[0-9]*.parquet")):
        raise SystemExit("Faltan datos de la tercera ronda (el SPY o las entregas fallidas): python event_lab.py "
                         "download --round 3")


def inputs(p: Panel, name: str):
    if name in ("H8", "H9"):
        need3()
    if name == "H8":
        return congress_buys(p, names(p))
    if name == "H9":
        return fund_frames(p, names(p))
    if name == "H1":
        return insider_buys(p)
    if name == "H7":
        return insider_trades(p, "S")
    if name == "H5":
        return earnings_events(p), reports(p)
    return bad_news(p) if name == "H6" else earnings_events(p)


def h4(p: Panel, ev: pd.DataFrame) -> pd.DataFrame:
    """Hueco de resultados en el día, con velas de 1 hora. Cada operación guarda su recorrido vela a vela (resultado
    flotante y lo que daría cerrarla ahí) para aplicar después las reglas de la cuenta de fondeo."""
    r = H4_RULES
    cands = []
    for t, g in ev.groupby("ticker"):
        path = pdx.price_file("hourly", t)
        if not path.exists():
            continue
        h = pd.read_parquet(path)
        h = h[h.index.normalize().tz_localize(None) < p.dates[-1] + pd.Timedelta(days=1)]  # sesiones terminadas
        days = {d: b for d, b in h.groupby(h.index.date)}
        order = sorted(days)
        for acc, d0 in zip(g.accepted, g.d0):
            local = acc.tz_convert(pdx.NY)
            day = p.dates[d0].date()
            if local.date() == day and 9 * 60 + 30 <= local.hour * 60 + local.minute < 16 * 60:
                continue  # comunicado con la bolsa abierta: el hueco ya no es la reacción completa
            if day not in days or order.index(day) == 0:
                continue
            bars, prev = days[day], days[order[order.index(day) - 1]]
            if len(bars) < 3 or bars.index[0].strftime("%H:%M") != "09:30":
                continue
            gap = bars.open.iloc[0] / prev.close.iloc[-1] - 1
            side = 1 if gap >= r["gap"] else -1 if gap <= -r["gap"] else 0
            first = bars.iloc[0]
            if not side or (first.close - first.open) * side <= 0:
                continue
            entry = bars.open.iloc[1]
            stop = first.low if side > 0 else first.high
            risk = (entry - stop) * side
            if risk <= 0:
                continue
            cost = FEE + SLIP[p.group[t]]
            shares = math.floor(min(r["risk_usd"] / (risk + (entry + stop) * cost), r["max_notional"] / entry))
            if shares < 1:
                continue
            target = entry + side * r["rr"] * risk
            trace, exit_, pnl = {}, None, 0.0
            for ts, b in bars.iloc[1:].iterrows():
                if exit_ is None:
                    if (b.low <= stop) if side > 0 else (b.high >= stop):  # si toca los dos, cuenta el stop
                        exit_ = min(b.open, stop) if side > 0 else max(b.open, stop)
                    elif (b.high >= target) if side > 0 else (b.low <= target):
                        exit_ = max(b.open, target) if side > 0 else min(b.open, target)
                    if exit_ is None:
                        mark = shares * (b.close - entry) * side
                        trace[ts] = (mark, mark - shares * (entry + b.close) * cost)
                        continue
                    pnl = shares * (exit_ - entry) * side - shares * (entry + exit_) * cost
                trace[ts] = (pnl, pnl)
            if exit_ is None:  # sin stop ni objetivo: sale al cierre
                exit_ = bars.close.iloc[-1]
                pnl = shares * (exit_ - entry) * side - shares * (entry + exit_) * cost
                trace[bars.index[-1]] = (pnl, pnl)
            cands.append({"ticker": t, "day": pd.Timestamp(day), "gap": gap, "side": side, "entry": entry,
                          "stop": stop, "exit": exit_, "shares": shares, "pnl": pnl, "R": pnl / r["risk_usd"],
                          "path": trace})
    df = pd.DataFrame(cands)
    if df.empty:
        return df
    df = df.assign(size=df.gap.abs()).sort_values(["day", "size"], ascending=[True, False])
    return df.groupby("day").head(r["max_per_day"]).drop(columns="size").reset_index(drop=True)


def h4_stats(df: pd.DataFrame) -> dict:
    """Reglas de la cuenta de fondeo vela a vela, como el motor: la cuenta se pierde si el saldo con lo flotante toca
    el suelo (2.000 bajo el mejor cierre diario, sin pasar del capital inicial); a 750 de pérdida en el día se cierra
    todo. El t-estadístico, las mitades y las semanas salen del resultado diario ya con esas reglas."""
    r = H4_RULES
    if df.empty:
        return {"n": 0}
    kill, cap = r["daily_limit"] * r["kill_pct"] / 100, r["capital"]
    eq = peak = cap
    lost, out = 0, {}
    for day, g in df.groupby("day"):
        floor, realized, dead = min(peak - r["max_loss"], cap), None, False
        for ts in sorted(set().union(*[set(tr) for tr in g.path])):
            marks = [tr[max(k for k in tr if k <= ts)] if min(tr) <= ts else (0.0, 0.0) for tr in g.path]
            total = sum(m[0] for m in marks)
            if eq + total <= floor or -total >= kill:
                realized, dead = sum(m[1] for m in marks), eq + total <= floor
                break
        pnl = float(g.pnl.sum()) if realized is None else realized
        out[day] = pnl
        if dead:
            lost, eq, peak = lost + 1, cap, cap
        else:
            eq += pnl
            peak = max(peak, eq)
    daily = pd.Series(out)
    t = daily.mean() / daily.std(ddof=1) * math.sqrt(len(daily)) if len(daily) > 1 and daily.std() > 0 else 0.0
    mid = daily.index.min() + (daily.index.max() - daily.index.min()) / 2
    weekly = daily.groupby(daily.index.to_period("W")).sum()
    return {"n": int(len(df)), "days": int(len(daily)), "mean_R": float(df.R.mean()), "win": float((df.pnl > 0).mean()),
            "t": float(t), "total": float(daily.sum()), "halves": [float(daily[daily.index <= mid].sum()),
                                                                 float(daily[daily.index > mid].sum())],
            "accounts_lost": lost, "weekly_mean": float(weekly.mean()), "weeks_500": int((weekly >= 500).sum()),
            "weeks": int(len(weekly)), "from": str(daily.index.min().date()), "to": str(daily.index.max().date())}


# --- Pasos del protocolo ------------------------------------------------------------------------------------------

def fmt(s: dict) -> str:
    return (f"{s['n']:>6} ops · media {s['mean_trade'] * 100:+.3f} % por op · acierto {s['win']:.0%} · "
            f"t = {s['t']:+.2f} · {s['annual'] * 100:+.1f} % al año (vol {s['vol'] * 100:.1f} %) · "
            f"años en positivo {s['years_pos']} de {s['n_years']}")


def save(name: str, data) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(data, indent=1, default=str))


def load(name: str):
    path = OUT / name
    if not path.exists():
        raise SystemExit(f"Falta {path.name}: hay que correr antes el paso anterior.")
    return json.loads(path.read_text())


def passes(s: dict, rnd: int, spy: dict | None = None, rest: float | None = None) -> bool:
    """Aprueba el examen: al menos 100 operaciones, el t de su ronda y al menos 3 de los 5 años en positivo. Desde la
    tercera ronda, además, gana también al SPY (t >= 1,65) y sigue ganando sin sus 3 acciones que más aportan."""
    ok = s["n"] >= 100 and s["t"] >= EXAM_T[rnd] and s["years_pos"] >= 3
    if rnd >= 3:
        ok = ok and spy is not None and spy["t"] >= 1.65 and rest is not None and rest > 0
    return ok


def confirms(s: dict, spy: dict | None = None) -> bool:
    """Confirma en el periodo reservado: al menos 100 operaciones, media positiva y t >= 1,65. Desde la tercera
    ronda (con `spy`), además, la cartera gana al SPY."""
    return s["n"] >= 100 and s["t"] >= 1.65 and s["mean_trade"] > 0 and (spy is None or spy["monthly_mean"] > 0)


def extra(p: Panel, df: pd.DataFrame, start, end) -> tuple[dict, float]:
    """Las dos pruebas de la tercera ronda: contra el SPY y sin las 3 acciones que más aportan."""
    return stats(p, df, start, end, vs_spy=True), without_top(df)


def fmt3(spy: dict, rest: float) -> str:
    return (f"contra el SPY: t = {spy['t']:+.2f}, {spy['annual'] * 100:+.1f} % al año · sin las 3 que más aportan: "
            f"{rest * 100:+.3f} % por op")


def tag(rnd: int, step: str) -> str:
    """Archivo de cada paso: train.json, exam.json y holdout.json para la primera ronda; train2.json... para la segunda."""
    return f"{step}{'' if rnd == 1 else rnd}.json"


def train(rnd: int) -> None:
    p = Panel()
    say(f"Ronda {rnd}. Panel: {len(p.tickers)} acciones, {p.dates[0].date()} a {p.dates[-1].date()}; con grupo al "
        f"empezar el entrenamiento: {int((p.grp[p.at_or_after(TRAIN[0])] > 0).sum())}, al empezar el examen: "
        f"{int((p.grp[p.at_or_after(EXAM[0])] > 0).sum())}")
    choice, table = {}, {}
    for name, (grid, fn) in ROUNDS[rnd].items():
        data = inputs(p, name)
        say(f"\n{name}: {len(data[0]) if isinstance(data, tuple) else len(data)} eventos de partida")
        rows = []
        for v in grid:
            df = period(fn(p, data, v), *TRAIN)
            s = stats(p, df, *TRAIN)
            rows.append({"variant": v, **s})
            say(f"  {json.dumps(v)}: {fmt(s)}")
            if rnd >= 3:  # solo para informar: la elección sigue siendo por el t frente al grupo
                spy, rest = extra(p, df, *TRAIN)
                rows[-1] |= {"spy": spy, "without_top3": rest}
                say(f"      {fmt3(spy, rest)}")
        ok = [r for r in rows if r["n"] >= 100]
        best = max(ok, key=lambda r: r["t"], default=None)
        table[name] = rows
        if best is None or best["t"] < 2:
            choice[name] = None
            say(f"  -> {name} DESCARTADA en el entrenamiento (mejor t = {best['t'] if best else 0:+.2f} < 2)")
        else:
            choice[name] = best["variant"]
            say(f"  -> elegida {json.dumps(best['variant'])} con t = {best['t']:+.2f}")
    save(tag(rnd, "train"), {"when": dt.datetime.now().isoformat(), "choice": choice, "table": table})


def exam(rnd: int) -> None:
    choice = load(tag(rnd, "train"))["choice"]
    p = Panel()
    res = {}
    for name, (grid, fn) in ROUNDS[rnd].items():
        v = choice.get(name)
        if v is None:
            res[name] = {"pass": False, "why": "descartada en el entrenamiento"}
            say(f"{name}: descartada en el entrenamiento, sin examen")
            continue
        df = period(fn(p, inputs(p, name), v), *EXAM)
        s = stats(p, df, *EXAM)
        spy, rest = extra(p, df, *EXAM) if rnd >= 3 else (None, None)
        ok = passes(s, rnd, spy, rest)
        res[name] = {"variant": v, **s, "pass": ok} | ({"spy": spy, "without_top3": rest} if rnd >= 3 else {})
        say(f"{name} {json.dumps(v)}: {fmt(s)} -> {'APRUEBA' if ok else 'SUSPENDE'} (hace falta t >= {EXAM_T[rnd]})")
        if rnd >= 3:
            say(f"     {fmt3(spy, rest)}")
        say(f"     por año: {s['years']}")
    if rnd == 1:  # H4 no tiene variantes: todo su periodo es examen
        trades = h4(p, earnings_events(p))
        s = h4_stats(trades)
        ok = s.get("n", 0) >= 100 and s.get("t", 0) >= 2.5 and min(s.get("halves", [0])) > 0 and s.get("accounts_lost", 1) == 0
        res["H4"] = {**H4_RULES, **s, "pass": ok}
        if s.get("n"):
            say(f"H4 ({s['from']} a {s['to']}): {s['n']} ops en {s['days']} días · media {s['mean_R']:+.3f} R · acierto "
                f"{s['win']:.0%} · t = {s['t']:+.2f} · total {s['total']:+,.0f} · mitades {s['halves'][0]:+,.0f} y "
                f"{s['halves'][1]:+,.0f} · cuentas perdidas {s['accounts_lost']} · media semanal {s['weekly_mean']:+,.0f} "
                f"· semanas >= 500: {s['weeks_500']} de {s['weeks']} -> {'APRUEBA' if ok else 'SUSPENDE'}")
            trades.drop(columns="path").to_csv(OUT / "h4_trades.csv", index=False)
    save(tag(rnd, "exam"), {"when": dt.datetime.now().isoformat(), "result": res})


def holdout(rnd: int) -> None:
    if (OUT / tag(rnd, "holdout")).exists():
        raise SystemExit("El periodo reservado ya se miró una vez para esta ronda: no se repite (ver ESTADO.md).")
    res = load(tag(rnd, "exam"))["result"]
    p = Panel()
    out = {}
    for name, (grid, fn) in ROUNDS[rnd].items():
        if not res.get(name, {}).get("pass"):
            continue
        end = str(p.dates[-1].date())
        df = period(fn(p, inputs(p, name), res[name]["variant"]), HOLD_START, end)
        s = stats(p, df, HOLD_START, end)
        spy = stats(p, df, HOLD_START, end, vs_spy=True) if rnd >= 3 else None
        ok = confirms(s, spy)
        out[name] = {"variant": res[name]["variant"], **s, "pass": ok} | ({"spy": spy} if spy else {})
        say(f"{name} reservado {HOLD_START} a {end}: {fmt(s)} -> {'CONFIRMA' if ok else 'NO CONFIRMA'}")
    if not out:
        say("Ninguna hipótesis diaria aprobó el examen: no hay nada que mirar en el periodo reservado.")
    save(tag(rnd, "holdout"), {"when": dt.datetime.now().isoformat(), "result": out})


def download3() -> None:
    """Datos de la tercera ronda: el SPY, lo declarado por el Congreso, los 13F de los gestores, los CUSIP de hoy, las
    entregas fallidas de un mes de cada trimestre (Feb, may, ago y nov: cuando salen los 13F) y la sede de cada empresa."""
    pdx.daily("SPY", days=0.5)
    say(f"Congreso: {len(pdx.congress())} operaciones declaradas")
    for m, ciks in pdx.MANAGERS.items():
        for cik in ciks:
            say(f"13F de {m} (CIK {cik}): {len(pdx.holdings13f(cik, days=0.5))} posiciones")
    say(f"CUSIP de hoy: {len(pdx.cusips(days=0))}")
    for year in range(2013, dt.date.today().year + 1):
        for month in (2, 5, 8, 11):
            try:
                pdx.ftd_symbols(f"{year}{month:02d}")
            except FileNotFoundError:
                pass  # todavía no publicado
    cik_of = pdx.ciks(days=SNAPSHOT)
    st = pdx.states(sorted({cik_of[t] for t in pdx.universe(days=SNAPSHOT).ticker if t in cik_of}))
    say(f"Sedes: {sum(bool(v) for v in st.values())} de {len(st)} empresas")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["download", "train", "exam", "holdout"])
    ap.add_argument("--round", type=int, default=1, choices=sorted(ROUNDS), help="ronda de hipótesis (ESTADO.md)")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if a.step == "download":
        download3() if a.round == 3 else download()
    else:
        {"train": train, "exam": exam, "holdout": holdout}[a.step](a.round)
