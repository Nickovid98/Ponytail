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


async def run(data: dict, days: float, evolution: bool = False, quiet: bool = True) -> dict:
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
    clock = pd.date_range(end - pd.Timedelta(days=days), end, freq=f"{step}s")
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

        # 2) Límites de pérdida, con el flotante incluido.
        total = equity + sum((bars[s].close - t["entry"]) * t["qty"] * (1 if t["side"] == "LONG" else -1)
                             for s, t in open_.items())
        if now.date() != day:
            day, day_start = now.date(), total
        breach = None if stopped else risk.check_limits(total, day_start, config.CAPITAL)
        if breach:
            for t in list(open_.values()):
                await close(t, bars[t["symbol"]].close, "LIMITE")
            stopped, halted = stopped or breach[0] == "total", day
        curve.append(total)
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
                    or ts - last_close.get(symbol, 0) < config.COOLDOWN_S:
                continue
            d = BossAgent.decide(symbol, [s for (_, sym), s in signals.items() if sym == symbol], {}, max_age, now=ts)
            if d.action == "NEUTRAL":
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

    peak = np.maximum.accumulate(curve)
    return {
        "metrics": optimizer.metrics(closed), "equity": equity, "open_at_end": len(open_), "trades": closed,
        "max_drawdown_pct": float(((np.array(curve) - peak) / peak).min() * 100),
        "costs": sum(t["fees"] + t["slippage"] for t in closed),
        "exits": {r: sum(t["exit_reason"] == r for t in closed) for r in ("SL", "TP", "LIMITE")},
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
    lines.append("Sin agente macro, sin libro de órdenes y con pesos iguales. Un backtest favorable no garantiza nada: "
                 "compara configuraciones entre sí y desconfía de cualquier resultado con pocas operaciones.")
    return "\n".join(lines)


async def main() -> None:
    import ccxt.async_support as ccxt
    ap = argparse.ArgumentParser(description="Simula el motor sobre velas históricas.")
    ap.add_argument("--days", type=float, default=30)
    ap.add_argument("--evolution", action="store_true", help="activa la selección natural (mutación fija)")
    ap.add_argument("--threshold", type=float, help="umbral de decisión (DECISION_THRESHOLD)")
    ap.add_argument("--rr", type=float, help="relación objetivo/stop (REWARD_RISK)")
    ap.add_argument("--sl-mult", type=float, help="stop en múltiplos de ATR (ATR_SL_MULT)")
    a = ap.parse_args()
    for name, value in (("DECISION_THRESHOLD", a.threshold), ("REWARD_RISK", a.rr), ("ATR_SL_MULT", a.sl_mult)):
        if value is not None:
            setattr(config, name, value)
    ex = getattr(ccxt, config.EXCHANGE_ID)({"enableRateLimit": True})
    try:
        data = {s: await download(ex, s, int(a.days), config.ROOT / "data" / "backtest") for s in config.SYMBOLS}
    finally:
        await ex.close()
    print(report(await run(data, a.days, a.evolution, quiet=False), a.days, a.evolution))


if __name__ == "__main__":
    asyncio.run(main())
