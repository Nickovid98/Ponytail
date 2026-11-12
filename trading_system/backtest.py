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
import os
import tempfile

if __name__ == "__main__":  # la simulación nunca toca la base de datos del motor
    os.environ.update(DB_PATH=os.path.join(tempfile.mkdtemp(prefix="backtest_"), "bt.db"), PAPER_TRADING="true")

import numpy as np
import pandas as pd

import config
import optimizer
from agents.boss_agent import BossAgent
from agents.historian_agent import HistorianAgent
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
              lineage: dict | None = None) -> dict:
    """lineage = {"max_loss": USD, "flat": bool, "learn": bool, "blocked": [(activo, lado)]} activa las reglas
    de cuenta fondeada: el bot muere al caer `max_loss` bajo su mejor cierre diario (el suelo deja de subir
    al llegar al capital inicial) y nace un sucesor con capital nuevo. `flat` cierra todo al acabar la sesión."""
    saved = config.DECISION_THRESHOLD, config.ATR_TIMEFRAME, config.RISK_PER_TRADE_PCT
    try:
        return await _run(data, days, evolution, quiet, lineage or {})
    finally:  # el sucesor ajusta la configuración global: no debe salir de la simulación
        config.DECISION_THRESHOLD, config.ATR_TIMEFRAME, config.RISK_PER_TRADE_PCT = saved


async def _run(data: dict, days: float, evolution: bool, quiet: bool, fl: dict) -> dict:
    market = HistoryMarket(data)
    agents = [HistorianAgent(), SMCAgent(), WyckoffAgent(), SentimentAgent()]
    step = seconds(config.SMC_TIMEFRAME)
    slow_every = max(1, SLOW_S // step)
    max_age = {a.name: 2.5 * (step if a.name == "smc" else slow_every * step) for a in agents}
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
        for symbol in config.SYMBOLS:
            for a in agents:
                if a.name != "smc" and i % slow_every:
                    continue
                if evo and not evo.alive(a.name):
                    continue
                try:
                    sig = (await a.run(symbol, market)).model_copy(update={"ts": ts})
                except Exception:
                    continue
                signals[a.name, symbol] = evo.apply(sig) if evo else sig
            if symbol in open_ or halted == day or len(open_) >= config.MAX_OPEN_POSITIONS \
                    or ts - last_close.get(symbol, 0) < config.COOLDOWN_S or no_entry[i]:
                continue
            d = BossAgent.decide(symbol, [s for (_, sym), s in signals.items() if sym == symbol], bot["weights"],
                                 max_age, now=ts)
            if d.action == "NEUTRAL" or (symbol, d.action) in bot["blocked"]:
                continue
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
    a = ap.parse_args()
    for name, value in (("DECISION_THRESHOLD", a.threshold), ("REWARD_RISK", a.rr), ("ATR_SL_MULT", a.sl_mult)):
        if value is not None:
            setattr(config, name, value)
    ex = make_exchange()
    try:
        data = {s: await download(ex, s, int(a.days), config.ROOT / "data" / "backtest") for s in config.SYMBOLS}
    finally:
        await ex.close()
    lineage = {"max_loss": a.lineage, "flat": a.flat} if a.lineage else None
    print(report(await run(data, a.days, a.evolution, quiet=False, lineage=lineage), a.days, a.evolution))


if __name__ == "__main__":
    asyncio.run(main())
