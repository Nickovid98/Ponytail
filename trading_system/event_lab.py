"""Laboratorio de estrategias con datos públicos oficiales: H1 a H4 de ESTADO.md ("Estrategias con datos públicos
oficiales", con su enmienda del 23-11). Las reglas, los periodos y el criterio de aprobado están escritos allí antes
de mirar ningún resultado; este archivo los aplica tal cual.

    python event_lab.py download    # baja todo: la primera vez una hora o más; después, de la caché de data/public/
    python event_lab.py train       # H1-H3: todas las variantes en 2010-2017 y elige una de cada (o la descarta)
    python event_lab.py exam        # examen 2018-2022 de las elegidas, y H4 en sus velas de 1 hora
    python event_lab.py holdout     # una sola vez: del 01-01-2023 a hoy, solo las que aprobaron el examen

Medida: una acción solo se opera si al entrar era del tamaño del S&P 500, 400 o 600 (según la SEC en ese momento), y
se mide contra la media de las acciones de ese mismo grupo en los mismos días, neta de costes. La cartera mensual
reúne todas las posiciones abiertas con el mismo peso. Entrada siempre al cierre de una sesión posterior a que el
dato sea público.
"""
import argparse
import datetime as dt
import json
import math
import sys
from bisect import bisect_left

import numpy as np
import pandas as pd

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

H1_GRID = [{"insiders": n, "usd": v, "hold": h} for n in (1, 2) for v in (0, 100_000) for h in (5, 21, 63)]
H2_GRID = [{"tail": t, "hold": h} for t in (0.10, 0.20) for h in (5, 21, 63)]
H3_GRID = [{"before": k} for k in (3, 5, 10)]
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
        self.build(adj.index, tickers, r.to_numpy(), (adj.notna() & (close >= 1)).to_numpy(), grp,
                   dict(zip(u.ticker, u.group)), {cik_of[t]: t for t in reversed(u.ticker) if t in cik_of and t in tickers})

    def build(self, dates, tickers, r, has, grp, group, cik):
        """`r`: rentabilidades diarias (NaN = sin dato); `grp`: grupo en cada fecha; `group`: el de hoy (H4)."""
        self.dates, self.tickers, self.group, self.has, self.grp = pd.DatetimeIndex(dates), list(tickers), group, has, grp
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


def insider_buys(p: Panel) -> pd.DataFrame:
    files = sorted((pdx.DIR / "insider").glob("*.parquet"))
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df = df[(df.code == "P") & df.insider & df.issuer.isin(p.cik.keys())]
    return df.assign(ticker=df.issuer.map(p.cik)).sort_values("filed").reset_index(drop=True)


# --- Operaciones y estadísticas ------------------------------------------------------------------------------------

def trades_from(p: Panel, signals: list[tuple[str, int, int, int]]) -> pd.DataFrame:
    """signals: (ticker, entrada, salida, lado) con índices de sesión. Solo si al entrar la acción tenía grupo (su
    tamaño de entonces), con precio en la entrada y la salida, y una posición por acción a la vez. El grupo de la
    entrada es el de toda la operación: salir del grupo después no la anula."""
    out, busy = [], {}
    for t, e, x, side in sorted(signals, key=lambda s: (s[1], s[0])):
        c = p.col[t]
        if e <= busy.get(t, -1) or e < 1 or x >= len(p.dates) or not (p.has[e, c] and p.has[x, c]) or not p.grp[e, c]:
            continue
        busy[t] = x
        g = int(p.grp[e, c])
        out.append({"ticker": t, "e": e, "x": x, "side": side, "entry": p.dates[e], "exit": p.dates[x], "g": g,
                    "ret": side * p.abnormal(c, g, e, x) - COST_BY_CODE[g]})
    return pd.DataFrame(out, columns=["ticker", "e", "x", "side", "entry", "exit", "g", "ret"])


def period(df: pd.DataFrame, start, end) -> pd.DataFrame:
    return df[(df.entry >= pd.Timestamp(start)) & (df.exit <= pd.Timestamp(end))]


def stats(p: Panel, df: pd.DataFrame, start, end) -> dict:
    """Por operación y, sobre todo, por cartera: cada sesión, la media de las posiciones abiertas (el coste se cobra
    el primer día); cada mes, la suma. El t-estadístico es el de la media mensual."""
    lo, hi = p.at_or_after(start), min(p.after(end), len(p.dates)) - 1
    tot, n = np.zeros(len(p.dates)), np.zeros(len(p.dates))
    for t, e, x, side, g in zip(df.ticker, df.e, df.x, df.side, df.g):
        c = p.col[t]
        tot[e + 1:x + 1] += side * (p.r[e + 1:x + 1, c] - p.bench[e + 1:x + 1, g])
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

def h1(p: Panel, buys: pd.DataFrame, v: dict) -> pd.DataFrame:
    sig = []
    for t, g in buys.groupby("ticker"):
        filed, owner, value = g.filed.to_numpy(), g.owner.to_numpy(), g.value.fillna(0).to_numpy()
        for d in np.unique(filed):
            lo = np.searchsorted(filed, d - np.timedelta64(30, "D"), side="right")
            hi = np.searchsorted(filed, d, side="right")
            if len(set(owner[lo:hi])) >= v["insiders"] and value[lo:hi].sum() >= v["usd"]:
                e = p.after(pd.Timestamp(d))  # público el día d (quizá tras el cierre): entra al cierre de la siguiente
                sig.append((t, e, e + v["hold"], 1))
    return trades_from(p, sig)


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


GRIDS = {"H1": (H1_GRID, h1), "H2": (H2_GRID, h2), "H3": (H3_GRID, h3)}


def inputs(p: Panel, name: str):
    return insider_buys(p) if name == "H1" else earnings_events(p)


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


def train() -> None:
    p = Panel()
    say(f"Panel: {len(p.tickers)} acciones, {p.dates[0].date()} a {p.dates[-1].date()}; con grupo al empezar el "
        f"entrenamiento: {int((p.grp[p.at_or_after(TRAIN[0])] > 0).sum())}, al empezar el examen: "
        f"{int((p.grp[p.at_or_after(EXAM[0])] > 0).sum())}")
    choice, table = {}, {}
    for name, (grid, fn) in GRIDS.items():
        data = inputs(p, name)
        say(f"\n{name}: {len(data)} eventos de partida")
        rows = []
        for v in grid:
            s = stats(p, period(fn(p, data, v), *TRAIN), *TRAIN)
            rows.append({"variant": v, **s})
            say(f"  {json.dumps(v)}: {fmt(s)}")
        ok = [r for r in rows if r["n"] >= 100]
        best = max(ok, key=lambda r: r["t"], default=None)
        table[name] = rows
        if best is None or best["t"] < 2:
            choice[name] = None
            say(f"  -> {name} DESCARTADA en el entrenamiento (mejor t = {best['t'] if best else 0:+.2f} < 2)")
        else:
            choice[name] = best["variant"]
            say(f"  -> elegida {json.dumps(best['variant'])} con t = {best['t']:+.2f}")
    save("train.json", {"when": dt.datetime.now().isoformat(), "choice": choice, "table": table})


def exam() -> None:
    choice = load("train.json")["choice"]
    p = Panel()
    res = {}
    for name, (grid, fn) in GRIDS.items():
        v = choice.get(name)
        if v is None:
            res[name] = {"pass": False, "why": "descartada en el entrenamiento"}
            say(f"{name}: descartada en el entrenamiento, sin examen")
            continue
        s = stats(p, period(fn(p, inputs(p, name), v), *EXAM), *EXAM)
        ok = s["n"] >= 100 and s["t"] >= 2.5 and s["years_pos"] >= 3
        res[name] = {"variant": v, **s, "pass": ok}
        say(f"{name} {json.dumps(v)}: {fmt(s)} -> {'APRUEBA' if ok else 'SUSPENDE'}")
        say(f"     por año: {s['years']}")
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
    save("exam.json", {"when": dt.datetime.now().isoformat(), "result": res})


def holdout() -> None:
    if (OUT / "holdout.json").exists():
        raise SystemExit("El periodo reservado ya se miró una vez: no se repite (ver ESTADO.md).")
    res = load("exam.json")["result"]
    p = Panel()
    out = {}
    for name, (grid, fn) in GRIDS.items():
        if not res.get(name, {}).get("pass"):
            continue
        end = str(p.dates[-1].date())
        s = stats(p, period(fn(p, inputs(p, name), res[name]["variant"]), HOLD_START, end), HOLD_START, end)
        ok = s["n"] >= 100 and s["t"] >= 1.65 and s["mean_trade"] > 0
        out[name] = {"variant": res[name]["variant"], **s, "pass": ok}
        say(f"{name} reservado {HOLD_START} a {end}: {fmt(s)} -> {'CONFIRMA' if ok else 'NO CONFIRMA'}")
    if not out:
        say("Ninguna hipótesis diaria aprobó el examen: no hay nada que mirar en el periodo reservado.")
    save("holdout.json", {"when": dt.datetime.now().isoformat(), "result": out})


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["download", "train", "exam", "holdout"])
    step = ap.parse_args().step
    sys.stdout.reconfigure(encoding="utf-8")
    {"download": download, "train": train, "exam": exam, "holdout": holdout}[step]()
