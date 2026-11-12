"""Backtest: reproduce el motor sobre velas históricas antes de arriesgar dinero.

    python backtest.py --days 30                 # descarga las velas (y las guarda) y simula
    python backtest.py --days 30 --evolution     # con la selección natural activada
    python backtest.py --days 60 --threshold 0.35 --rr 1.5 --sl-mult 2

Usa las mismas piezas que el motor en vivo: los agentes, BossAgent.decide y
risk_manager (stop, tamaño, comisiones y deslizamiento). Sirve para comparar
configuraciones con datos en vez de con intuición.

Lo que NO reproduce: el agente macro (no hay titulares históricos), el libro de
órdenes, los pesos del optimizador (todos valen 1) ni los post-mortem con
Claude (la evolución usa siempre la mutación fija).
"""
import argparse
import asyncio
import json
import math
import os
import tempfile
import urllib.request

if __name__ == "__main__":  # la simulación nunca toca la base de datos del motor
    os.environ.update(DB_PATH=os.path.join(tempfile.mkdtemp(prefix="backtest_"), "bt.db"), PAPER_TRADING="true")

import numpy as np
import pandas as pd

import config
import optimizer
from agents.boss_agent import BossAgent
from agents.historian_agent import HistorianAgent
from agents.orb_agent import ORBAgent, genomes
from agents.sentiment_agent import SentimentAgent
from agents.smc_agent import SMCAgent
from agents.wyckoff_agent import WyckoffAgent
from utils import risk_manager as risk

SLOW_S = 900  # los agentes de velas largas se recalculan cada 15 minutos simulados
WARMUP = 520  # velas previas que necesitan los agentes en cada timeframe


def seconds(tf: str) -> int:
    return int(tf[:-1]) * {"m": 60, "h": 3600, "d": 86400}[tf[-1]]


def timeframes() -> list[str]:
    return sorted({config.SMC_TIMEFRAME, config.ATR_TIMEFRAME, config.WYCKOFF_TIMEFRAME, "1h", "4h", "1d"}, key=seconds)


async def download(ex, symbol: str, days: int, cache_dir) -> dict[str, pd.DataFrame]:
    """Velas de todos los timeframes: `days` días más el calentamiento. Se guardan en CSV."""
    out, end = {}, ex.milliseconds()
    cache_dir.mkdir(parents=True, exist_ok=True)
    for tf in timeframes():
        path = cache_dir / f"{config.EXCHANGE_ID}_{symbol.replace('/', '-').replace(':', '-')}_{tf}_{days}d_{pd.Timestamp(end, unit='ms'):%Y%m%d}.csv"
        if path.exists():
            out[tf] = pd.read_csv(path, parse_dates=["ts"])
            continue
        step = seconds(tf) * 1000
        since, rows = end - days * 86_400_000 - WARMUP * step, []
        while since < end:
            batch = await ex.fetch_ohlcv(symbol, tf, since=since, limit=1000)
            if not batch or batch[-1][0] < since:
                break
            rows += batch
            since = batch[-1][0] + step
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"]).drop_duplicates("ts")
        df["ts"] = pd.to_datetime(df.ts, unit="ms", utc=True)
        df.sort_values("ts").to_csv(path, index=False)
        out[tf] = df.sort_values("ts").reset_index(drop=True)
    return out


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    return df.set_index("ts").resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna().reset_index()


def yahoo_frame(chart: dict) -> pd.DataFrame:
    r = chart["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    df = pd.DataFrame({"ts": pd.to_datetime(r["timestamp"], unit="s", utc=True),
                       **{k: q[k] for k in ("open", "high", "low", "close", "volume")}}).dropna()
    return df[df.volume > 0].drop_duplicates("ts").sort_values("ts").reset_index(drop=True)


def yahoo(symbol: str, cache_dir) -> dict[str, pd.DataFrame]:
    """Velas de acciones de Yahoo Finance, gratis y sin clave (EXCHANGE_ID=yahoo): 2 años en 1h y
    5 en 1d. Con SMC_TIMEFRAME=5m añade las de 5m, de las que Yahoo solo da los últimos 60 días."""
    def get(interval: str, span: str) -> pd.DataFrame:
        path = cache_dir / f"yahoo_{symbol}_{interval}_{pd.Timestamp.now('UTC'):%Y%m%d}.json"
        if not path.exists():
            req = urllib.request.Request(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval={interval}&range={span}",
                headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                path.write_bytes(r.read())
        return yahoo_frame(json.loads(path.read_text()))

    cache_dir.mkdir(parents=True, exist_ok=True)
    h1 = get("1h", "730d")
    out = {"1h": h1, "4h": resample(h1, "4h"), "1d": get("1d", "5y")}
    if config.SMC_TIMEFRAME != "1h":
        m5 = get("5m", "60d")
        out |= {"5m": m5, "15m": resample(m5, "15min")}
    return out


class HistoryMarket:
    """Sirve velas como el mercado en vivo, pero solo las ya cerradas en `now`."""

    def __init__(self, data: dict[str, dict[str, pd.DataFrame]]):
        self.data, self.now = data, None
        self._ts = {s: {tf: df.ts.to_numpy("datetime64[ns]") for tf, df in tfs.items()} for s, tfs in data.items()}

    async def ohlcv(self, symbol: str, tf: str, limit: int = 300) -> pd.DataFrame:
        # Una vela abierta en ts cierra en ts + tf: solo cuenta si ts + tf <= now.
        cut = np.datetime64(self.now.tz_localize(None), "ns") - np.timedelta64(seconds(tf), "s")
        end = int(np.searchsorted(self._ts[symbol][tf], cut, side="right"))
        return self.data[symbol][tf].iloc[max(0, end - limit):end]


STOP_TFS = ("5m", "15m", "1h", "4h")


def _gross(trades: list[dict]) -> float:
    """Resultado antes de comisiones y deslizamiento: lo que aciertan las señales."""
    return sum(t["pnl"] + t["fees"] + t["slippage"] for t in trades)


def successor(bot: dict, mine: list[dict], history: list[dict], tfs: set, when) -> dict:
    """El bot que sustituye a uno eliminado. Hereda todo lo aprendido (pesos de los agentes con el
    historial completo, vetos y ajustes) y corrige UNA causa de la muerte del anterior.
    ponytail: diagnóstico con cinco reglas fijas, sin LLM. Techo: solo corrige costes, exceso de
    operaciones, un par activo/lado perdedor y tamaño. Mejora: post-mortem con Claude, como el de los agentes."""
    new = bot | {"gen": bot["gen"] + 1, "born": when, "blocked": set(bot["blocked"]),
                 "weights": optimizer.new_weights(optimizer.agent_stats(history), bot["weights"])}
    costs, risked = sum(t["fees"] + t["slippage"] for t in mine), sum(t["risk"] for t in mine)
    at = STOP_TFS.index(config.ATR_TIMEFRAME) if config.ATR_TIMEFRAME in STOP_TFS else len(STOP_TFS)
    wider = [tf for tf in STOP_TFS[at + 1:] if tf in tfs]
    buckets: dict = {}
    for t in mine:
        buckets.setdefault((t["symbol"], t["side"]), []).append(t)
    loss, worst = min(((_gross(ts), k) for k, ts in buckets.items() if len(ts) >= 5), default=(0, None))
    if risked and costs >= 0.25 * risked and wider:
        config.ATR_TIMEFRAME = wider[0]
        new["lesson"] = f"los costes eran el {costs / risked:.0%} de lo arriesgado: stop más ancho (ATR de {wider[0]})"
    elif _gross(mine) > 0 and config.DECISION_THRESHOLD < 0.75:
        config.DECISION_THRESHOLD = round(min(0.75, config.DECISION_THRESHOLD + 0.1), 2)
        new["lesson"] = f"acertaba antes de costes pero operaba de más: umbral a {config.DECISION_THRESHOLD}"
    elif loss < 0 and len(new["blocked"]) < 2 * len(config.SYMBOLS) - 1:
        new["blocked"].add(worst)
        new["lesson"] = f"deja de operar {worst[1]} en {worst[0]}: perdió {-loss:,.0f} en {len(buckets[worst])} operaciones"
    elif config.DECISION_THRESHOLD < 0.75:
        config.DECISION_THRESHOLD = round(min(0.75, config.DECISION_THRESHOLD + 0.05), 2)
        new["lesson"] = f"señales sin acierto claro: exige más acuerdo, umbral a {config.DECISION_THRESHOLD}"
    else:
        config.RISK_PER_TRADE_PCT = max(0.1, round(config.RISK_PER_TRADE_PCT * 0.75, 3))
        new["lesson"] = f"sin más filtros que probar: riesgo por operación al {config.RISK_PER_TRADE_PCT}%"
    return new


def _generation(bot: dict, mine: list[dict], when, dead: bool) -> dict:
    return {"gen": bot["gen"], "born": bot["born"], "end": when, "dead": dead, "n": len(mine),
            "net": sum(t["pnl"] for t in mine), "gross": _gross(mine), "lesson": bot["lesson"],
            "threshold": config.DECISION_THRESHOLD, "atr_tf": config.ATR_TIMEFRAME,
            "risk_pct": config.RISK_PER_TRADE_PCT, "blocked": sorted(bot["blocked"])}


async def run(data: dict, days: float, evolution: bool = False, quiet: bool = True,
              lineage: dict | None = None, genes: dict | None = None) -> dict:
    """lineage = {"max_loss": USD, "flat": bool, "learn": bool, "blocked": [(activo, lado)]} activa las reglas
    de cuenta fondeada: el bot muere al caer `max_loss` bajo su mejor cierre diario (el suelo deja de subir
    al llegar al capital inicial) y nace un sucesor con capital nuevo. `flat` cierra todo al acabar la sesión."""
    saved = config.DECISION_THRESHOLD, config.ATR_TIMEFRAME, config.RISK_PER_TRADE_PCT, config.REWARD_RISK
    try:
        if genes and "rr" in genes:  # con STRATEGY=orb, `genes` es el genoma del agente
            config.REWARD_RISK = genes["rr"]
        return await _run(data, days, evolution, quiet, lineage or {}, genes)
    finally:  # el sucesor y el genoma ajustan la configuración global: no debe salir de la simulación
        config.DECISION_THRESHOLD, config.ATR_TIMEFRAME, config.RISK_PER_TRADE_PCT, config.REWARD_RISK = saved


async def _run(data: dict, days: float, evolution: bool, quiet: bool, fl: dict, genes: dict | None = None) -> dict:
    market = HistoryMarket(data)
    if config.STRATEGY == "orb":
        agents = [ORBAgent(genes)]
    else:
        agents = [HistorianAgent(), SMCAgent(), WyckoffAgent(), SentimentAgent()]
    step = seconds(config.SMC_TIMEFRAME)
    slow_every = max(1, SLOW_S // step)
    fast = lambda a: a.name == "smc" or getattr(a, "every_bar", False)
    max_age = {a.name: 2.5 * (step if fast(a) else slow_every * step) for a in agents}
    evo = None
    if evolution:
        from agents.evolution_manager import EvolutionManager
        config.USE_CLAUDE = False  # un backtest no gasta tokens: mutación fija
        evo = EvolutionManager()

    end = min(tfs[config.SMC_TIMEFRAME].ts.iloc[-1] for tfs in data.values()) + pd.Timedelta(seconds=step)
    # El reloj son los cierres de vela que existen: un mercado con sesiones (acciones) no avanza de noche.
    closes = pd.DatetimeIndex(sorted(set().union(*(tfs[config.SMC_TIMEFRAME].ts for tfs in data.values())))) \
        + pd.Timedelta(seconds=step)
    clock = closes[(closes >= end - pd.Timedelta(days=days)) & (closes <= end)]
    secs = clock.values.astype("datetime64[s]").astype("int64")
    session_end = np.r_[np.diff(secs) > step, True]  # tras esta vela el mercado cierra
    no_entry = np.array([session_end[i:i + max(1, 1800 // step)].any() for i in range(len(clock))]) & bool(fl.get("flat"))
    bot = {"gen": 1, "born": clock[0], "weights": {}, "blocked": set(map(tuple, fl.get("blocked", ()))),
           "lesson": "configuración inicial"}
    gens, base, peak_eod, first_trade, last_total = [], 0.0, config.CAPITAL, 0, config.CAPITAL
    equity, open_, closed, last_close, signals, curve = config.CAPITAL, {}, [], {}, {}, []
    day = day_start = halted = None
    stopped = False

    async def close(t: dict, ref: float, reason: str, market_order: bool = True) -> None:
        nonlocal equity
        long = t["side"] == "LONG"
        px = risk.slip(ref, buying=not long) if market_order else ref
        fees = t["fees"] + risk.fee(px, t["qty"])
        pnl = (px - t["entry"]) * t["qty"] * (1 if long else -1) - fees
        equity += pnl
        del open_[t["symbol"]]
        last_close[t["symbol"]] = market.now.timestamp()
        c = t | {"id": len(closed) + 1, "exit": px, "pnl": pnl, "fees": fees, "exit_reason": reason,
                 "closed": market.now, "gen": bot["gen"], "risk": abs(t["entry"] - t["sl"]) * t["qty"],
                 "slippage": t["slippage"] + abs(px - ref) * t["qty"]}
        closed.append(c)
        if evo:
            evo.on_trade_closed(c)
            for key in [k for k in signals if not evo.alive(k[0])]:
                del signals[key]  # un muerto no vota hasta que su sucesor emita señal
            await evo.drain()

    for i, now in enumerate(clock):
        market.now, ts = now, now.timestamp()
        bars = {s: (await market.ohlcv(s, config.SMC_TIMEFRAME, 1)).iloc[-1] for s in config.SYMBOLS}

        # 1) Salidas, con el rango de la vela que acaba de cerrar. Si toca ambos niveles, cuenta el stop.
        for t in list(open_.values()):
            b, long = bars[t["symbol"]], t["side"] == "LONG"
            if t["opened_i"] == i:
                continue  # entró al cierre de esta misma vela
            if (b.low <= t["sl"]) if long else (b.high >= t["sl"]):
                await close(t, t["sl"], "SL")
            elif (b.high >= t["tp"]) if long else (b.low <= t["tp"]):
                await close(t, t["tp"], "TP", market_order=False)
        if fl.get("flat") and session_end[i]:  # cuenta fondeada de acciones: nada pasa la noche abierto
            for t in list(open_.values()):
                await close(t, bars[t["symbol"]].close, "SESION")

        # 2) Límites de pérdida, con el flotante incluido.
        total = equity + sum((bars[s].close - t["entry"]) * t["qty"] * (1 if t["side"] == "LONG" else -1)
                             for s, t in open_.items())
        if now.date() != day:
            peak_eod = max(peak_eod, last_total)  # el suelo sigue al mejor saldo de cierre diario
            day, day_start = now.date(), total
        last_total = total
        if fl and total <= min(peak_eod - fl["max_loss"], config.CAPITAL):  # cuenta perdida: el bot muere
            for t in list(open_.values()):
                await close(t, bars[t["symbol"]].close, "MUERTE")
            mine = closed[first_trade:]
            gens.append(_generation(bot, mine, now, dead=True))
            bot = successor(bot, mine, closed, set(next(iter(data.values()))), now) if fl.get("learn", True) \
                else bot | {"gen": bot["gen"] + 1, "born": now}
            base += equity - config.CAPITAL
            equity = peak_eod = day_start = total = last_total = config.CAPITAL  # el sucesor estrena cuenta
            first_trade, halted = len(closed), None
        breach = None if stopped else risk.check_limits(total, day_start, config.CAPITAL)
        if breach:
            for t in list(open_.values()):
                await close(t, bars[t["symbol"]].close, "LIMITE")
            stopped, halted = stopped or breach[0] == "total", day
        curve.append(base + total)  # con generaciones: la cuenta acumulada de todo el linaje
        if stopped:
            continue

        # 3) Señales y entradas.
        cands = []
        for symbol in config.SYMBOLS:
            for a in agents:
                if not fast(a) and i % slow_every:
                    continue
                if evo and not evo.alive(a.name):
                    continue
                try:
                    sig = (await a.run(symbol, market)).model_copy(update={"ts": ts})
                except Exception:
                    continue
                signals[a.name, symbol] = evo.apply(sig) if evo else sig
            mine = [signals[a.name, symbol] for a in agents if (a.name, symbol) in signals]
            if symbol in open_ or halted == day or ts - last_close.get(symbol, 0) < config.COOLDOWN_S or no_entry[i] \
                    or all(s.direction == "NEUTRAL" for s in mine):
                continue
            d = BossAgent.decide(symbol, mine, bot["weights"], max_age, now=ts)
            if d.action != "NEUTRAL" and (symbol, d.action) not in bot["blocked"]:
                cands.append(d)
        # Con los huecos limitados entra primero la señal más fuerte, no el primer activo de la lista.
        for d in sorted(cands, key=lambda d: -abs(d.score)):
            if len(open_) >= config.MAX_OPEN_POSITIONS:
                break
            symbol = d.symbol
            price = float(bars[symbol].close)
            plan = risk.build_plan(d.action, price, risk.atr(await market.ohlcv(symbol, config.ATR_TIMEFRAME, 100)),
                                   equity, BossAgent._structure(d, price))
            if plan is None:
                continue
            entry = risk.slip(price, buying=d.action == "LONG")
            open_[symbol] = {"symbol": symbol, "side": d.action, "qty": plan["qty"], "entry": entry, "sl": plan["sl"],
                             "tp": plan["tp"], "fees": risk.fee(entry, plan["qty"]), "opened_i": i, "opened": now,
                             "slippage": abs(entry - price) * plan["qty"], "votes": d.votes, "equity_at_entry": equity}
        if not quiet and i % max(1, len(clock) // 10) == 0:
            print(f"  {100 * i // len(clock):>3}% · {now:%Y-%m-%d %H:%M} · capital {total:,.2f} · {len(closed)} operaciones")

    lin = None
    if fl:
        gens.append(_generation(bot, closed[first_trade:], clock[-1], dead=False))
        week = pd.Timedelta(days=7)
        weekly = np.zeros(max(1, int(np.ceil((clock[-1] - clock[0]) / week))))
        for t in closed:
            weekly[min(len(weekly) - 1, int((t["closed"] - clock[0]) / week))] += t["pnl"]
        lin = {"generations": gens, "weekly": weekly.tolist(), "net": sum(t["pnl"] for t in closed)}
    peak = np.maximum.accumulate(curve)
    return {
        "lineage": lin,
        "metrics": optimizer.metrics(closed), "equity": equity, "open_at_end": len(open_), "trades": closed,
        "max_drawdown_pct": float(((np.array(curve) - peak) / peak).min() * 100),
        "costs": sum(t["fees"] + t["slippage"] for t in closed),
        "exits": {r: n for r in ("SL", "TP", "LIMITE", "SESION", "MUERTE")
                  if (n := sum(t["exit_reason"] == r for t in closed)) or r in ("SL", "TP", "LIMITE")},
        "agents": {a: {s: {"votos": n, "precision": ok / n} for s, (n, ok) in by.items()}
                   for a, by in optimizer.agent_stats(closed).items()},
        "versions": {a: g["name"] for a, g in evo.active.items()} if evo else None,
    }


MIN_TRAIN_TRADES = 30  # con menos operaciones en el entrenamiento, el resultado es ruido y no se elige


def trim(data: dict, end) -> dict:
    return {s: {tf: df[df.ts < end].reset_index(drop=True) for tf, df in tfs.items()} for s, tfs in data.items()}


async def fold(data: dict, test_end, train_days: float, test_days: float, rules: dict,
               population: list[dict] | None = None) -> dict:
    """Un pliegue de la validación. ENTRENAMIENTO: cada genoma de la población opera el periodo anterior
    al examen con las reglas de cuenta fondeada; los que pierden la cuenta quedan eliminados y entre los
    supervivientes con ganancia se elige el mejor. EXAMEN: ese genoma, congelado, opera el periodo
    siguiente, del que el entrenamiento no ha visto ni una vela. Si nadie sobrevive ganando, no se opera."""
    rules = rules | {"learn": False}
    test_start = test_end - pd.Timedelta(days=test_days)
    train, pop = trim(data, test_start), []
    for g in population or genomes(seconds(config.SMC_TIMEFRAME)):
        r = await run(train, train_days, lineage=rules, genes=g)
        pop.append({"genes": g, "net": r["lineage"]["net"], "n": r["metrics"]["n"],
                    "deaths": len(r["lineage"]["generations"]) - 1})
    alive = [p for p in pop if not p["deaths"] and p["net"] > 0 and p["n"] >= rules.get("min_trades", MIN_TRAIN_TRADES)]
    best = max(alive, key=lambda p: p["net"], default=None)
    out = {"train": (test_start - pd.Timedelta(days=train_days), test_start), "test": (test_start, test_end),
           "population": len(pop), "dead": sum(bool(p["deaths"]) for p in pop), "survivors": len(alive), "best": best,
           "net": 0.0, "n": 0, "deaths": 0, "weekly": [0.0] * math.ceil(test_days / 7), "trades": []}
    if best:
        r = await run(trim(data, test_end), test_days, lineage=rules, genes=best["genes"])
        out |= {"net": r["lineage"]["net"], "n": r["metrics"]["n"], "deaths": len(r["lineage"]["generations"]) - 1,
                "weekly": r["lineage"]["weekly"], "trades": r["trades"]}
    return out


def fold_ends(data: dict, train_days: float, test_days: float) -> list:
    """Finales de los periodos de examen: consecutivos, sin solaparse, hasta la última vela."""
    tf = config.SMC_TIMEFRAME
    first = max(tfs[tf].ts.iloc[0] for tfs in data.values())
    end = min(tfs[tf].ts.iloc[-1] for tfs in data.values()) + pd.Timedelta(seconds=seconds(tf))
    # Margen para que el ATR tenga sus 100 velas antes del primer entrenamiento (en días de calendario de bolsa).
    margin = math.ceil(100 * seconds(config.ATR_TIMEFRAME) / 23400 * 1.4) + 2
    k = int(((end - first).days - train_days - margin) // test_days)
    if k < 1:
        raise SystemExit(f"No hay historia para entrenar {train_days:g} días y examinar {test_days:g}.")
    return [end - pd.Timedelta(days=test_days * i) for i in range(k - 1, -1, -1)]


async def walk_forward(data: dict, train_days: float, test_days: float, rules: dict,
                       population: list[dict] | None = None) -> list[dict]:
    return [await fold(data, e, train_days, test_days, rules, population) for e in fold_ends(data, train_days, test_days)]


def report_wf(folds: list[dict]) -> str:
    gene = lambda g: (f"rango {g['or_min']} min, stop {g['stop']}, vol. relativo ≥{g['rvol']:g}x, {g['sides']}, "
                      f"{'vela de apertura' if g['dir'] == 'candle' else 'ruptura'}, {g['rr']:g}R")
    lines = ["Validación fuera de muestra (walk-forward). Cada examen usa el genoma elegido SOLO con el periodo anterior."]
    for f in folds:
        b = f["best"]
        chosen = f"{gene(b['genes'])} · entrenamiento {b['net']:+,.0f} en {b['n']} ops" if b else "ninguno ganó sin morir: no opera"
        lines.append(f"  examen {f['test'][0]:%d-%m-%y} a {f['test'][1]:%d-%m-%y} · población {f['population']}, eliminados "
                     f"{f['dead']}, aptos {f['survivors']} · {chosen} · EXAMEN {f['net']:+,.0f} en {f['n']} ops"
                     + (f", {f['deaths']} cuentas perdidas" if f["deaths"] else ""))
    w = np.array([x for f in folds for x in f["weekly"]])
    promised = sum(f["best"]["net"] for f in folds if f["best"])
    lines.append(f"Total fuera de muestra {sum(f['net'] for f in folds):+,.0f} en {sum(f['n'] for f in folds)} operaciones y "
                 f"{len(w)} semanas · por semana: media {w.mean():+,.0f}, mediana {np.median(w):+,.0f} · semanas con 500 o más: "
                 f"{(w >= 500).sum()} de {len(w)} · exámenes en positivo: {sum(f['net'] > 0 for f in folds)} de {len(folds)} "
                 f"({sum(f['best'] is None for f in folds)} sin operar) · cuentas perdidas: {sum(f['deaths'] for f in folds)}")
    lines.append(f"Los genomas elegidos sumaban {promised:+,.0f} en sus entrenamientos. La diferencia con el total de los "
                 "exámenes es lo que el entrenamiento memorizó en vez de aprender.")
    return "\n".join(lines)


def report(res: dict, days: float, evolution: bool) -> str:
    m, fmt = res["metrics"], lambda x: "n/d" if x is None else f"{x:.2f}"
    lines = [f"Backtest de {days:g} días · {', '.join(config.SYMBOLS)} · {config.EXCHANGE_ID} · umbral {config.DECISION_THRESHOLD} · "
             f"RR {config.REWARD_RISK} · stop {config.ATR_SL_MULT} ATR · evolución {'sí' if evolution else 'no'}"]
    if not m["n"]:
        return lines[0] + "\nNinguna operación: el umbral de decisión no se alcanzó en el periodo."
    lines += [
        f"Operaciones {m['n']} · win rate {m['win_rate']:.0%} · PnL neto {m['pnl']:+,.2f} {config.QUOTE} "
        f"({m['pnl'] / config.CAPITAL:+.1%}) · profit factor {fmt(m['profit_factor'])} · Sharpe/op {fmt(m['sharpe'])}",
        f"Caída máxima {res['max_drawdown_pct']:.1f}% · costes pagados {res['costs']:,.2f} · capital final "
        f"{res['equity']:,.2f} · salidas: " + ", ".join(f"{k} {v}" for k, v in res["exits"].items())]
    for agent, by in res["agents"].items():
        lines.append(f"  {agent:<10} " + " · ".join(f"{s}: {r['precision']:.0%} en {r['votos']} votos" for s, r in by.items()))
    if res["versions"]:
        lines.append("Generaciones al final: " + ", ".join(res["versions"].values()))
    if res.get("lineage"):
        g, w = res["lineage"]["generations"], np.array(res["lineage"]["weekly"])
        lines += [f"Linaje: {len(g)} bots, {sum(x['dead'] for x in g)} eliminados · resultado neto acumulado "
                  f"{res['lineage']['net']:+,.2f} · por semana: media {w.mean():+,.0f}, mediana {np.median(w):+,.0f}, "
                  f"mejor {w.max():+,.0f}, peor {w.min():+,.0f} · semanas con 500 o más: {(w >= 500).sum()} de {len(w)}"]
        for x in g if len(g) <= 16 else g[:8] + g[-8:]:
            lines.append(f"  bot {x['gen']:>2} · {x['born']:%d-%m-%y} a {x['end']:%d-%m-%y} · {x['n']:>3} ops · neto "
                         f"{x['net']:>+9,.0f} · bruto {x['gross']:>+9,.0f} · {'eliminado' if x['dead'] else 'vivo'} · "
                         f"nació con: {x['lesson']}")
    lines.append(f"Sin agente macro, sin libro de órdenes y con pesos {'heredados entre bots' if res.get('lineage') else 'iguales'}. Un backtest favorable no garantiza nada: "
                 "compara configuraciones entre sí y desconfía de cualquier resultado con pocas operaciones.")
    return "\n".join(lines)


async def main() -> None:
    from utils.exchange import make_exchange
    ap = argparse.ArgumentParser(description="Simula el motor sobre velas históricas.")
    ap.add_argument("--days", type=float, default=30)
    ap.add_argument("--evolution", action="store_true", help="activa la selección natural (mutación fija)")
    ap.add_argument("--threshold", type=float, help="umbral de decisión (DECISION_THRESHOLD)")
    ap.add_argument("--rr", type=float, help="relación objetivo/stop (REWARD_RISK)")
    ap.add_argument("--sl-mult", type=float, help="stop en múltiplos de ATR (ATR_SL_MULT)")
    ap.add_argument("--lineage", type=float, metavar="USD", help="cuenta fondeada: al perder USD bajo su mejor cierre "
                    "diario el bot muere y nace un sucesor que hereda lo aprendido y corrige una causa")
    ap.add_argument("--flat", action="store_true", help="con --lineage: cierra todo al final de cada sesión")
    ap.add_argument("--walk-forward", metavar="ENTRENA,EXAMINA", help="días de entrenamiento y de examen: elige el "
                    "genoma ORB en cada periodo y lo examina en el siguiente (STRATEGY=orb)")
    a = ap.parse_args()
    for name, value in (("DECISION_THRESHOLD", a.threshold), ("REWARD_RISK", a.rr), ("ATR_SL_MULT", a.sl_mult)):
        if value is not None:
            setattr(config, name, value)
    cache_dir = config.ROOT / "data" / "backtest"
    if config.EXCHANGE_ID == "yahoo":
        data = {s: yahoo(s, cache_dir) for s in config.SYMBOLS}
    else:
        ex = make_exchange()
        try:
            data = {s: await download(ex, s, int(a.days), cache_dir) for s in config.SYMBOLS}
        finally:
            await ex.close()
    if a.walk_forward:
        if config.STRATEGY != "orb":
            raise SystemExit("--walk-forward entrena genomas ORB: arranca con STRATEGY=orb")
        train_days, test_days = map(float, a.walk_forward.split(","))
        print(report_wf(await walk_forward(data, train_days, test_days, {"max_loss": a.lineage or 2000, "flat": a.flat})))
        return
    lineage = {"max_loss": a.lineage, "flat": a.flat} if a.lineage else None
    print(report(await run(data, a.days, a.evolution, quiet=False, lineage=lineage), a.days, a.evolution))


if __name__ == "__main__":
    asyncio.run(main())
